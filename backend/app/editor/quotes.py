"""Finds lines of dialogue from the show that would strengthen the narration, and
cuts them out precisely.

A quote is its own stretch of the final video: the clip plays with its original
sound between two pieces of narration, pushing everything after it later. Quotes
are only proposed here - nothing enters the video until the user approves it.

Flow: Claude reads the narration and says what kind of line it would want and
where -> those descriptions are searched in the transcript-chunk index (RAG) ->
Claude reads the retrieved dialogue and picks exact lines, or nothing -> the
fragment is re-transcribed with word timestamps to find where the words really
start and stop, because the index only has line-level timings.
"""

import difflib
import re
import subprocess
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Source, TranscriptChunk, TranscriptLine
from app.editor.llm import run_claude_json
from app.editor.store import Project, Quote, Word, project_shows, project_universe
from app.services.embeddings import embed_text
from app.vectorstore.sqlite_vec_client import knn_chunks

MAX_QUOTES = 8
CHUNKS_PER_QUERY = 4
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
                },
                "required": ["after_sentence", "query"],
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


def _wanted(project: Project, words: list[Word], ends: list[int], cwd: Path) -> tuple[list[dict], float]:
    starts = [0] + [e + 1 for e in ends[:-1]]
    sentences = [" ".join(w.text for w in words[s : e + 1]) for s, e in zip(starts, ends)]
    shows = ", ".join(project_shows(project)) or project_universe(project).name
    prompt = (
        f"Below is the narration of a video essay about {shows}, one numbered sentence per line. The editor "
        "wants to drop in a few short clips from the show where a character's own words make the point "
        "better than the narrator can - proof, a punchline, or an emotional beat.\n\n"
        f"Suggest up to {MAX_QUOTES} such moments, fewer if the narration doesn't call for them (none is a "
        "fine answer). For each, give the sentence it should play after and the line you have in mind. Only "
        "suggest lines that are really spoken in the show; they will be looked up in the episode transcripts, "
        "so be specific about the wording and the scene. Don't pick lines that just repeat what the narrator "
        "said.\n\nNarration:\n" + "\n".join(f"[{i}] {s}" for i, s in enumerate(sentences))
    )
    result, cost = run_claude_json(prompt, WANTED_SCHEMA, cwd=cwd)
    wanted = [q for q in result["quotes"] if 0 <= int(q["after_sentence"]) < len(ends)][:MAX_QUOTES]
    return wanted, cost or 0.0


def _retrieve(db: Session, project: Project, query: str) -> list[tuple[Source, list[TranscriptLine]]]:
    """Dialogue excerpts likely to contain the wanted line: the lines covered by
    the best-matching transcript chunks, merged per episode."""
    candidate_ids = None
    shows = project_shows(project)
    if shows:
        candidate_ids = list(
            db.execute(select(TranscriptChunk.id).where(TranscriptChunk.show_title.in_(shows))).scalars()
        )
        if not candidate_ids:
            return []
    hits = knn_chunks(db, embed_text(query), candidate_ids, k=CHUNKS_PER_QUERY)
    chunks = list(db.execute(select(TranscriptChunk).where(TranscriptChunk.id.in_([cid for cid, _ in hits]))).scalars())

    excerpts: dict[int, dict[int, TranscriptLine]] = {}
    for chunk in chunks:
        lines = db.execute(
            select(TranscriptLine).where(
                TranscriptLine.source_id == chunk.source_id,
                TranscriptLine.start_time < chunk.end_time,
                TranscriptLine.end_time > chunk.start_time,
            )
        ).scalars()
        excerpts.setdefault(chunk.source_id, {}).update({line.id: line for line in lines})

    result = []
    for source_id, lines in excerpts.items():
        source = db.get(Source, source_id)
        if source is not None:
            result.append((source, sorted(lines.values(), key=lambda line: line.start_time)))
    return result


def _pick(wanted: list[dict], retrieved: list[list[tuple[Source, list[TranscriptLine]]]], cwd: Path) -> tuple[list[dict], float]:
    blocks = []
    for n, (want, excerpts) in enumerate(zip(wanted, retrieved)):
        block = [f"=== Request {n}: {want['query']}"]
        for source, lines in excerpts:
            block.append(
                f"--- {source.show_title or ''} Book {source.book} Episode {source.episode_number} "
                f"'{source.episode_title or ''}'. {source.episode_summary or ''}"
            )
            block += [f"L{line.id} [{int(line.start_time // 60)}:{int(line.start_time % 60):02d}] {line.text}" for line in lines]
        blocks.append("\n".join(block))

    prompt = (
        "An editor wants to drop short clips of dialogue from an animated show into a video essay. For each "
        "request below, transcript excerpts that might contain the wanted line were retrieved. The transcripts "
        "come from speech recognition: they have no speaker names and may contain errors.\n\n"
        "For each request, pick the line or the few consecutive lines that make the best quote - a complete "
        "thought that stands on its own, ideally 3 to 15 seconds - and give the line ids. Work out who is "
        "speaking from the episode summary and the surrounding lines. If none of the excerpts contains a line "
        "worth quoting for a request, leave that request out; a weak quote is worse than none. Lines must be "
        "consecutive and from the same episode.\n\n" + "\n\n".join(blocks)
    )
    result, cost = run_claude_json(prompt, PICK_SCHEMA, cwd=cwd, timeout=600)
    return result["quotes"], cost or 0.0


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
    report("looking for places a quote would help")
    wanted, cost = _wanted(project, words, ends, cwd)

    kept = [q for q in project.quotes if q.status == "approved"]
    project.quotes = kept
    if not wanted:
        return cost

    report(f"searching transcripts for {len(wanted)} quotes")
    retrieved = [_retrieve(db, project, want["query"]) for want in wanted]
    if not any(retrieved):
        raise RuntimeError("No transcript chunks are indexed for the selected shows - run the transcript-chunk index first")

    report("choosing the exact lines")
    picks, pick_cost = _pick(wanted, retrieved, cwd)
    cost += pick_cost

    cache_dir = cwd / "cache"
    cache_dir.mkdir(exist_ok=True)
    for n, pick in enumerate(picks):
        request = int(pick["request"])
        if not 0 <= request < len(wanted):
            continue
        lines = list(
            db.execute(
                select(TranscriptLine)
                .where(TranscriptLine.id >= int(pick["first_line"]), TranscriptLine.id <= int(pick["last_line"]))
                .order_by(TranscriptLine.start_time)
            ).scalars()
        )
        offered = {line.id for _, excerpt in retrieved[request] for line in excerpt}
        if not lines or len({line.source_id for line in lines}) != 1 or not {line.id for line in lines} <= offered:
            continue
        start, end = lines[0].start_time, lines[-1].end_time
        if end - start > MAX_QUOTE_SECONDS:
            continue
        source = db.get(Source, lines[0].source_id)
        if any(q.source_id == source.id and q.clip_start < end and q.clip_end > start for q in project.quotes):
            continue  # same moment as a quote already in the list

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
