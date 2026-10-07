"""Editor stages. Each takes a project id and a report(detail) callback, reads and
writes the project through app.editor.store, and can be re-run at any time."""

import logging
import math
import re
from collections.abc import Callable
from pathlib import Path

from app.editor import store
from app.editor.footage import CANDIDATES_PER_SLOT, SearchContext, Usage, choose, end_on_a_cut, search_slot
from app.editor.llm import run_claude_json
from app.editor.quotes import resolve_placement
from app.editor.render import render_project
from app.editor.store import Beat, Candidate, Segment, Slot, Word
from app.editor.voiceover import run_voiceover

Report = Callable[[str], None]

logger = logging.getLogger(__name__)

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
                                "moment": {
                                    "type": "string",
                                    "description": (
                                        "Empty for most shots. Only when the narration at this shot names one "
                                        "particular event that happens in one place in the shows (e.g. 'Iroh's "
                                        "army breaks through the outer wall of Ba Sing Se'): that event, in one "
                                        "sentence with names."
                                    ),
                                },
                                "episodes": {
                                    "type": "array",
                                    "items": {"type": "integer"},
                                    "description": "With a moment: the 1-3 episodes it happens in, by number from the episode list.",
                                },
                                "photo": {
                                    "type": "string",
                                    "description": (
                                        "Usually empty. For a shot about a real person, place or thing outside the "
                                        "story's world that the shows never show - the people who made them, voice "
                                        "actors, real people and places the story drew on: what to search Wikimedia "
                                        "Commons for to get a photo of it, as its usual name, e.g. 'Mako Iwamatsu'."
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


def _slot_count(beat: Beat, max_clip_seconds: float) -> int:
    return max(1, math.ceil((beat.end - beat.start) / max_clip_seconds - 1e-6))


# a comic page gets at least this long on screen, however many pages a beat walks through
MIN_PAGE_SECONDS = 3.0


def _make_slots(beat: Beat, max_clip_seconds: float) -> list[Slot]:
    return _even_slots(beat, _slot_count(beat, max_clip_seconds))


def _even_slots(beat: Beat, count: int) -> list[Slot]:
    length = (beat.end - beat.start) / count
    return [
        Slot(start=round(beat.start + i * length, 3), end=round(beat.start + (i + 1) * length, 3))
        for i in range(count)
    ]


def _episode_list(db, project: store.Project, in_library: set[str]) -> tuple[str, set[int]]:
    """The project's episodes, one per line with the number to name them by - for
    the shot queries to say where a specific moment happens."""
    from sqlalchemy import select

    from app.db.models import Source
    from app.editor.quotes import _episode_label

    universe = store.project_universe(project)
    shows = [show for show in store.project_shows(project) if show in in_library] or sorted(in_library)
    sources = db.execute(
        select(Source).where(Source.show_title.in_(shows), Source.kind == "video")
        .order_by(Source.show_title, Source.book, Source.episode_number)
    ).scalars().all()
    return "\n".join(f"[{s.id}] {_episode_label(s, universe)}" for s in sources), {s.id for s in sources}


def _has_indexed_pages(db, title: str) -> bool:
    from sqlalchemy import select

    from app.db.models import Scene

    return db.execute(
        select(Scene.id).where(Scene.show_title == title, Scene.embedded_at.isnot(None)).limit(1)
    ).first() is not None


REQUERY_SCHEMA = {
    "type": "object",
    "properties": {
        "shots": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "shot": {"type": "integer", "description": "The shot number as given."},
                    "text_query": {"type": "string", "description": "A scene from the shows by what happens in it, with names."},
                    "visual_query": {"type": "string", "description": "The same shot by what is visible only, no names."},
                    "shows": {"type": "array", "items": {"type": "string"}, "description": "Which shows to search, by exact title."},
                },
                "required": ["shot", "text_query", "visual_query", "shows"],
            },
        }
    },
    "required": ["shots"],
}


def _requery(project: store.Project, failed: list[tuple[Beat, Slot]], shows: list[str], cwd: Path) -> float:
    """New queries for shots whose search found nothing - usually a shot sent to a
    comic with no pages indexed, or one too specific for the index. One Claude call
    for all of them; the queries are written into the slots."""
    lines = []
    for number, (beat, slot) in enumerate(failed):
        lines.append(
            f"[{number}] narration: {beat.text}\n"
            f"    the query that found nothing: {slot.text_query!r} in {', '.join(slot.shows) or 'all shows'}"
        )
    prompt = (
        "A video essay is illustrated with clips from these shows: " + "; ".join(f'"{s}"' for s in shows) + ". "
        "For the shots below the search of the footage index found nothing - most often because the query "
        "points to a comic or a moment that is not in the index. Write a new query for each, for footage that "
        "surely exists in the shows and suits what the narration says: the same characters, the same place, "
        "or the same feeling, when the exact moment was never shown. text_query describes the scene with "
        "names, visual_query the same frame by what is visible only, with no names; shows names where to "
        "search, by exact title.\n\n" + "\n".join(lines)
    )
    result, cost = run_claude_json(prompt, REQUERY_SCHEMA, cwd=cwd, timeout=300)
    for entry in result.get("shots", []):
        number = int(entry.get("shot", -1))
        if not 0 <= number < len(failed):
            continue
        slot = failed[number][1]
        slot.text_query = entry["text_query"].strip()
        slot.visual_query = entry["visual_query"].strip()
        chosen = [title for title in entry.get("shows", []) if title in shows]
        slot.shows = chosen if len(chosen) < len(shows) else []
    return cost or 0.0


def _comic_cast(db, comics: list[str], most: int = 12) -> dict[str, list[str]]:
    """Who appears in each comic, most pages first, from the described pages' tags -
    so the shot-queries call can tell a comic's own characters (often from comics too
    new for the model to know) from the shows'. Names on a single page are left out."""
    from collections import Counter

    from sqlalchemy import select

    from app.db.models import Scene

    cast: dict[str, list[str]] = {}
    for title in comics:
        counts: Counter[str] = Counter()
        for tags in db.execute(select(Scene.character_tags).where(Scene.show_title == title, Scene.caption_status == "done")).scalars():
            counts.update(tags or [])
        cast[title] = [name for name, n in counts.most_common(most) if n > 1]
    return cast


def _drop_locks_if_asked(project_id: str, stage: str) -> None:
    """When the stage was started with "re-pick locked clips too", unlocks every
    slot before it reads the project (see run_stage's unlock)."""

    def apply(p: store.Project) -> None:
        if stage in p.unlock_before:
            p.unlock_before.remove(stage)
            for beat in p.beats:
                for slot in beat.slots:
                    slot.locked = False

    store.update_project(project_id, apply)


def run_queries(project_id: str, report: Report) -> None:
    """Asks the LLM what footage should be on screen for every clip slot. Slots a
    user has locked keep their queries and footage."""
    _drop_locks_if_asked(project_id, "queries")
    project = store.load_project(project_id)
    if not project.beats:
        raise RuntimeError("Build beats first")

    beats = project.beats
    limit = project.settings.max_clip_seconds
    for beat in beats:
        # (Re)build the clips of a beat when it has none, or when the clip length
        # limit changed since they were made - unless the user has locked one.
        stale = len(beat.slots) != _slot_count(beat, limit) and not any(slot.locked for slot in beat.slots)
        if not beat.slots or stale:
            beat.slots = _make_slots(beat, limit)

    from app.db.models import Source
    from app.db.session import SessionLocal
    from sqlalchemy import select

    # the project's shows (or its whole universe), limited to what is actually in the library
    db = SessionLocal()
    try:
        in_library = {t for t in db.execute(select(Source.show_title).distinct()).scalars() if t}
        comics = [title for title in store.project_comics(project) if title in in_library]
        # a comic with no page in the index yet can't be shown - its shots go to the shows
        unindexed = [title for title in comics if not _has_indexed_pages(db, title)]
        comics = [title for title in comics if title not in unindexed]
        cast = _comic_cast(db, comics)
        episode_list, episode_ids = _episode_list(db, project, in_library - set(comics))
    finally:
        db.close()
    available = [show for show in store.project_shows(project) if show in in_library] or sorted(in_library - set(comics))
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
            "while it is spoken - exactly as many shots as 'shots needed' says (a beat told from a comic is "
            "the exception, see below).\n\n"
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
            + (
                "The library also holds these comics, page by page (with who appears on their pages, most "
                "often first):\n"
                + "\n".join(f'- "{title}": {", ".join(cast.get(title, [])) or "no pages described yet"}' for title in comics)
                + "\nWhen the narration at a shot is about something that happens in one of these comics - "
                "and not in the shows - give that comic's title in shows, alone: a page of the comic is then "
                "shown in place of a clip. A character who appears in a comic but never in the shows marks the "
                "narration around him as the comic's story, even when it also names places or events of the "
                "shows: a shot introducing such a character, or telling what he does, goes to the comic. For "
                "such a shot text_query describes what happens on the page, with names, and visual_query is not "
                "used. Do not send a shot to a comic merely because the comic also touches on the subject; "
                "otherwise the shows come first.\n"
                "A comic's shots are its pages, and they are not bound by 'shots needed' or any length: when a "
                "whole beat goes to a comic, give one shot per page the narration there walks through - a single "
                "page when it dwells on one moment, several when it moves through a sequence of events, in the "
                "order they happen. The beat's time is shared between them, however long that makes each page "
                f"(but no less than {MIN_PAGE_SECONDS:.0f}s a page).\n\n"
                if comics
                else ""
            )
            + (
                "These comics are not in the library's index yet, so nothing from them can be shown: "
                + "; ".join(f'"{title}"' for title in unindexed)
                + ". Where the narration (or the author's notes) points to one of them, choose the closest "
                "footage from the shows instead - the same characters, place or feeling.\n\n"
                if unindexed
                else ""
            )
            + (
                "Most shots only illustrate what the narration says - any fitting clip will do. Some are about "
                "one particular event the narration explicitly names, which happens in one place in the shows "
                "(a battle, a duel, a confession, a death, a specific scene): for those also give moment - the "
                "event, in one sentence - and the 1-3 episodes it happens in, by number from the list below. "
                "The episodes are then read to find the exact scene. Only explicit events: a general statement, "
                "a habit, a mood or a character's nature is not a moment, and neither is a shot for a comic or "
                "a photo.\n\nEpisodes:\n" + episode_list + "\n\n"
                if episode_list
                else ""
            )
            + (
                "When the narration at a shot is about the real world rather than the story - the people who "
                "made the shows, voice actors, real people, places, art or events the story drew on - and the "
                "shows have nothing that shows it, put what to photograph in photo: a photo from Wikimedia "
                "Commons is then shown in place of a clip. Still write text_query and visual_query for the "
                "closest footage, in case no photo is found. Leave photo empty for everything else.\n\n"
            )
            + (
                "The author's notes on where the material comes from - trust them over your own memory, "
                "and send a shot to the source they name:\n" + project.footage_notes.strip() + "\n\n"
                if project.footage_notes and project.footage_notes.strip()
                else ""
            )
            + "Beats:\n" + "\n".join(lines)
        )
        report(f"writing shot queries for beats {window_start + 1}-{window_start + len(window)} of {len(beats)}")
        result, cost = run_claude_json(prompt, QUERIES_SCHEMA, cwd=store.project_dir(project_id), timeout=600)
        total_cost += cost or 0.0

        for entry in result["beats"]:
            index = int(entry["beat"])
            if not window_start <= index < window_start + len(window) or not entry["shots"]:
                continue
            beat = beats[index]
            pages = [shot for shot in entry["shots"] if any(title in comics for title in shot.get("shows", []))]
            if pages and len(pages) == len(entry["shots"]) and not any(slot.locked for slot in beat.slots):
                # a beat told from a comic: as many pages as the narration walks through,
                # sharing the beat evenly - not held to the clip-length limit
                count = max(1, min(len(pages), int((beat.end - beat.start) // MIN_PAGE_SECONDS)))
                if count != len(beat.slots):
                    beat.slots = _even_slots(beat, count)
            for i, slot in enumerate(beat.slots):
                if slot.locked:
                    continue
                # fewer shots returned than slots: reuse the last one rather than leave a slot unsearchable
                shot = entry["shots"][min(i, len(entry["shots"]) - 1)]
                slot.text_query = shot["text_query"].strip()
                slot.visual_query = shot["visual_query"].strip()
                slot.photo_query = (shot.get("photo") or "").strip()
                slot.moment = (shot.get("moment") or "").strip()
                slot.moment_episodes = (
                    [int(e) for e in shot.get("episodes", []) if int(e) in episode_ids][:3] if slot.moment else []
                )
                if not slot.moment_episodes:
                    slot.moment = ""
                # only titles that exist; a single-show library needs no restriction
                chosen = [title for title in shot.get("shows", []) if title in available]
                in_comics = [title for title in shot.get("shows", []) if title in comics]
                if in_comics:
                    slot.shows = in_comics  # a shot for a comic is searched in the comic only
                else:
                    slot.shows = chosen if len(available) > 1 and len(chosen) < len(available) else []

    def apply(p: store.Project) -> None:
        p.beats = beats
        p.llm_cost_usd = round(p.llm_cost_usd + total_cost, 4)

    store.update_project(project_id, apply)


# --- select -----------------------------------------------------------------


def run_select(project_id: str, report: Report) -> None:
    """Searches the index for every unlocked slot, in narration order, so footage
    already used earlier in the video isn't picked again. Free and local - except
    for shots that want a photo, which are searched on Wikimedia Commons and looked
    at by Claude (once per photo query; see editor/photos.py)."""
    from app.db.session import SessionLocal
    from app.editor import photos
    from app.editor.comic_pages import pick_pages
    from app.editor.moments import find_moments

    _drop_locks_if_asked(project_id, "select")
    project = store.load_project(project_id)
    slots = [slot for beat in project.beats for slot in beat.slots]
    if not any(slot.text_query or slot.visual_query for slot in slots):
        raise RuntimeError("Write shot queries first")

    db = SessionLocal()
    try:
        report("loading search models")
        ctx = SearchContext(db, project)
        usage = Usage()
        photo_cache = photos.load_cache(project_id)
        llm_cost = 0.0
        for slot in slots:
            if slot.locked and slot.selected is not None:
                usage.add(slot.candidates[slot.selected])

        # every beat from the even split (see the loop below) before anything is cut to a slot's length
        limit = project.settings.max_clip_seconds
        for beat in project.beats:
            if beat.slots and not any(slot.locked for slot in beat.slots) and len(beat.slots) == _slot_count(beat, limit):
                for slot, fresh in zip(beat.slots, _make_slots(beat, limit)):
                    slot.start, slot.end = fresh.start, fresh.end

        # Specific moments first, from reading their episodes, and reserved: an
        # illustrative shot earlier in the video must not use up a clip one of them needs.
        specific = [
            (beat, slot) for beat in project.beats for slot in beat.slots
            if slot.moment and slot.moment_episodes and not (slot.locked and slot.selected is not None)
        ]
        moments_for: dict[int, Candidate] = {}
        if specific:
            moments_for, cost = find_moments(db, project_id, specific, report)
            llm_cost += cost
            for candidate in moments_for.values():
                usage.add(candidate)

        # Shots sent to a comic: Claude reads the comic's pages and picks the page for
        # each (one call per comic), rather than trusting the vector search alone.
        pages_for: dict[int, Candidate | None] = {}  # id(slot) -> picked page
        by_comic: dict[str, list[tuple[Beat, Slot]]] = {}
        for beat in project.beats:
            for slot in beat.slots:
                if not (slot.locked and slot.selected is not None) and len(slot.shows) == 1 and slot.shows[0] in ctx.comics:
                    by_comic.setdefault(slot.shows[0], []).append((beat, slot))
        for title, shots in by_comic.items():
            report(f"picking pages of {title} for {len(shots)} shot{'s' if len(shots) > 1 else ''}")
            try:
                picked, cost = pick_pages(db, title, shots, store.project_dir(project_id))
            except Exception as exc:  # the vector search's pages are still there
                logger.warning("picking pages of %s: %s", title, exc)
                continue
            llm_cost += cost
            for i, (_, slot) in enumerate(shots):
                pages_for[id(slot)] = picked.get(i)
        # the same page for two shots in a row of one beat: one shot, the page shown longer
        for beat in project.beats:
            merged: list[Slot] = []
            for slot in beat.slots:
                page = pages_for.get(id(slot))
                before = pages_for.get(id(merged[-1])) if merged else None
                if page is not None and before is not None and page.scene_id == before.scene_id and not merged[-1].locked:
                    merged[-1].end = slot.end
                    before.clip_end = round(merged[-1].end - merged[-1].start, 3)
                else:
                    merged.append(slot)
            beat.slots = merged
        slots = [slot for beat in project.beats for slot in beat.slots]
        n = 0
        for beat in project.beats:
            # Start every run from the even split, so boundaries moved by an earlier
            # run don't drift further - unless the user has locked a clip in this beat,
            # whose length then has to stay as it is.
            limit = project.settings.max_clip_seconds
            # the number of clips is decided when shot queries are written; if the
            # limit changed since, leave the beat as it is until they are re-run
            flexible = not any(slot.locked for slot in beat.slots) and len(beat.slots) == _slot_count(beat, limit)
            if flexible and beat.slots:
                even = _make_slots(beat, limit)
                for slot, fresh in zip(beat.slots, even):
                    slot.start, slot.end = fresh.start, fresh.end
            for i, slot in enumerate(beat.slots):
                n += 1
                if slot.locked and slot.selected is not None:
                    continue
                found = search_slot(ctx, slot)
                if id(slot) in moments_for:
                    moment = moments_for[id(slot)]
                    slot.candidates = [moment] + [c for c in found if c.scene_id != moment.scene_id][: CANDIDATES_PER_SLOT - 1]
                    slot.selected = 0
                    report(f"finding footage {n}/{len(slots)}")
                    continue
                if id(slot) in pages_for:
                    page = pages_for[id(slot)]
                    if page is None:
                        # no page shows it: left empty, so it gets new queries for the shows below
                        slot.candidates, slot.selected = [], None
                    else:
                        # Claude's page, even if shown earlier - the narration may come back to a moment
                        page.clip_end = round(slot.end - slot.start, 3)
                        slot.candidates = [page] + [c for c in found if c.scene_id != page.scene_id][: CANDIDATES_PER_SLOT - 1]
                        slot.selected = 0
                        usage.add(page)
                    report(f"finding footage {n}/{len(slots)}")
                    continue
                if slot.photo_query.strip():
                    report(f"finding footage {n}/{len(slots)} - looking for a photo of {slot.photo_query}")
                    pictures, cost = photos.photo_candidates(project_id, slot, beat.text, photo_cache)
                    photos.save_cache(project_id, photo_cache)
                    llm_cost += cost
                    found = pictures + found
                choose(slot, found, usage)
                # let this clip end on a cut by trading a little time with the next one
                if flexible and i + 1 < len(beat.slots):
                    end_on_a_cut(db, slot, beat.slots[i + 1], limit)
                report(f"finding footage {n}/{len(slots)}")

        # Shots that found nothing get new queries, written for the shows, and are
        # searched again; whatever still finds nothing is searched by its narration.
        failed = [(beat, slot) for beat in project.beats for slot in beat.slots if not slot.candidates and not slot.locked]
        if failed:
            report(f"writing new queries for {len(failed)} shot{'s' if len(failed) > 1 else ''} that found nothing")
            shows = ctx.project_shows or sorted(set(store.project_universe(project).shows))
            try:
                llm_cost += _requery(project, failed, shows, store.project_dir(project_id))
            except Exception as exc:  # the fallback below still gives them footage
                logger.warning("re-querying failed shots: %s", exc)
            for beat, slot in failed:
                found = search_slot(ctx, slot)
                if not found:
                    slot.shows = []
                    slot.text_query = beat.text
                    found = search_slot(ctx, slot)
                choose(slot, found, usage)
    finally:
        db.close()

    def apply(p: store.Project) -> None:
        p.beats = project.beats
        p.llm_cost_usd = round(p.llm_cost_usd + llm_cost, 4)

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


def run_music(project_id: str, report: Report) -> None:
    """Music under the narration: one Claude call divides the beats into blocks of
    one mood, then tracks are composed for every block through Lumean."""
    from app.editor.music import compose

    compose(project_id, report)


STAGES: dict[str, Callable[[str, Report], None]] = {
    "voiceover": run_voiceover,
    "transcribe": run_transcribe,
    "quotes": run_quotes,
    "beats": run_beats,
    "queries": run_queries,
    "select": run_select,
    "background": run_background,
    "music": run_music,
    "render": render_project,
}
