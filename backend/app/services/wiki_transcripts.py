"""Fan-wiki episode transcripts: who says each line, the exact wording, and written
descriptions of what happens on screen between the lines.

The wiki has no timings and our Whisper transcripts have no speakers, but the words
are nearly the same, so the two are aligned word by word: a wiki line takes its time
from the Whisper words it matched, and a Whisper line takes its speaker from the
wiki line its words belong to. A description sits between two spoken lines and gets
the gap between them as its time range.

Pages are fetched through the MediaWiki API of the universe's wiki (plain page
requests are refused) and cached under <root>/index/cache/wiki_transcripts/.
"""

import collections
import difflib
import json
import math
import re
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

from app.config import CACHE_DIR as _CACHE_ROOT

CACHE_DIR = _CACHE_ROOT / "wiki_transcripts"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
MIN_LINES_PER_PAGE = 20  # fewer spoken lines than this is not an episode transcript
# a wiki line counts as found when this share of its words matched
LINE_MATCH_SHARE = 0.6
# a Whisper line takes a speaker when this share of its words matched
SPEAKER_MATCH_SHARE = 0.5


@dataclass
class Entry:
    kind: str  # "line" | "desc"
    text: str
    speaker: str = ""
    start: float | None = None
    end: float | None = None


@dataclass
class Alignment:
    page: str
    # how clearly this page won over the next best one (1.0 = no better than the runner-up)
    page_margin: float
    entries: list[Entry]
    # per Whisper line (same order as given): speaker or None, and the share of its words found in the wiki
    speakers: list[str | None] = field(default_factory=list)
    found: list[float] = field(default_factory=list)
    wiki_words_found: float = 0.0


def _api(base: str, **params) -> dict:
    url = base + "?" + urllib.parse.urlencode({**params, "format": "json"})
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    return json.load(urllib.request.urlopen(request, timeout=40))


def _cache_dir(base: str):
    path = CACHE_DIR / re.sub(r"[^a-z0-9]+", "_", urllib.parse.urlparse(base).netloc.lower())
    path.mkdir(parents=True, exist_ok=True)
    return path


def transcript_pages(base: str) -> list[str]:
    """Titles of every page in the wiki's Transcript namespace."""
    index = _cache_dir(base) / "_pages.json"
    if index.exists():
        return json.loads(index.read_text(encoding="utf-8"))
    namespaces = _api(base, action="query", meta="siteinfo", siprop="namespaces")["query"]["namespaces"]
    namespace = next(int(k) for k, v in namespaces.items() if v.get("*") == "Transcript")
    titles, cont = [], {}
    while True:
        data = _api(base, action="query", list="allpages", apnamespace=namespace, aplimit=500, **cont)
        titles += [page["title"] for page in data["query"]["allpages"]]
        if "continue" not in data:
            break
        cont = data["continue"]
        time.sleep(0.3)
    index.write_text(json.dumps(titles, ensure_ascii=False), encoding="utf-8")
    return titles


def page_wikitext(base: str, title: str) -> str:
    path = _cache_dir(base) / (re.sub(r"[^A-Za-z0-9]+", "_", title) + ".txt")
    if path.exists():
        return path.read_text(encoding="utf-8")
    text = _api(base, action="parse", page=title, prop="wikitext").get("parse", {}).get("wikitext", {}).get("*", "")
    path.write_text(text, encoding="utf-8")
    time.sleep(0.25)  # be polite to the wiki
    return text


def _clean(text: str) -> str:
    text = text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    text = re.sub(r"\{\{[^{}]*\}\}", "", text)
    text = re.sub(r"\[\[(?:[^\]|]*\|)?([^\]]*)\]\]", r"\1", text)  # [[link|shown]] -> shown
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\s+", " ", text.replace("'''", "").replace("''", "")).strip()


def parse_page(wikitext: str) -> list[Entry]:
    """The rows of the page's tables, in order: "!Speaker" + "|text" is a spoken
    line, an empty first cell + "|text" is a description of what happens."""
    entries: list[Entry] = []
    for row in re.split(r"\n\|-|\n\|\}|\{\|[^\n]*\n", wikitext):
        cells = [cell for cell in row.strip().split("\n") if cell.strip()]
        if not cells:
            continue
        if cells[0].startswith("!"):
            body = _clean(" ".join(cell.lstrip("|") for cell in cells[1:]))
            # stage notes inside a line - [Narrating.], (Angrily) - are not spoken
            spoken = re.sub(r"\s+", " ", re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", body)).strip()
            if spoken:
                entries.append(Entry(kind="line", speaker=_clean(cells[0][1:]), text=spoken))
        elif cells[0].startswith("|"):
            body = _clean(" ".join(cell.lstrip("|") for cell in cells))
            if len(body) > 20:
                entries.append(Entry(kind="desc", text=body))
    return entries


def tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower().replace("’", "'"))


class Wiki:
    """All of a wiki's episode transcripts, parsed, with what is needed to find the
    page of an episode from its words."""

    def __init__(self, base: str, report=None):
        self.base = base
        self.pages: dict[str, list[Entry]] = {}
        titles = transcript_pages(base)
        for n, title in enumerate(titles):
            entries = parse_page(page_wikitext(base, title))
            if sum(1 for e in entries if e.kind == "line") >= MIN_LINES_PER_PAGE:
                self.pages[title] = entries
            if report and (n + 1) % 50 == 0:
                report(f"reading wiki pages {n + 1}/{len(titles)}")
        self._words = {
            title: collections.Counter(w for e in entries if e.kind == "line" for w in tokens(e.text))
            for title, entries in self.pages.items()
        }
        document_frequency = collections.Counter(w for words in self._words.values() for w in words)
        # rare words - names, places - are what tell one episode from another
        self._idf = {w: math.log(len(self._words) / n) for w, n in document_frequency.items()}
        self._norms = {
            title: math.sqrt(sum((self._idf[w] * n) ** 2 for w, n in words.items())) or 1.0
            for title, words in self._words.items()
        }

    def best_page(self, words: collections.Counter) -> tuple[str, float]:
        """(title of the page whose wording is closest, how many times better it scores than the next)."""
        norm = math.sqrt(sum((self._idf.get(w, 0) * n) ** 2 for w, n in words.items())) or 1.0
        scores = []
        for title, page_words in self._words.items():
            dot = sum(self._idf.get(w, 0) ** 2 * n * page_words[w] for w, n in words.items() if w in page_words)
            scores.append((dot / (norm * self._norms[title]), title))
        scores.sort(reverse=True)
        return scores[0][1], scores[0][0] / max(scores[1][0], 1e-9)

    def align(self, lines: list[tuple[float, float, str]]) -> Alignment | None:
        """Aligns an episode's Whisper lines [(start, end, text)] with its wiki page."""
        our_words, our_line, our_time = [], [], []
        for index, (start, end, text) in enumerate(lines):
            words = tokens(text)
            # Whisper sometimes glues a stretch of silence onto a line; a line can't last
            # much longer than its words take to say
            end = min(end, start + max(1.0, 0.45 * len(words) + 1.0))
            for k, word in enumerate(words):
                our_words.append(word)
                our_line.append(index)
                our_time.append(start + (end - start) * (k + 0.5) / len(words))
        if not our_words or not self.pages:
            return None

        page, margin = self.best_page(collections.Counter(our_words))
        entries = [Entry(e.kind, e.text, e.speaker) for e in self.pages[page]]
        wiki_words, owner = [], []
        for i, entry in enumerate(entries):
            if entry.kind == "line":
                for word in tokens(entry.text):
                    wiki_words.append(word)
                    owner.append(i)

        pair: dict[int, int] = {}  # wiki word index -> our word index
        matcher = difflib.SequenceMatcher(a=wiki_words, b=our_words, autojunk=False)
        for block in matcher.get_matching_blocks():
            for k in range(block.size):
                pair[block.a + k] = block.b + k

        # wiki lines: time span of the words that matched
        per_entry: dict[int, list] = {}
        for wi, entry_index in enumerate(owner):
            stats = per_entry.setdefault(entry_index, [0, []])
            stats[0] += 1
            if wi in pair:
                stats[1].append(our_time[pair[wi]])
        for entry_index, (count, times) in per_entry.items():
            if times and len(times) / count >= LINE_MATCH_SHARE:
                entries[entry_index].start, entries[entry_index].end = round(min(times), 2), round(max(times), 2)

        # descriptions: the gap between the placed lines before and after
        for i, entry in enumerate(entries):
            if entry.kind != "desc":
                continue
            before = next((entries[j].end for j in range(i - 1, -1, -1) if entries[j].kind == "line" and entries[j].end is not None), 0.0)
            after = next((entries[j].start for j in range(i + 1, len(entries)) if entries[j].kind == "line" and entries[j].start is not None), None)
            if after is not None and after >= before:
                entry.start, entry.end = before, after

        # our lines: speaker by majority of their matched words
        votes: dict[int, list[str]] = collections.defaultdict(list)
        for wi, oi in pair.items():
            votes[our_line[oi]].append(entries[owner[wi]].speaker)
        speakers: list[str | None] = []
        found: list[float] = []
        for index, (_, _, text) in enumerate(lines):
            count = len(tokens(text))
            matched = votes.get(index, [])
            share = len(matched) / count if count else 0.0
            found.append(round(share, 2))
            speakers.append(max(set(matched), key=matched.count) if matched and share >= SPEAKER_MATCH_SHARE else None)

        return Alignment(
            page=page, page_margin=round(margin, 2), entries=entries, speakers=speakers, found=found,
            wiki_words_found=round(len(pair) / len(wiki_words), 3) if wiki_words else 0.0,
        )
