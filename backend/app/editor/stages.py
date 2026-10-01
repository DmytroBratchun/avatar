"""Editor stages. Each takes a project id and a report(detail) callback, reads and
writes the project through app.editor.store, and can be re-run at any time."""

import math
import re
from collections.abc import Callable

from app.editor import store
from app.editor.footage import MAX_CLIP_SECONDS, SearchContext, Usage, choose, search_slot
from app.editor.llm import run_claude_json
from app.editor.quotes import resolve_placement
from app.editor.render import render_project
from app.editor.store import Beat, Segment, Slot, Word

Report = Callable[[str], None]

# --- transcribe -------------------------------------------------------------


def _hotwords_from_script(script_text: str | None, limit: int = 40) -> str | None:
    """Proper nouns from the script (Aang, Yangchen, Sozin...) - the words Whisper
    most often mangles and the ones search depends on most. A capitalised word
    counts as a name when it never also appears in lowercase, which drops ordinary
    sentence-initial words without needing a dictionary."""
    if not script_text:
        return None
    tokens = re.findall(r"[A-Za-z][A-Za-z'-]*", script_text)
    lowercase_seen = {t for t in tokens if t[0].islower()}
    names: list[str] = []
    for token in tokens:
        bare = token.removesuffix("'s")
        if bare[0].isupper() and len(bare) > 2 and bare.lower() not in lowercase_seen and bare not in names:
            names.append(bare)
    return " ".join(names[:limit]) or None


def run_transcribe(project_id: str, report: Report) -> None:
    from app.pipeline.stages.transcribe import _whisper_model

    project = store.load_project(project_id)
    if not project.audio_file:
        raise RuntimeError("Project has no audio yet")
    audio_path = store.project_dir(project_id) / project.audio_file

    report("loading speech model")
    model = _whisper_model()
    segments, info = model.transcribe(
        str(audio_path),
        word_timestamps=True,
        hotwords=_hotwords_from_script(project.script_text),
    )

    words: list[Word] = []
    for segment in segments:
        for w in segment.words or []:
            text = w.word.strip()
            if text:
                words.append(Word(text=text, start=round(w.start, 3), end=round(w.end, 3)))
        report(f"transcribing: {segment.end:.0f}s / {info.duration:.0f}s")

    if not words:
        raise RuntimeError("No speech found in the audio")

    store.save_words(project_id, words)

    def apply(p: store.Project) -> None:
        p.audio_duration = round(info.duration, 3)
        p.word_count = len(words)
        # a fresh transcript invalidates anything built on the old word indexes
        p.timeline = [Segment(id="seg_001", type="narration", audio_start=0.0, audio_end=p.audio_duration)]
        p.beats = []
        p.quotes = []

    store.update_project(project_id, apply)


# --- beats ------------------------------------------------------------------

MAX_UNIT_SECONDS = 8.0
MIN_SPLIT_PART_SECONDS = 2.0
SENTENCE_END = (".", "!", "?", "…", '."', '!"', '?"')
CLAUSE_END = (",", ";", ":", "—", "–")

BEATS_SCHEMA = {
    "type": "object",
    "properties": {
        "beats": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "first_unit": {"type": "integer", "description": "Index of the first unit in this beat."},
                    "last_unit": {"type": "integer", "description": "Index of the last unit in this beat (inclusive)."},
                    "summary": {
                        "type": "string",
                        "description": "Under 12 words: what this beat is about, naming the characters, places or events it refers to.",
                    },
                },
                "required": ["first_unit", "last_unit", "summary"],
            },
        }
    },
    "required": ["beats"],
}


def _split_long_unit(words: list[Word], first: int, last: int) -> list[tuple[int, int]]:
    """Splits a unit longer than MAX_UNIT_SECONDS at the clause boundary nearest
    its middle, recursively - a 15s sentence is too coarse to be one thing on screen."""
    duration = words[last].end - words[first].start
    if duration <= MAX_UNIT_SECONDS:
        return [(first, last)]
    middle = words[first].start + duration / 2
    candidates = [
        i
        for i in range(first, last)
        if words[i].text.endswith(CLAUSE_END)
        and words[i].end - words[first].start >= MIN_SPLIT_PART_SECONDS
        and words[last].end - words[i + 1].start >= MIN_SPLIT_PART_SECONDS
    ]
    if not candidates:
        return [(first, last)]
    cut = min(candidates, key=lambda i: abs(words[i].end - middle))
    return _split_long_unit(words, first, cut) + _split_long_unit(words, cut + 1, last)


def build_units(words: list[Word]) -> list[tuple[int, int]]:
    """Sentences (long ones split into clauses) as inclusive word-index ranges -
    the smallest pieces the LLM is allowed to group into beats."""
    units: list[tuple[int, int]] = []
    first = 0
    for i, word in enumerate(words):
        if word.text.endswith(SENTENCE_END) or i == len(words) - 1:
            units.extend(_split_long_unit(words, first, i))
            first = i + 1
    return units


def _repair_grouping(raw_beats: list[dict], unit_count: int) -> list[tuple[int, int, str]]:
    """Forces the LLM's grouping into a gapless, ordered cover of all units - a
    skipped or overlapping unit would otherwise be narration with nothing on screen."""
    groups: list[tuple[int, int, str]] = []
    expected = 0
    for beat in sorted(raw_beats, key=lambda b: b["first_unit"]):
        last = min(int(beat["last_unit"]), unit_count - 1)
        if last < expected:
            continue
        groups.append((expected, last, beat.get("summary", "")))
        expected = last + 1
        if expected >= unit_count:
            break
    if expected < unit_count:
        groups.append((expected, unit_count - 1, ""))
    return groups


def run_beats(project_id: str, report: Report) -> None:
    project = store.load_project(project_id)
    words = store.load_words(project_id)
    if not words:
        raise RuntimeError("Transcribe the audio first")

    universe = store.project_universe(project)
    world = f"{universe.name} ({universe.description})" if universe.description else universe.name
    units = build_units(words)
    unit_lines = []
    for i, (first, last) in enumerate(units):
        text = " ".join(w.text for w in words[first : last + 1])
        unit_lines.append(f"[{i}] ({words[last].end - words[first].start:.1f}s) {text}")

    prompt = (
        f"This is the narration of a video essay about {world}, split into numbered units "
        "(sentences or clauses) with their spoken duration. The video will be illustrated with footage "
        "from the show: each beat gets its own footage.\n\n"
        "Group consecutive units into beats. A beat is one visual idea - one thing the viewer should be "
        "looking at. Start a new beat when the subject, character, place or example changes. Most beats "
        "are 1-3 units and 3-12 seconds; never merge unrelated ideas just to make a beat longer, and "
        "keep a single unit as its own beat when it stands alone.\n\n"
        "Every unit must belong to exactly one beat, in order, with no gaps and no overlaps.\n\n"
        + (
            "The original script, for correct spelling of names (the units below come from speech "
            f"recognition and may misspell them):\n{project.script_text}\n\n"
            if project.script_text
            else ""
        )
        + "Units:\n"
        + "\n".join(unit_lines)
    )

    report(f"grouping {len(units)} units into beats")
    result, cost = run_claude_json(prompt, BEATS_SCHEMA, cwd=store.project_dir(project_id))
    groups = _repair_grouping(result["beats"], len(units))

    # A quote plays between two beats, so a beat must end wherever one is placed.
    live_quotes = [q for q in project.quotes if q.status != "rejected"]
    for quote in live_quotes:
        unit = next((i for i, (first, last) in enumerate(units) if first <= quote.after_word <= last), len(units) - 1)
        for g, (first_unit, last_unit, summary) in enumerate(groups):
            if first_unit <= unit < last_unit:
                groups[g : g + 1] = [(first_unit, unit, summary), (unit + 1, last_unit, summary)]
                break

    segment = project.timeline[0]
    beats: list[Beat] = []
    for n, (first_unit, last_unit, summary) in enumerate(groups):
        word_start = units[first_unit][0]
        word_end = units[last_unit][1]
        beats.append(
            Beat(
                id=f"beat_{n + 1:03d}",
                segment_id=segment.id,
                word_start=word_start,
                word_end=word_end,
                start=words[word_start].start,
                end=words[word_end].end,
                text=" ".join(w.text for w in words[word_start : word_end + 1]),
                summary=summary,
            )
        )

    # Beats tile the segment with no gaps: the pause after a beat's last word stays
    # with that beat, so there is always exactly one beat on screen.
    beats[0].start = segment.audio_start
    for current, following in zip(beats, beats[1:]):
        current.end = following.start
    beats[-1].end = segment.audio_end

    project.beats = beats
    for quote in live_quotes:
        quote.after_word, quote.at, quote.after_text = resolve_placement(project, words, quote.after_word)

    def apply(p: store.Project) -> None:
        p.beats = beats
        p.quotes = project.quotes
        if cost is not None:
            p.llm_cost_usd = round(p.llm_cost_usd + cost, 4)

    store.update_project(project_id, apply)


# --- queries ----------------------------------------------------------------

QUERY_WINDOW_BEATS = 40

QUERIES_SCHEMA = {
    "type": "object",
    "properties": {
        "beats": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "beat": {"type": "integer", "description": "The beat number as given."},
                    "shots": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "text_query": {
                                    "type": "string",
                                    "description": (
                                        "One sentence describing a scene from the show by what happens in it, "
                                        "using character and place names, e.g. 'Aang rides an air scooter around "
                                        "the Southern Air Temple courtyard'."
                                    ),
                                },
                                "shows": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": (
                                        "Which of the available shows/films to look in for this shot, by exact "
                                        "title: the one where this moment happens, or several when it could be "
                                        "found in more than one."
                                    ),
                                },
                                "visual_query": {
                                    "type": "string",
                                    "description": (
                                        "The same shot described purely by what is visible in the frame, with no "
                                        "names: appearance, clothing, colours, setting, framing, e.g. 'a bald boy "
                                        "with a blue arrow tattoo on his forehead riding a spinning ball of air'."
                                    ),
                                },
                            },
                            "required": ["text_query", "visual_query", "shows"],
                        },
                    },
                },
                "required": ["beat", "shots"],
            },
        }
    },
    "required": ["beats"],
}


def _make_slots(beat: Beat) -> list[Slot]:
    count = max(1, math.ceil((beat.end - beat.start) / MAX_CLIP_SECONDS - 1e-6))
    length = (beat.end - beat.start) / count
    return [
        Slot(start=round(beat.start + i * length, 3), end=round(beat.start + (i + 1) * length, 3))
        for i in range(count)
    ]


def run_queries(project_id: str, report: Report) -> None:
    """Asks the LLM what footage should be on screen for every clip slot. Slots a
    user has locked keep their queries and footage."""
    project = store.load_project(project_id)
    if not project.beats:
        raise RuntimeError("Build beats first")

    beats = project.beats
    for beat in beats:
        if not beat.slots:
            beat.slots = _make_slots(beat)

    from app.db.models import Source
    from app.db.session import SessionLocal
    from sqlalchemy import select

    # the project's shows (or its whole universe), limited to what is actually in the library
    db = SessionLocal()
    try:
        in_library = {t for t in db.execute(select(Source.show_title).distinct()).scalars() if t}
    finally:
        db.close()
    available = [show for show in store.project_shows(project) if show in in_library] or sorted(in_library)
    shows = ", ".join(available)
    total_cost = 0.0
    for window_start in range(0, len(beats), QUERY_WINDOW_BEATS):
        window = beats[window_start : window_start + QUERY_WINDOW_BEATS]
        lines = [
            f"[{window_start + i}] shots needed: {len(beat.slots)} | {beat.text}" for i, beat in enumerate(window)
        ]
        prompt = (
            f"A video essay is being illustrated with clips cut from {shows}. Below is its narration, split "
            "into numbered beats. For every beat, describe the shots from the show that should be on screen "
            "while it is spoken - exactly as many shots as 'shots needed' says.\n\n"
            "Each shot is found by searching an index of the show's scenes, so describe footage that really "
            "exists in the show, as concretely as you can: a specific moment, character, place or action. "
            "When the narration is abstract or general, choose concrete imagery from the show that illustrates "
            "the point. When it refers to something that was never shown on screen, choose the closest thing "
            "that was. Shots within one beat must show different things, and avoid repeating the same shot "
            "across beats.\n\n"
            "Give each shot two descriptions of the same footage: text_query (what happens, with names) and "
            "visual_query (only what is visible, no names - the visual index does not know who anyone is).\n\n"
            "The footage library holds these shows/films: "
            + "; ".join(f'"{title}"' for title in available)
            + ". For each shot also say which of them to search (shows), using the exact titles. Searching "
            "only where the moment actually happens gives much better matches than searching everything, so "
            "name one when you know it; name several when the same thing can be found in more than one, or "
            "when you are not sure.\n\n"
            "Beats:\n" + "\n".join(lines)
        )
        report(f"writing shot queries for beats {window_start + 1}-{window_start + len(window)} of {len(beats)}")
        result, cost = run_claude_json(prompt, QUERIES_SCHEMA, cwd=store.project_dir(project_id), timeout=600)
        total_cost += cost or 0.0

        for entry in result["beats"]:
            index = int(entry["beat"])
            if not window_start <= index < window_start + len(window) or not entry["shots"]:
                continue
            for i, slot in enumerate(beats[index].slots):
                if slot.locked:
                    continue
                # fewer shots returned than slots: reuse the last one rather than leave a slot unsearchable
                shot = entry["shots"][min(i, len(entry["shots"]) - 1)]
                slot.text_query = shot["text_query"].strip()
                slot.visual_query = shot["visual_query"].strip()
                # only titles that exist; a single-show library needs no restriction
                chosen = [title for title in shot.get("shows", []) if title in available]
                slot.shows = chosen if len(available) > 1 and len(chosen) < len(available) else []

    def apply(p: store.Project) -> None:
        p.beats = beats
        p.llm_cost_usd = round(p.llm_cost_usd + total_cost, 4)

    store.update_project(project_id, apply)


# --- select -----------------------------------------------------------------


def run_select(project_id: str, report: Report) -> None:
    """Searches the index for every unlocked slot, in narration order, so footage
    already used earlier in the video isn't picked again. Free and local."""
    from app.db.session import SessionLocal

    project = store.load_project(project_id)
    slots = [slot for beat in project.beats for slot in beat.slots]
    if not any(slot.text_query or slot.visual_query for slot in slots):
        raise RuntimeError("Write shot queries first")

    db = SessionLocal()
    try:
        report("loading search models")
        ctx = SearchContext(db, project)
        usage = Usage()
        for slot in slots:
            if slot.locked and slot.selected is not None:
                usage.add(slot.candidates[slot.selected])
        for n, slot in enumerate(slots):
            if slot.locked and slot.selected is not None:
                continue
            choose(slot, search_slot(ctx, slot), usage)
            report(f"finding footage {n + 1}/{len(slots)}")
    finally:
        db.close()

    def apply(p: store.Project) -> None:
        p.beats = project.beats

    store.update_project(project_id, apply)


# --- quotes -----------------------------------------------------------------


def run_quotes(project_id: str, report: Report) -> None:
    """Proposes quotes from the show to drop into the narration. Nothing is added
    to the video until the user approves a proposal."""
    from app.db.session import SessionLocal
    from app.editor.quotes import find_quotes

    project = store.load_project(project_id)
    words = store.load_words(project_id)
    if not words:
        raise RuntimeError("Transcribe the audio first")

    db = SessionLocal()
    try:
        cost = find_quotes(db, project, words, store.project_dir(project_id), report)
    finally:
        db.close()

    def apply(p: store.Project) -> None:
        p.quotes = project.quotes
        p.llm_cost_usd = round(p.llm_cost_usd + cost, 4)

    store.update_project(project_id, apply)


# --- background -------------------------------------------------------------


def run_background(project_id: str, report: Report) -> None:
    """Picks calm, themed scenery for behind the frame: one Claude call to decide
    what kind of place suits each beat, then a free local search."""
    from app.db.session import SessionLocal
    from app.editor.background import choose_backgrounds, write_background_queries

    project = store.load_project(project_id)
    if not project.beats:
        raise RuntimeError("Build beats first")

    report("deciding what scenery suits each beat")
    cost = write_background_queries(project, store.project_dir(project_id))
    db = SessionLocal()
    try:
        choose_backgrounds(db, project, report)
    finally:
        db.close()

    def apply(p: store.Project) -> None:
        for target, source in zip(p.beats, project.beats):
            target.background_query = source.background_query
            target.backgrounds = source.backgrounds
        p.llm_cost_usd = round(p.llm_cost_usd + cost, 4)

    store.update_project(project_id, apply)


STAGES: dict[str, Callable[[str, Report], None]] = {
    "transcribe": run_transcribe,
    "quotes": run_quotes,
    "beats": run_beats,
    "queries": run_queries,
    "select": run_select,
    "background": run_background,
    "render": render_project,
}
