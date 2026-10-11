"""Finds lines of dialogue from the show that would strengthen the narration, and
cuts them out precisely.

A quote is its own stretch of the final video: the clip plays with its original
sound between two pieces of narration, pushing everything after it later. Quotes
are only proposed here - nothing enters the video until the user approves it.

Flow: Claude reads the narration, the quote bank (notable quotes already located in
the episodes, see services/quote_bank.py) and the list of episodes, and for each
place a quote would help either picks one from the bank or says what line it would
want and in which episodes it expects to find it -> for the latter, Claude reads the
full transcripts of those episodes and picks exact lines, or nothing -> the fragment is re-transcribed with word timestamps to find where the
words really start and stop, because the index only has line-level timings -> Claude
then hears every cut clip in its place, between the narration before and after it,
and keeps it, moves it to a sentence nearby or drops it (_review): a line can be the
right one and still sit badly where it was put.

How many quotes a video gets is the model's call - there is no limit, and none is
a fine answer.

There is no vector search here on purpose. A whole episode's transcript is only a
few thousand tokens, so reading it outright beats retrieving one-minute windows of
it: the model sees who is talking to whom, and finds lines that match in meaning
rather than in wording.
"""

import bisect
import copy
import difflib
import logging
import re
import subprocess
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import BankQuote, Source, TranscriptLine
from app.editor.llm import run_claude_json
from app.editor.store import Project, Quote, QuotePart, Word, project_shows, project_universe
from app.services.universes import still_shows

logger = logging.getLogger(__name__)

EPISODES_PER_QUOTE = 3
# the review shows this many sentences of narration either side of a clip, and may move it within them
REVIEW_BEFORE, REVIEW_AFTER = 3, 3
# the model is the "quotes" task's (Opus unless changed: see runtime_settings.DEFAULT_TASK_MODELS)
# transcripts are read in batches of episodes, at most about this many characters per call
BATCH_CHARS = 60_000
# A quote is a sentence or two. Whole speeches and exchanges stall the narration, so
# the prompts ask for QUOTE_LENGTH and a longer clip is dropped.
MAX_QUOTE_SECONDS = 12.0
QUOTE_LENGTH = "one or two sentences, about 2 to 8 seconds of speech (up to some 20 words)"
# banked quotes are whole exchanges; the model is shown those up to this long and takes a part
MAX_BANK_SECONDS = 40.0
BANK_LINE_CHARS = 320  # how much of a banked quote is shown to the model
# how many more words may sit between two matched runs in the audio than in the quote
# before the outer run is taken for a chance match elsewhere in the fragment
MATCH_GAP_WORDS = 3
FRAGMENT_PADDING = 2.0  # seconds of audio either side of the lines, re-transcribed to find the exact words
# breathing room kept around the spoken words: a little before the first, more after
# the last - the scene's sound is let die away there (see render._soundtrack)
LEAD_IN, LEAD_OUT = 0.2, 0.7
SENTENCE_END = (".", "!", "?", "…", '."', '!"', '?"')

WANTED_SCHEMA = {
    "type": "object",
    "properties": {
        "quotes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "after_sentence": {"type": "integer", "description": "The quote plays right after this sentence."},
                    "bank_quote": {
                        "type": "integer",
                        "description": (
                            "Number (after Q) of a quote from the quote bank, when one of them is the line you "
                            "want. Then leave episodes empty. Omit when the line is not in the bank."
                        ),
                    },
                    "bank_part": {
                        "type": "string",
                        "description": (
                            "With bank_quote: the words of that quote to play, copied exactly, without speaker "
                            f"names - {QUOTE_LENGTH}. Banked quotes are usually longer than that; take only the "
                            "part that makes the point."
                        ),
                    },
                    "query": {
                        "type": "string",
                        "description": (
                            "The line of dialogue you hope to find, as close to the real wording as you can "
                            "recall, with who says it and in what situation."
                        ),
                    },
                    "episodes": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": (
                            "Numbers (from the episode list) of the episodes where this line is most likely "
                            "spoken - the most likely first. One if you are sure, up to three if not."
                        ),
                    },
                },
                "required": ["after_sentence", "query", "episodes"],
            },
        }
    },
    "required": ["quotes"],
}

PICK_SCHEMA = {
    "type": "object",
    "properties": {
        "quotes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "request": {"type": "integer", "description": "The request number this quote answers."},
                    "first_line": {"type": "integer", "description": "Line id (the number after L) of the first line."},
                    "last_line": {"type": "integer", "description": "Line id of the last line (same as first for one line)."},
                    "speaker": {"type": "string", "description": "Who is speaking, or 'unknown'."},
                    "reason": {"type": "string", "description": "One sentence: what this adds to the narration at that point."},
                },
                "required": ["request", "first_line", "last_line", "speaker", "reason"],
            },
        }
    },
    "required": ["quotes"],
}

REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "clips": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "clip": {"type": "integer", "description": "The clip number as given."},
                    "verdict": {"type": "string", "enum": ["keep", "move", "drop"]},
                    "after_sentence": {
                        "type": "integer",
                        "description": "With move: the number of the sentence the clip should play after - one of those shown for it.",
                    },
                    "why": {
                        "type": "string",
                        "description": "One sentence: what the clip adds where it now sits, or why it goes.",
                    },
                },
                "required": ["clip", "verdict", "why"],
            },
        }
    },
    "required": ["clips"],
}

# Quote cuts (the project's quote_cuts setting): a quote may be put together from up
# to MAX_PIECES pieces of one moment, played back to back with the middle left out.
# These fields are only offered to the model when the setting is on.
MAX_PIECES = 3
# pieces that come out closer together than this play as one: nothing worth skipping
JOIN_GAP = 0.6
# pieces must come from one stretch of the episode at most this long
MAX_CUT_SPAN = 60.0
BANK_THEN = {
    "type": "array",
    "items": {"type": "string"},
    "description": (
        "With bank_quote, optional: one or two more pieces of the same quote to play straight after "
        "bank_part, in the order they are spoken, copied exactly - what is said between the pieces is cut out."
    ),
}
PICK_THEN = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {"first_line": {"type": "integer"}, "last_line": {"type": "integer"}},
        "required": ["first_line", "last_line"],
    },
    "description": (
        "Optional: one or two more runs of lines from later in the same scene, to play straight after the "
        "first - the lines between the runs are cut out."
    ),
}
CUTS_HINT = (
    "A quote may also be cut together: when the point takes two sentences that have something else said "
    "between them - a set-up and its answer, the two strong sentences of a speech - give them as separate "
    "pieces ({field}) and they play back to back with the middle left out. Each piece is a whole sentence, "
    "the pieces together keep to the same length, and they must still sound like one thought when joined; "
    "when one continuous piece does the job, use one."
)


def _offering(schema: dict, field: str, spec: dict) -> dict:
    """The schema with one more optional field on each quote."""
    schema = copy.deepcopy(schema)
    schema["properties"]["quotes"]["items"]["properties"][field] = spec
    return schema


def sentence_ends(words: list[Word]) -> list[int]:
    """Word index of the last word of every sentence."""
    ends = [i for i, word in enumerate(words) if word.text.endswith(SENTENCE_END)]
    if not ends or ends[-1] != len(words) - 1:
        ends.append(len(words) - 1)
    return ends


def resolve_placement(project: Project, words: list[Word], after_word: int) -> tuple[int, float, str]:
    """(after_word, narration time, text before it) for a quote placed after the
    given word. Once beats exist a quote can only sit between two beats, so the
    position snaps to the nearest beat boundary - a quote in the middle of a beat
    would leave half a beat's footage on either side of it."""
    after_word = max(0, min(after_word, len(words) - 1))
    if project.beats:
        beat = min(project.beats, key=lambda b: abs(b.word_end - after_word))
        after_word, at = beat.word_end, beat.end
    elif after_word >= len(words) - 1:
        at = project.audio_duration or words[-1].end
    else:
        # middle of the pause between the two sentences
        at = (words[after_word].end + words[after_word + 1].start) / 2
    before = " ".join(w.text for w in words[max(0, after_word - 11) : after_word + 1])
    return after_word, round(at, 3), before


def _clean_title(source: Source) -> str:
    """Episode titles parsed from file names carry the show name and stray dashes
    ("Avatar The Last Airbender - - The King of Omashu")."""
    title = (source.episode_title or "").split(" - ")[-1].strip(" -")
    return title or "untitled"


def _episode_label(source: Source, universe) -> str:
    parts = [source.show_title or ""]
    if source.book is not None:
        parts.append(f"{universe.season_word} {source.book}")
    if source.episode_number is not None:
        parts.append(f"Episode {source.episode_number}")
    return ", ".join(p for p in parts if p) + f" - {_clean_title(source)}"


def _bank(db: Session, episodes: dict[int, Source]) -> dict[int, BankQuote]:
    """Banked quotes from the project's episodes that are short enough to show in full."""
    rows = db.execute(select(BankQuote).where(BankQuote.source_id.in_(list(episodes))).order_by(BankQuote.id)).scalars()
    return {quote.id: quote for quote in rows if quote.end_time - quote.start_time <= MAX_BANK_SECONDS}


def _bank_pieces(banked: BankQuote, parts: list[str]) -> tuple[list[str], str]:
    """(wordings, speakers) of the parts of a banked quote that were asked for - the
    whole quote when no part was given or none has its words in the quote."""
    lines = [line.partition(": ") if ": " in line else ("", "", line) for line in banked.text.split("\n")]
    pieces, speakers = [], []
    for part in parts:
        wanted = _tokens(part)
        said_by, found = [], 0
        for speaker, _, text in lines:
            blocks = difflib.SequenceMatcher(a=wanted, b=_tokens(text), autojunk=False).get_matching_blocks()
            # runs of 3+ words, so that a stray "the" doesn't credit a line with the part
            shared = sum(block.size for block in blocks if block.size >= min(3, len(wanted)))
            found += shared
            if shared and speaker:
                said_by.append(speaker)
        if wanted and found >= 0.6 * len(wanted):
            pieces.append(part.strip())
            speakers += [s for s in said_by if s not in speakers]
    if not pieces:
        return [" ".join(text for _, _, text in lines)], banked.speakers
    return pieces, ", ".join(speakers) or banked.speakers


def _wanted(
    project: Project, words: list[Word], ends: list[int], episodes: list[Source], bank: dict[int, BankQuote], cwd: Path
) -> tuple[list[dict], float]:
    """What quotes the narration could use and where: one from the bank, or a line
    to look for and the episodes to look in."""
    starts = [0] + [e + 1 for e in ends[:-1]]
    sentences = [" ".join(w.text for w in words[s : e + 1]) for s, e in zip(starts, ends)]
    universe = project_universe(project)
    shows = ", ".join(project_shows(project)) or universe.name
    listing = "\n".join(f"[{source.id}] {_episode_label(source, universe)}" for source in episodes)
    cuts = project.settings.quote_cuts
    prompt = (
        f"Below is the narration of a video essay about {shows}, one numbered sentence per line. The editor "
        "wants to drop in a few short clips from the show where a character's own words make the point "
        "better than the narrator can - proof, a punchline, or an emotional beat. A clip is short: "
        f"{QUOTE_LENGTH}, never a whole speech or exchange.\n\n"
        "How many is yours to judge: suggest every place where a clip really earns its seat, and no others - "
        "a video may have many or none. A clip interrupts the narrator, so each has to be worth the "
        "interruption. Narration that retells something the shows never put on screen - a comic, a book, how "
        "the shows were made - seldom has a line from the shows that belongs in it: there a clip fits only "
        "where the narrator turns to a scene of the shows themselves.\n\n"
        "Judge each clip in its place - read the sentence it would follow and the one that would come after "
        "it:\n"
        "- It is about the very point being made there, not merely the same subject or character.\n"
        "- It does not come between two sentences that lean on each other. When the next sentence picks up "
        "the words or the thought of the one before ('...and so she left the city.' / 'Leaving, though, was "
        "the easy part.'), nothing goes between them: put the clip after the thought is finished, or leave "
        "it out.\n"
        "- It does not say again what the narrator has just said or is about to say, and does not pull "
        "against the point of the sentence before it.\n"
        "- The words to play are a complete sentence that makes sense heard on its own, by someone who does "
        "not remember the scene - not a clause that needs the rest of its speech ('My brother, who never "
        "once...').\n\n"
        "For each, give the sentence it should play after, the line you have in mind, and the "
        "episodes to look for it in. Only suggest lines that are really spoken in the show; the transcripts "
        "of the episodes you name will be read to find them, so be specific about the wording and the scene.\n\n"
        + (
            "A quote bank is given below: well-known quotes that have already been found in the episodes. "
            "When the line you want is there, give its number as bank_quote and the words to play as "
            "bank_part - it is then cut with no search. The bank only saves that search: decide what line the "
            "place needs first, and never settle for a banked line because it is at hand - a line that is not "
            "in the bank is looked for just the same. Most banked quotes are longer than a clip should be: "
            "copy only the sentence or two that make the point, as whole sentences. "
            + (CUTS_HINT.format(field="bank_part, then bank_then") + " " if cuts else "")
            + "When nothing in the bank fits, describe the line and name the episodes as usual.\n\n"
            if bank
            else ""
        )
        + "Narration:\n" + "\n".join(f"[{i}] {s}" for i, s in enumerate(sentences))
        + "\n\nEpisodes available (give the number in brackets):\n" + listing
        + (
            "\n\nQuote bank (the episode number in brackets, then the lines):\n"
            + "\n".join(
                f"Q{quote.id} [{quote.source_id}] " + " / ".join(quote.text.split("\n"))[:BANK_LINE_CHARS]
                for quote in bank.values()
            )
            if bank
            else ""
        )
    )
    schema = _offering(WANTED_SCHEMA, "bank_then", BANK_THEN) if cuts and bank else WANTED_SCHEMA
    result, cost = run_claude_json(prompt, schema, cwd=cwd, timeout=600, task="quotes")
    known = {source.id for source in episodes}
    wanted = []
    for quote in result["quotes"]:
        if not 0 <= int(quote["after_sentence"]) < len(ends):
            continue
        quote["episodes"] = [int(e) for e in quote.get("episodes", []) if int(e) in known][:EPISODES_PER_QUOTE]
        if quote.get("bank_quote") is not None and int(quote["bank_quote"]) not in bank:
            quote["bank_quote"] = None  # no such number: fall back to looking for it
        if quote.get("bank_quote") is not None or quote["episodes"]:
            wanted.append(quote)
    return wanted, cost or 0.0


def _read_episodes(
    db: Session, wanted: list[dict], episodes: dict[int, Source], universe, cwd: Path, report, cuts: bool = False
) -> tuple[list[dict], dict[int, set[int]], float]:
    """Has Claude read the full transcripts of the named episodes and pick lines.
    Returns the picks, the line ids that were shown per request (to validate picks
    against), and the cost."""
    transcripts: dict[int, list[TranscriptLine]] = {}
    for source_id in dict.fromkeys(e for want in wanted for e in want["episodes"]):
        transcripts[source_id] = list(
            db.execute(
                select(TranscriptLine).where(TranscriptLine.source_id == source_id).order_by(TranscriptLine.start_time)
            ).scalars()
        )

    # several episodes per call, so a quote's candidate episodes are usually read together
    batches: list[list[int]] = [[]]
    size = 0
    for source_id, lines in transcripts.items():
        length = sum(len(line.text) + 16 for line in lines)
        if batches[-1] and size + length > BATCH_CHARS:
            batches.append([])
            size = 0
        batches[-1].append(source_id)
        size += length

    picks: list[dict] = []
    offered: dict[int, set[int]] = {n: set() for n in range(len(wanted))}
    cost = 0.0
    for b, batch in enumerate(batches):
        requests = [(n, want) for n, want in enumerate(wanted) if any(e in batch for e in want["episodes"])]
        if not requests:
            continue
        blocks = []
        for source_id in batch:
            lines = transcripts[source_id]
            blocks.append(
                f"=== Episode [{source_id}] {_episode_label(episodes[source_id], universe)}\n"
                + "\n".join(
                    f"L{line.id} [{int(line.start_time // 60)}:{int(line.start_time % 60):02d}] "
                    + (f"{line.speaker}: " if line.speaker else "") + line.text.strip()
                    for line in lines
                )
            )
            for n, want in requests:
                if source_id in want["episodes"]:
                    offered[n].update(line.id for line in lines)
        asks = "\n".join(
            f"Request {n}: {want['query']} (look in episode{'s' if len(want['episodes']) > 1 else ''} "
            + ", ".join(f"[{e}]" for e in want["episodes"] if e in batch) + ")"
            for n, want in requests
        )
        prompt = (
            "An editor wants to drop short clips of dialogue from a show into a video essay. Below are the "
            "lines they are looking for, and the full transcripts of the episodes where each is expected. The "
            "transcripts come from speech recognition and may contain errors. A name before a line is who says "
            "it (taken from a fan transcript; not every line has one).\n\n"
            "For each request, find the line or the few consecutive lines that make the best quote - a complete "
            f"thought that stands on its own, {QUOTE_LENGTH} - and give the line ids. Out of a longer speech "
            f"or exchange take only its strongest part: a quote over {MAX_QUOTE_SECONDS:.0f} seconds is thrown "
            "away. Start and end "
            "the quote on whole sentences. Where a line has no name, work out who is speaking from the "
            "conversation around it. If the "
            "line is not in the transcripts given, or nothing there is worth quoting, leave that request out; "
            "a weak quote is worse than none. Lines must be consecutive and from one episode."
            + (" " + CUTS_HINT.format(field="the first run of lines as usual, the later ones in `then`") if cuts else "")
            + f"\n\n{asks}\n\n" + "\n\n".join(blocks)
        )
        report(f"reading episodes {b + 1}/{len(batches)}")
        schema = _offering(PICK_SCHEMA, "then", PICK_THEN) if cuts else PICK_SCHEMA
        result, batch_cost = run_claude_json(prompt, schema, cwd=cwd, timeout=900, task="quotes")
        cost += batch_cost or 0.0
        picks.extend(result["quotes"])
    return picks, offered, cost


def _review(project: Project, words: list[Word], ends: list[int], universe, cwd: Path) -> float:
    """Has Claude hear every proposed clip where it would play and keep it, move it
    to a sentence boundary nearby, or drop it. The clips were chosen from memory of
    the line and cut afterwards; this is the first time one is read as cut, between
    the sentences around it. Approved quotes are the user's and are left alone."""
    proposed = [quote for quote in project.quotes if quote.status != "approved"]
    if not proposed:
        return 0.0
    starts = [0] + [e + 1 for e in ends[:-1]]
    sentences = [" ".join(w.text for w in words[s : e + 1]) for s, e in zip(starts, ends)]
    blocks, windows = [], []
    for n, quote in enumerate(proposed):
        # the sentence the clip plays after (a clip snapped to a beat may sit inside one)
        at = min(bisect.bisect_left(ends, quote.after_word), len(ends) - 1)
        low, high = max(0, at - REVIEW_BEFORE + 1), min(len(ends) - 1, at + REVIEW_AFTER)
        windows.append((low, high))
        where = ", ".join(
            [quote.show_title]
            + ([f"{universe.season_word} {quote.book}"] if quote.book is not None else [])
            + ([f"Episode {quote.episode_number}"] if quote.episode_number is not None else [])
        )
        lines = []
        for i in range(low, high + 1):
            lines.append(f"[{i}] {sentences[i]}")
            if i == at:
                lines.append(f"    >>> CLIP {n} ({quote.speaker or 'unknown'}, {where}): \"{quote.text}\"")
        blocks.append("\n".join(lines))
    prompt = (
        f"Short clips of dialogue from {', '.join(project_shows(project)) or universe.name} have been cut to be "
        "dropped into the narration of a video essay: the narrator stops, the clip plays with the show's own "
        "sound, the narrator goes on. Below, each clip is shown where it would play, with the numbered "
        "sentences of narration before and after it; the words in quotes are exactly what is heard.\n\n"
        "Hear each one in its place, as a viewer would, and decide:\n"
        "- keep: the clip is about the very point the narrator is making there, the words are a complete "
        "thought that makes sense heard on their own, and the narration reads naturally through it.\n"
        "- move: the clip belongs here but at a different sentence boundary - most often because where it "
        "sits it comes between two sentences that lean on each other (the next one picks up the words or the "
        "thought of the one before), or before the sentence it answers. Give the sentence it should play "
        "after instead, from those shown.\n"
        "- drop: it shares only the subject with the narration, repeats what the narrator says right there, "
        "pulls against the point being made, is a fragment that needs the rest of its speech, or makes the "
        "same point as another clip (keep the better of the two).\n\n"
        "Be strict: a clip interrupts the narrator, and one that does not fit is worse than none. Any number "
        "may stay, including all or none. Answer for every clip.\n\n"
        + "\n\n".join(blocks)
    )
    result, cost = run_claude_json(prompt, REVIEW_SCHEMA, cwd=cwd, timeout=600, task="quotes")
    dropped: set[str] = set()
    for answer in result.get("clips", []):
        n = int(answer.get("clip", -1))
        if not 0 <= n < len(proposed):
            continue
        quote, why = proposed[n], (answer.get("why") or "").strip()
        if answer["verdict"] == "drop":
            logger.info("quote dropped in review: %r - %s", quote.text, why)
            dropped.add(quote.id)
            continue
        target = answer.get("after_sentence")
        if answer["verdict"] == "move" and target is not None and windows[n][0] <= int(target) <= windows[n][1]:
            quote.after_word, quote.at, quote.after_text = resolve_placement(project, words, ends[int(target)])
        if why:
            quote.reason = why + (" (from the quote bank)" if quote.reason.startswith("From the quote bank") else "")
    project.quotes = [quote for quote in project.quotes if quote.id not in dropped]
    return cost or 0.0


def text_between(source_id: int, start: float, end: float) -> str:
    """The transcript of an episode between two times - the lines whose middle falls
    inside the range - for showing what a re-cut quote now says."""
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        lines = db.execute(
            select(TranscriptLine).where(
                TranscriptLine.source_id == source_id,
                TranscriptLine.end_time > start,
                TranscriptLine.start_time < end,
            ).order_by(TranscriptLine.start_time)
        ).scalars()
        kept = [line.text.strip() for line in lines if start <= (line.start_time + min(line.end_time, line.start_time + 6)) / 2 <= end]
    finally:
        db.close()
    return " ".join(kept)


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower())


def _hear(file_path: str, start: float, end: float, cache_dir: Path) -> tuple[float, list]:
    """Transcribes a stretch of the episode (with some padding) again, with word
    timings. Returns the time the fragment starts at and the words heard in it."""
    from app.pipeline.stages.transcribe import _whisper_model

    fragment_start = max(0.0, start - FRAGMENT_PADDING)
    wav = cache_dir / f"quote_{uuid.uuid4().hex[:8]}.wav"
    try:
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{fragment_start:.3f}",
             "-t", f"{end - start + 2 * FRAGMENT_PADDING:.3f}", "-i", file_path, "-vn", "-ac", "1", "-ar", "16000", str(wav)],
            check=True, capture_output=True,
        )
        segments, _ = _whisper_model().transcribe(str(wav), word_timestamps=True)
        heard = [w for segment in segments for w in (segment.words or []) if w.word.strip()]
    finally:
        wav.unlink(missing_ok=True)
    return fragment_start, heard


def _locate(heard: list, offset: float, text: str, after: float = 0.0) -> tuple[float, float, float] | None:
    """Where the clip for the words of `text` starts and stops, and where its last
    word stops, in episode time, among the words heard at or after `after` - None
    when they can't be found again."""
    skip = next((i for i, w in enumerate(heard) if offset + w.start >= after), len(heard))
    wanted_tokens = _tokens(text)
    heard_tokens = [(_tokens(w.word) or [""])[0] for w in heard[skip:]]
    matcher = difflib.SequenceMatcher(a=wanted_tokens, b=heard_tokens, autojunk=False)
    blocks = [b for b in matcher.get_matching_blocks() if b.size > 0]
    matched = sum(b.size for b in blocks)
    if not wanted_tokens or matched < 0.6 * len(wanted_tokens):
        return None

    # The quote may be a part of what was transcribed, so a word of it can also match
    # somewhere else in the fragment. Grow outwards from the longest run and stop at a
    # run that lies much further away in the audio than it does in the quote.
    low = high = max(range(len(blocks)), key=lambda i: blocks[i].size)

    def apart(before, after) -> bool:
        return after.b - (before.b + before.size) > after.a - (before.a + before.size) + MATCH_GAP_WORDS

    while low > 0 and not apart(blocks[low - 1], blocks[low]):
        low -= 1
    while high < len(blocks) - 1 and not apart(blocks[high], blocks[high + 1]):
        high += 1
    first = skip + blocks[low].b
    last = skip + blocks[high].b + blocks[high].size - 1
    start = heard[first].start - LEAD_IN
    end = heard[last].end + LEAD_OUT
    # the breathing room must not reach into the words said just before and after
    if first > 0:
        start = max(start, min(heard[first - 1].end, heard[first].start - 0.05))
    if last + 1 < len(heard):
        end = min(end, max(heard[last + 1].start - 0.05, heard[last].end + 0.1))
    return max(0.0, offset + start), offset + end, offset + heard[last].end


def _cut(
    file_path: str, start: float, end: float, pieces: list[tuple[str, tuple[float, float] | None]], cache_dir: Path
) -> list[QuotePart]:
    """Where the quoted words actually are in the episode. The index only knows whole
    lines, so start..end is transcribed again with word timings and each piece's
    words are located in it, one after another. A piece comes with the time range to
    use when its words can't be found again (line timings from the index), or None to
    leave it out then. Pieces with next to nothing between them come back as one."""
    offset, heard = _hear(file_path, start, end, cache_dir)
    parts: list[QuotePart] = []
    for text, fallback in pieces:
        span = _locate(heard, offset, text, after=parts[-1].end - LEAD_OUT if parts else 0.0)
        if span is None:
            if fallback is None:
                continue
            span = (max(0.0, fallback[0] - LEAD_IN), fallback[1] + LEAD_OUT, None)
        speech_end = round(span[2], 3) if span[2] is not None else None
        if parts and span[0] <= parts[-1].end + JOIN_GAP:
            parts[-1].end = max(parts[-1].end, round(span[1], 3))
            parts[-1].text += " " + text
            parts[-1].speech_end = speech_end
        else:
            parts.append(QuotePart(start=round(span[0], 3), end=round(span[1], 3), text=text, speech_end=speech_end))
    return parts


def retail(quote: Quote, cache_dir: Path) -> bool:
    """Gives a quote cut before the longer tail its new end: its last piece is heard
    again, and the clip ends LEAD_OUT after the last word, or just before whoever
    speaks next. It never gets shorter. Returns whether anything changed."""
    start, end = quote.spans()[-1]
    text = quote.parts[-1].text if quote.parts else quote.text
    offset, heard = _hear(quote.file_path, start, end, cache_dir)
    span = _locate(heard, offset, text)
    if span is None or abs(span[0] - start) > 1.0:
        return False  # not the words it was cut by - set by hand, or not heard again
    new_end = round(max(end, span[1]), 3)
    speech_end = round(span[2], 3)
    if new_end == end and quote.speech_end == speech_end:
        return False
    quote.speech_end = speech_end
    if quote.parts:
        quote.parts[-1].end, quote.parts[-1].speech_end = new_end, speech_end
    quote.clip_end = new_end
    return True


def find_quotes(db: Session, project: Project, words: list[Word], cwd: Path, report) -> float:
    """Replaces the project's unapproved quote proposals with fresh ones; approved
    quotes are kept. Returns the LLM cost."""
    ends = sentence_ends(words)
    universe = project_universe(project)
    shows = project_shows(project)
    stmt = select(Source).order_by(Source.show_title, Source.book, Source.episode_number)
    if shows:
        stmt = stmt.where(Source.show_title.in_(shows))
    # only episodes that have a transcript to read
    transcribed = set(db.execute(select(TranscriptLine.source_id).distinct()).scalars())
    # a show that may only be shown as stills is not quoted either: a quote plays its sound
    stills = still_shows()
    episodes = {
        source.id: source for source in db.execute(stmt).scalars()
        if source.id in transcribed and source.show_title not in stills
    }
    if not episodes:
        raise RuntimeError("None of this project's shows has a transcript yet (or may be quoted)")

    report("looking for places a quote would help")
    cuts = project.settings.quote_cuts
    bank = _bank(db, episodes)
    wanted, cost = _wanted(project, words, ends, list(episodes.values()), bank, cwd)

    kept = [q for q in project.quotes if q.status == "approved"]
    project.quotes = kept
    if not wanted:
        return cost

    cache_dir = cwd / "cache"
    cache_dir.mkdir(exist_ok=True)

    def add(request: int, source: Source, start: float, end: float, pieces: list, speaker: str, reason: str) -> bool:
        """Cuts the pieces (see _cut) out of start..end; False when nothing was found
        or the clip came out too long."""
        parts = _cut(source.file_path, start, end, pieces, cache_dir)
        if not parts or sum(part.end - part.start for part in parts) > MAX_QUOTE_SECONDS:
            return False
        after_word, at, before = resolve_placement(project, words, ends[int(wanted[request]["after_sentence"])])
        project.quotes.append(
            Quote(
                id=f"quote_{uuid.uuid4().hex[:8]}",
                after_word=after_word,
                at=at,
                after_text=before,
                source_id=source.id,
                file_path=source.file_path,
                show_title=source.show_title,
                book=source.book,
                episode_number=source.episode_number,
                clip_start=parts[0].start,
                clip_end=parts[-1].end,
                parts=parts if len(parts) > 1 else [],
                speech_end=parts[-1].speech_end,
                text=" … ".join(part.text for part in parts),
                speaker=speaker,
                reason=reason,
            )
        )
        return True

    # quotes taken from the bank are already located - no episodes to read
    answered: set[int] = set()
    for request, want in enumerate(wanted):
        if want.get("bank_quote") is None:
            continue
        banked = bank[int(want["bank_quote"])]
        if any(q.source_id == banked.source_id and q.clip_start < banked.end_time and q.clip_end > banked.start_time for q in project.quotes):
            answered.add(request)
            continue
        report(f"cutting a quote from the bank ({len(answered) + 1})")
        # the wording without the "Speaker:" prefixes, to find the exact words in the audio
        asked = [want.get("bank_part") or ""] + (list(want.get("bank_then") or []) if cuts else [])
        spoken, speakers = _bank_pieces(banked, asked[:MAX_PIECES])
        # a lone piece that can't be heard again plays as the bank timed it
        whole = (banked.start_time, banked.end_time) if len(spoken) == 1 else None
        add(request, episodes[banked.source_id], banked.start_time, banked.end_time, [(text, whole) for text in spoken], speakers, f"From the quote bank. {want['query']}".strip())
        answered.add(request)

    wanted_search = [want if n not in answered and want["episodes"] else {**want, "episodes": []} for n, want in enumerate(wanted)]
    picks, offered = [], {}
    if any(want["episodes"] for want in wanted_search):
        picks, offered, read_cost = _read_episodes(db, wanted_search, episodes, universe, cwd, report, cuts)
        cost += read_cost
    for n, pick in enumerate(picks):
        request = int(pick["request"])
        # one quote per request: its episodes may have been read in separate batches
        if not 0 <= request < len(wanted) or request in answered:
            continue
        # the runs of lines to play: one, or several when the quote is cut together
        runs: list[list[TranscriptLine]] = []
        for run in [pick, *(pick.get("then") or [])][: MAX_PIECES if cuts else 1]:
            lines = list(
                db.execute(
                    select(TranscriptLine)
                    .where(TranscriptLine.id >= int(run["first_line"]), TranscriptLine.id <= int(run["last_line"]))
                    .order_by(TranscriptLine.start_time)
                ).scalars()
            )
            if not lines or not {line.id for line in lines} <= offered[request]:
                break
            # line timings include the pauses around the words; the real check is on the cut clip
            if lines[-1].end_time - lines[0].start_time > MAX_QUOTE_SECONDS * 2:
                break
            if runs and (
                lines[0].source_id != runs[0][0].source_id
                or lines[0].start_time < runs[-1][-1].end_time
                or lines[-1].end_time - runs[0][0].start_time > MAX_CUT_SPAN
            ):
                break  # a later piece must follow the first, in the same stretch of the episode
            runs.append(lines)
        if not runs or len({line.source_id for line in runs[0]}) != 1:
            continue
        start, end = runs[0][0].start_time, runs[-1][-1].end_time
        source = db.get(Source, runs[0][0].source_id)
        if any(q.source_id == source.id and q.clip_start < end and q.clip_end > start for q in project.quotes):
            continue  # same moment as a quote already in the list

        report(f"cutting quote {n + 1}/{len(picks)}")
        pieces = [(" ".join(line.text.strip() for line in lines), (lines[0].start_time, lines[-1].end_time)) for lines in runs]
        if add(request, source, start, end, pieces, pick["speaker"], pick["reason"]):
            answered.add(request)
    if any(quote.status != "approved" for quote in project.quotes):
        report("checking how each quote sits in the narration")
        cost += _review(project, words, ends, universe, cwd)
    project.quotes.sort(key=lambda q: q.at)
    return cost
