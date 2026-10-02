"""A bank of notable quotes: lines people have already singled out as worth quoting,
each tied to the exact moment in an episode.

The quotes come from curated collections - Wikiquote's per-season pages, or a plain
text file of quotes - which give wording and usually the speaker, but no timings.
Each is located in the episode transcripts the fan wiki gave us (wiki_entries, which
do have times), so a quote from the bank can go straight into a video.
"""

import collections
import difflib
import json
import math
import re
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import DATA_DIR
from app.db.models import Source, WikiEntry
from app.services.wiki_transcripts import USER_AGENT, tokens

CACHE_DIR = DATA_DIR / "quote_sources"
# a collected quote counts as found when this share of its words is in the matched line(s)
MIN_MATCH = 0.7
# a collected exchange longer than this is cut into several clips
MAX_CLIP_SECONDS = 45.0
MIN_WORDS = 4  # shorter "quotes" ("Yip yip!") match everywhere and say nothing


@dataclass
class Collected:
    """A quote as a collection gives it: one or more consecutive lines."""

    lines: list[tuple[str, str]]  # (speaker, text)
    origin: str  # where it was collected from, e.g. "Wikiquote: Avatar: The Last Airbender (season 1) / The Storm"


@dataclass
class Located:
    lines: list[tuple[str, str]]  # the collected lines this clip covers
    origin: str
    source_id: int
    start: float
    end: float
    score: float


def _clean(text: str) -> str:
    text = text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    text = re.sub(r"\{\{[^{}]*\}\}", "", text)
    text = re.sub(r"\[\[(?:[^\]|]*\|)?([^\]]*)\]\]", r"\1", text)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\s+", " ", text.replace("'''", "").replace("''", "")).strip()


def wikiquote_page(api: str, title: str) -> str:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / (re.sub(r"[^A-Za-z0-9]+", "_", title) + ".txt")
    if path.exists():
        return path.read_text(encoding="utf-8")
    url = api + "?" + urllib.parse.urlencode({"action": "parse", "page": title, "prop": "wikitext", "format": "json"})
    data = json.load(urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": USER_AGENT}), timeout=40))
    text = data.get("parse", {}).get("wikitext", {}).get("*", "")
    path.write_text(text, encoding="utf-8")
    time.sleep(0.3)
    return text


def parse_wikiquote(wikitext: str, page: str) -> list[Collected]:
    """Wikiquote lists quotes under a heading per episode. A line reads
    ":'''Speaker''': text" (or "*'''Speaker''': text"); an exchange is several such
    lines in a row, and a blank line or a rule ends it."""
    quotes: list[Collected] = []
    section = ""
    block: list[tuple[str, str]] = []

    def flush() -> None:
        nonlocal block
        if block:
            quotes.append(Collected(lines=block, origin=f"Wikiquote: {page}" + (f" / {section}" if section else "")))
        block = []

    for raw in wikitext.split("\n"):
        line = raw.strip()
        heading = re.match(r"^=+\s*(.*?)\s*=+$", line)
        if heading:
            flush()
            section = _clean(heading.group(1))
            continue
        spoken = re.match(r"^[:*]+\s*'''(.+?)'''\s*:?\s*(.*)$", line)
        if spoken:
            speaker = _clean(spoken.group(1)).rstrip(":").strip()
            # stage notes - [sighs], (via radio) - are not part of the wording
            text = re.sub(r"\s+", " ", re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", _clean(spoken.group(2)))).strip()
            if text:
                block.append((speaker, text))
            continue
        flush()
    flush()
    return [q for q in quotes if sum(len(tokens(text)) for _, text in q.lines) >= MIN_WORDS]


def parse_plain(text: str, origin: str) -> list[Collected]:
    """One quote per line, optionally "Speaker: text" or '"text" - Speaker'."""
    quotes = []
    for raw in text.splitlines():
        line = raw.strip().strip("-*• ").strip()
        if not line or line.startswith("#"):
            continue
        speaker = ""
        attributed = re.match(r'^["“](.+?)["”]\s*[-–—~]+\s*(.+)$', line)
        named = re.match(r"^([A-Z][\w .']{1,30}):\s+(.+)$", line)
        if attributed:
            line, speaker = attributed.group(1), attributed.group(2)
        elif named:
            speaker, line = named.group(1), named.group(2)
        line = _clean(line).strip('"')
        if len(tokens(line)) >= MIN_WORDS:
            quotes.append(Collected(lines=[(speaker.strip(), line)], origin=origin))
    return quotes


class Locator:
    """Finds collected quotes in the wiki transcript lines of the given shows."""

    def __init__(self, db: Session, shows: list[str]):
        self.sources = list(db.execute(select(Source).where(Source.show_title.in_(shows))).scalars())
        rows = db.execute(
            select(WikiEntry)
            .join(Source, Source.id == WikiEntry.source_id)
            .where(Source.show_title.in_(shows), WikiEntry.kind == "line", WikiEntry.start_time.isnot(None))
            .order_by(WikiEntry.source_id, WikiEntry.position)
        ).scalars()
        self.lines = list(rows)
        self.tokens = [tokens(line.text) for line in self.lines]
        self.by_source: dict[int, list[int]] = collections.defaultdict(list)
        self.by_word: dict[str, list[int]] = collections.defaultdict(list)
        for index, (line, words) in enumerate(zip(self.lines, self.tokens)):
            self.by_source[line.source_id].append(index)
            for word in set(words):
                self.by_word[word].append(index)
        self.idf = {w: math.log(len(self.lines) / len(indexes)) for w, indexes in self.by_word.items()}

    def episode(self, origin: str) -> int | None:
        """The episode a collection files the quote under: "... / The Storm [1.12]"
        on a page named after the show."""
        page, _, section = origin.partition(" / ")
        shows = [s for s in {src.show_title for src in self.sources} if s and s.lower() in page.lower()]
        show = max(shows, key=len) if shows else None
        title = tokens(re.sub(r"\[[^\]]*\]", "", section))
        if title:
            for source in self.sources:
                if show and source.show_title != show:
                    continue
                if source.wiki_page and tokens(source.wiki_page.split(":", 1)[-1]) == title:
                    return source.id
        number = re.search(r"\[(\d+)\.(\d+)\]", section)
        if number and show:
            for source in self.sources:
                if source.show_title == show and source.book == int(number.group(1)) and source.episode_number == int(number.group(2)):
                    return source.id
        return None

    def _matches(self, text: str, source_id: int | None) -> list[tuple[int, float]]:
        """Transcript lines containing the text: (index, share of the text's words
        found there), best first."""
        words = tokens(text)
        if not words:
            return []
        if source_id is not None:
            shared = set(words)
            candidates = [i for i in self.by_source.get(source_id, []) if shared & set(self.tokens[i])]
        else:
            # across all episodes, look only at lines sharing the text's rarest words
            rare = sorted(set(words), key=lambda w: self.idf.get(w, 99), reverse=True)[:4]
            counted = collections.Counter(i for w in rare for i in self.by_word.get(w, []))
            candidates = [i for i, _ in counted.most_common(40)]
        found = []
        for index in candidates:
            matcher = difflib.SequenceMatcher(a=words, b=self.tokens[index], autojunk=False)
            share = sum(block.size for block in matcher.get_matching_blocks()) / len(words)
            if share >= MIN_MATCH:
                # among equally complete matches, the line that is mostly this text wins
                found.append((index, share, matcher.ratio()))
        found.sort(key=lambda f: (f[1], f[2]), reverse=True)
        return [(index, share) for index, share, _ in found]

    def locate(self, quote: Collected) -> list[Located]:
        """Where the quote is in the episodes: one clip, or several when a long
        exchange has to be cut. Empty when it could not be found."""
        source_id = self.episode(quote.origin)
        matches = [self._matches(text, source_id) for _, text in quote.lines]
        if source_id is None:
            # no episode given: the one most of the quote's lines point to
            votes = collections.Counter(self.lines[m[0][0]].source_id for m in matches if m)
            if not votes:
                return []
            source_id = votes.most_common(1)[0][0]
            matches = [[x for x in m if self.lines[x[0]].source_id == source_id] for m in matches]

        # lines that are long enough or match one place only fix where the quote is;
        # ambiguous short lines ("I'm sorry.") take the match nearest to them
        anchors = [
            options[0][0]
            for (_, text), options in zip(quote.lines, matches)
            if options and (len(options) == 1 or len(tokens(text)) >= MIN_WORDS)
        ]
        if not anchors:
            return []
        centre = sorted(anchors)[len(anchors) // 2]
        placed: list[tuple[int, int, float]] = []  # (line number in the quote, transcript index, share)
        for number, options in enumerate(matches):
            if not options:
                continue
            best_share = options[0][1]
            index, share = min((o for o in options if o[1] >= best_share - 0.05), key=lambda o: abs(o[0] - centre))
            placed.append((number, index, share))

        # keep the longest run that goes forward through the episode
        runs: list[list[tuple[int, int, float]]] = []
        for item in placed:
            before = [run for run in runs if run[-1][1] <= item[1]]
            runs.append(max(before, key=len, default=[]) + [item])
        best_run = max(runs, key=len, default=[])
        if len(best_run) * 2 < len(quote.lines):
            return []

        # cut where the clip would get too long
        pieces: list[list[tuple[int, int, float]]] = [[]]
        for item in best_run:
            piece = pieces[-1]
            if piece and self.lines[item[1]].end_time - self.lines[piece[0][1]].start_time > MAX_CLIP_SECONDS:
                pieces.append([])
            pieces[-1].append(item)

        located = []
        for piece in pieces:
            covered = [quote.lines[number] for number, _, _ in piece]
            if sum(len(tokens(text)) for _, text in covered) < MIN_WORDS:
                continue
            start = min(self.lines[index].start_time for _, index, _ in piece)
            end = max(self.lines[index].end_time for _, index, _ in piece)
            if end - start > MAX_CLIP_SECONDS * 2:
                continue
            located.append(
                Located(
                    lines=covered, origin=quote.origin, source_id=source_id, start=start, end=end,
                    score=round(min(share for _, _, share in piece), 2),
                )
            )
        return located
