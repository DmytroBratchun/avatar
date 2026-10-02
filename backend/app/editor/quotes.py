"""Finds lines of dialogue from the show that would strengthen the narration, and
cuts them out precisely.

A quote is its own stretch of the final video: the clip plays with its original
sound between two pieces of narration, pushing everything after it later. Quotes
are only proposed here - nothing enters the video until the user approves it.

Flow: Claude reads the narration and the list of episodes and says what line it
would want, after which sentence, and in which episodes it expects to find it ->
Claude reads the full transcripts of those episodes and picks exact lines, or
nothing -> the fragment is re-transcribed with word timestamps to find where the
words really start and stop, because the index only has line-level timings.

There is no vector search here on purpose. A whole episode's transcript is only a
few thousand tokens, so reading it outright beats retrieving one-minute windows of
it: the model sees who is talking to whom, and finds lines that match in meaning
rather than in wording.
"""

import difflib
import re
import subprocess
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Source, TranscriptLine
from app.editor.llm import run_claude_json
from app.editor.store import Project, Quote, Word, project_shows, project_universe

MAX_QUOTES = 8
EPISODES_PER_QUOTE = 3
# Quotes depend on the model remembering which line is spoken where. Tried with Sonnet,
# recall turned up lines that are not in the show at all; Opus found 6 of 7.
QUOTE_MODEL = "opus"
# transcripts are read in batches of episodes, at most about this many characters per call
BATCH_CHARS = 60_000
MAX_QUOTE_SECONDS = 40.0
FRAGMENT_PADDING = 2.0  # seconds of audio either side of the lines, re-transcribed to find the exact words
LEAD_IN, LEAD_OUT = 0.2, 0.3  # breathing room kept around the spoken words
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


def _wanted(
    project: Project, words: list[Word], ends: list[int], episodes: list[Source], cwd: Path
) -> tuple[list[dict], float]:
    """What quotes the narration could use, where, and which episodes to look in."""
    starts = [0] + [e + 1 for e in ends[:-1]]
    sentences = [" ".join(w.text for w in words[s : e + 1]) for s, e in zip(starts, ends)]
    universe = project_universe(project)
    shows = ", ".join(project_shows(project)) or universe.name
    listing = "\n".join(f"[{source.id}] {_episode_label(source, universe)}" for source in episodes)
    prompt = (
        f"Below is the narration of a video essay about {shows}, one numbered sentence per line. The editor "
        "wants to drop in a few short clips from the show where a character's own words make the point "
        "better than the narrator can - proof, a punchline, or an emotional beat.\n\n"
        f"Suggest up to {MAX_QUOTES} such moments, fewer if the narration doesn't call for them (none is a "
        "fine answer). For each, give the sentence it should play after, the line you have in mind, and the "
        "episodes to look for it in. Only suggest lines that are really spoken in the show; the transcripts "
        "of the episodes you name will be read to find them, so be specific about the wording and the scene. "
        "Don't pick lines that just repeat what the narrator said.\n\n"
        "Narration:\n" + "\n".join(f"[{i}] {s}" for i, s in enumerate(sentences))
        + "\n\nEpisodes available (give the number in brackets):\n" + listing
    )
    result, cost = run_claude_json(prompt, WANTED_SCHEMA, cwd=cwd, timeout=600, model=QUOTE_MODEL)
    known = {source.id for source in episodes}
    wanted = []
    for quote in result["quotes"][:MAX_QUOTES]:
        if not 0 <= int(quote["after_sentence"]) < len(ends):
            continue
        quote["episodes"] = [int(e) for e in quote.get("episodes", []) if int(e) in known][:EPISODES_PER_QUOTE]
        if quote["episodes"]:
            wanted.append(quote)
    return wanted, cost or 0.0


def _read_episodes(
    db: Session, wanted: list[dict], episodes: dict[int, Source], universe, cwd: Path, report
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
                    f"L{line.id} [{int(line.start_time // 60)}:{int(line.start_time % 60):02d}] {line.text.strip()}"
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
            "transcripts come from speech recognition: they have no speaker names and may contain errors.\n\n"
            "For each request, find the line or the few consecutive lines that make the best quote - a complete "
            "thought that stands on its own, ideally 3 to 15 seconds - and give the line ids. Start and end "
            "the quote on whole sentences. Work out who is speaking from the conversation around it. If the "
            "line is not in the transcripts given, or nothing there is worth quoting, leave that request out; "
            "a weak quote is worse than none. Lines must be consecutive and from one episode.\n\n"
            f"{asks}\n\n" + "\n\n".join(blocks)
        )
        report(f"reading episodes {b + 1}/{len(batches)}")
        result, batch_cost = run_claude_json(prompt, PICK_SCHEMA, cwd=cwd, timeout=900, model=QUOTE_MODEL)
        cost += batch_cost or 0.0
        picks.extend(result["quotes"])
    return picks, offered, cost


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower())


def _exact_span(file_path: str, start: float, end: float, quote_text: str, cache_dir: Path) -> tuple[float, float]:
    """Where the quoted words actually start and stop in the episode. The index only
    knows whole lines, so the surrounding audio is transcribed again with word
    timings and the quote's words are located in it."""
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

    wanted_tokens = _tokens(quote_text)
    heard_tokens = [(_tokens(w.word) or [""])[0] for w in heard]
    matcher = difflib.SequenceMatcher(a=wanted_tokens, b=heard_tokens, autojunk=False)
    blocks = [b for b in matcher.get_matching_blocks() if b.size > 0]
    matched = sum(b.size for b in blocks)
    if not wanted_tokens or matched < 0.6 * len(wanted_tokens):
        # couldn't find the words again - fall back to the line timings from the index
        return max(0.0, start - LEAD_IN), end + LEAD_OUT

    first, last = heard[blocks[0].b], heard[blocks[-1].b + blocks[-1].size - 1]
    return max(0.0, fragment_start + first.start - LEAD_IN), fragment_start + last.end + LEAD_OUT


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
    episodes = {source.id: source for source in db.execute(stmt).scalars() if source.id in transcribed}
    if not episodes:
        raise RuntimeError("None of this project's shows has a transcript yet")

    report("looking for places a quote would help")
    wanted, cost = _wanted(project, words, ends, list(episodes.values()), cwd)

    kept = [q for q in project.quotes if q.status == "approved"]
    project.quotes = kept
    if not wanted:
        return cost

    picks, offered, read_cost = _read_episodes(db, wanted, episodes, universe, cwd, report)
    cost += read_cost
    answered: set[int] = set()

    cache_dir = cwd / "cache"
    cache_dir.mkdir(exist_ok=True)
    for n, pick in enumerate(picks):
        request = int(pick["request"])
        # one quote per request: its episodes may have been read in separate batches
        if not 0 <= request < len(wanted) or request in answered:
            continue
        lines = list(
            db.execute(
                select(TranscriptLine)
                .where(TranscriptLine.id >= int(pick["first_line"]), TranscriptLine.id <= int(pick["last_line"]))
                .order_by(TranscriptLine.start_time)
            ).scalars()
        )
        if not lines or len({line.source_id for line in lines}) != 1 or not {line.id for line in lines} <= offered[request]:
            continue
        start, end = lines[0].start_time, lines[-1].end_time
        if end - start > MAX_QUOTE_SECONDS:
            continue
        source = db.get(Source, lines[0].source_id)
        if any(q.source_id == source.id and q.clip_start < end and q.clip_end > start for q in project.quotes):
            continue  # same moment as a quote already in the list

        answered.add(request)
        report(f"cutting quote {n + 1}/{len(picks)}")
        text = " ".join(line.text.strip() for line in lines)
        clip_start, clip_end = _exact_span(source.file_path, start, end, text, cache_dir)
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
                clip_start=round(clip_start, 3),
                clip_end=round(clip_end, 3),
                text=text,
                speaker=pick["speaker"],
                reason=pick["reason"],
            )
        )
    project.quotes.sort(key=lambda q: q.at)
    return cost
