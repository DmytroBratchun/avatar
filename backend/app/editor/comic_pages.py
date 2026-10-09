"""Picking comic pages for the shots sent to a comic. A vector search over page
descriptions finds pages about the same people and subject, but often not the
moment the narration is at - so, as for quotes, Claude reads the descriptions of
all the comic's indexed pages, in order, and picks the page for every shot of the
video that goes to that comic, in one call per comic. The vector search's pages
stay as alternatives in the slot.
"""

import logging
from pathlib import Path

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.db.models import Scene, Source
from app.editor.llm import run_claude_json
from app.editor.store import Beat, Candidate, Slot

logger = logging.getLogger(__name__)

# a picked page outranks any page the vector search found
PICKED_SCORE = 50.0
# page descriptions are cut to this many characters - enough to know the moment
DESCRIPTION_CHARS = 600

# a page gets at least this long on screen
MIN_PAGE_SECONDS = 3.0

SCHEMA = {
    "type": "object",
    "properties": {
        "beats": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "beat": {"type": "integer", "description": "The beat number as given."},
                    "pages": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "page": {"type": "integer", "description": "The page's number in the list."},
                                "from_word": {"type": "integer", "description": "The number of the word it comes on at."},
                            },
                            "required": ["page", "from_word"],
                        },
                        "description": "The pages to show over this beat, in order; empty when the comic has nothing for it.",
                    },
                },
                "required": ["beat", "pages"],
            },
        },
        "shots": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "shot": {"type": "integer", "description": "The shot number as given."},
                    "page": {
                        "type": "integer",
                        "description": "The number of the page to show, as given in the list, or -1 when no page shows it.",
                    },
                },
                "required": ["shot", "page"],
            },
        },
    },
    "required": ["beats", "shots"],
}


def page_candidate(scene: Scene, source: Source, duration: float, score: float, text_score: float | None = None) -> Candidate:
    """A comic page as a slot candidate."""
    return Candidate(
        scene_id=scene.id,
        source_id=source.id,
        file_path=source.file_path,
        kind="comic",
        image_path=scene.keyframe_paths[0],
        show_title=source.show_title,
        book=source.book,
        episode_number=source.episode_number,
        scene_start=scene.start_time,
        scene_end=scene.end_time,
        clip_start=0.0,
        clip_end=round(duration, 3),
        description=scene.context_description or scene.wiki_description,
        text_score=round(text_score, 2) if text_score is not None else None,
        score=round(score, 2),
    )


def _about(scene: Scene) -> str:
    """What is known of the page: its caption, else the fan wiki's account of it."""
    return scene.context_description or scene.wiki_description or ""


def _pages(db: Session, title: str) -> list[tuple[Scene, Source]]:
    """The comic's indexed pages, part by part, in page order: those described by a
    caption, and those the fan wiki's transcript gives a text for."""
    rows = db.execute(
        select(Scene, Source)
        .join(Source, Scene.source_id == Source.id)
        .where(
            Source.show_title == title, Source.kind == "comic",
            or_(Scene.embedded_at.isnot(None), Scene.wiki_description.isnot(None)),
        )
        .order_by(Source.episode_number, Scene.start_time)
    ).all()
    return [(scene, source) for scene, source in rows if scene.keyframe_paths and _about(scene)]


def pick_pages(
    db: Session,
    title: str,
    beats: list[tuple[Beat, list]],
    shots: list[tuple[Beat, Slot]],
    cwd: Path,
) -> tuple[dict[int, list[tuple[Candidate, float, float]] | None], dict[int, Candidate | None], float]:
    """Pages of the comic for the video, from one reading of the comic.

    beats: beats told wholly from this comic, each with its narration words (with
    times). For these Claude decides how many pages to show and on which word each
    comes on - it knows only here how the comic tells the moment. Returns, per
    index in beats, the pages in order as (page, start, end) on the narration's
    clock, or None when the comic has nothing for the beat.

    shots: single shots sent to the comic inside other beats; each gets one page
    (or None), by index in shots."""
    pages = _pages(db, title)
    if not pages:
        return {i: None for i in range(len(beats))}, {i: None for i in range(len(shots))}, 0.0
    parts = len({source.id for _, source in pages}) > 1
    listing = []
    for number, (scene, source) in enumerate(pages):
        where = f"part {source.episode_number}, page {int(scene.start_time) + 1}" if parts else f"page {int(scene.start_time) + 1}"
        listing.append(f"[{number}] ({where}) {_about(scene)[:DESCRIPTION_CHARS]}")

    beat_lines = []
    for number, (beat, words) in enumerate(beats):
        notes = "; ".join(dict.fromkeys(slot.text_query for slot in beat.slots if slot.text_query))
        beat_lines.append(
            f"beat {number} ({beat.end - beat.start:.1f}s; the editor's note: {notes})\n  "
            + " ".join(f"{i}|{word.text}" for i, word in enumerate(words))
        )
    shot_lines = []
    previous_beat = None
    for number, (beat, slot) in enumerate(shots):
        if beat is not previous_beat:
            shot_lines.append(f"Narration: \"{beat.text}\"")
            previous_beat = beat
        shot_lines.append(f"  shot {number} ({slot.end - slot.start:.1f}s): {slot.text_query}")

    prompt = (
        f'A video essay shows pages of the comic "{title}" while its narration talks about the comic. Below are '
        "descriptions of the comic's pages, in order, and the places in the video that show them. A page is "
        "shown whole, scrolling slowly from its top to its bottom for as long as it is on screen. The editor's "
        "notes were written without the comic at hand - trust the narration over them where they differ.\n\n"
        "Take the page where the moment the narration is talking about actually happens - not one that only "
        "mentions it, recalls it or deals with the same people. When the narration walks through several "
        "events, follow them in order through the comic.\n\n"
        + (
            "BEATS are stretches of narration shown entirely with this comic. Each is given with its length and "
            "its words numbered. For each, decide which pages to show and when: give them in order, each with "
            "from_word - the number of the word at which that page comes on (0 for the first). One page for the "
            "whole beat when the narration stays on one moment, or when the comic tells it on one page; a new "
            f"page where the narration moves to something a different page shows. A page needs at least "
            f"{MIN_PAGE_SECONDS:.0f} seconds to be read (the narrator speaks about 2.5 words a second), and "
            "longer is fine - don't change pages more often than the story does. Give no pages for a beat when "
            "the comic has nothing close to it (for instance, it happens in a part that is not listed).\n\n"
            if beats
            else ""
        )
        + (
            "SHOTS are single places of a set length inside other narration. Pick one page for each, or -1 when "
            "the comic has nothing close to it.\n\n"
            if shots
            else ""
        )
        + "Pages:\n" + "\n".join(listing)
        + ("\n\nBeats:\n" + "\n".join(beat_lines) if beats else "")
        + ("\n\nShots:\n" + "\n".join(shot_lines) if shots else "")
    )
    result, cost = run_claude_json(prompt, SCHEMA, cwd=cwd, timeout=600)

    timed: dict[int, list[tuple[Candidate, float, float]] | None] = {i: None for i in range(len(beats))}
    for entry in result.get("beats", []):
        number = int(entry.get("beat", -1))
        if not 0 <= number < len(beats):
            continue
        beat, words = beats[number]
        starts: list[tuple[int, float]] = []  # (page, time it comes on)
        for item in sorted(entry.get("pages", []), key=lambda item: int(item.get("from_word", 0))):
            page, word = int(item.get("page", -1)), int(item.get("from_word", 0))
            if not 0 <= page < len(pages):
                continue
            at = beat.start if not starts or not words else words[min(max(word, 0), len(words) - 1)].start
            at = min(max(at, beat.start), beat.end)
            if starts and (at - starts[-1][1] < MIN_PAGE_SECONDS or page == starts[-1][0]):
                continue  # too soon after the last page (or the same page again): the last one stays
            starts.append((page, at))
        # the last page needs its time too
        while len(starts) > 1 and beat.end - starts[-1][1] < MIN_PAGE_SECONDS:
            starts.pop()
        if not starts:
            continue
        out = []
        for i, (page, at) in enumerate(starts):
            end = starts[i + 1][1] if i + 1 < len(starts) else beat.end
            scene, source = pages[page]
            out.append((page_candidate(scene, source, end - at, PICKED_SCORE), round(at, 3), round(end, 3)))
        timed[number] = out

    picked: dict[int, Candidate | None] = {i: None for i in range(len(shots))}
    for entry in result.get("shots", []):
        shot, page = int(entry.get("shot", -1)), int(entry.get("page", -1))
        if 0 <= shot < len(shots) and 0 <= page < len(pages):
            scene, source = pages[page]
            slot = shots[shot][1]
            picked[shot] = page_candidate(scene, source, slot.end - slot.start, PICKED_SCORE)
    return timed, picked, cost or 0.0
