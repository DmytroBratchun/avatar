"""Picking comic pages for the shots sent to a comic. A vector search over page
descriptions finds pages about the same people and subject, but often not the
moment the narration is at - so, as for quotes, Claude reads the descriptions of
all the comic's indexed pages, in order, and picks the page for every shot of the
video that goes to that comic, in one call per comic. The vector search's pages
stay as alternatives in the slot.
"""

import logging
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Scene, Source
from app.editor.llm import run_claude_json
from app.editor.store import Beat, Candidate, Slot

logger = logging.getLogger(__name__)

# a picked page outranks any page the vector search found
PICKED_SCORE = 50.0
# page descriptions are cut to this many characters - enough to know the moment
DESCRIPTION_CHARS = 600

SCHEMA = {
    "type": "object",
    "properties": {
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
        }
    },
    "required": ["shots"],
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
        description=scene.context_description,
        text_score=round(text_score, 2) if text_score is not None else None,
        score=round(score, 2),
    )


def _pages(db: Session, title: str) -> list[tuple[Scene, Source]]:
    """The comic's described, indexed pages, part by part, in page order."""
    rows = db.execute(
        select(Scene, Source)
        .join(Source, Scene.source_id == Source.id)
        .where(Source.show_title == title, Source.kind == "comic", Scene.embedded_at.isnot(None))
        .order_by(Source.episode_number, Scene.start_time)
    ).all()
    return [(scene, source) for scene, source in rows if scene.keyframe_paths and scene.context_description]


def pick_pages(db: Session, title: str, shots: list[tuple[Beat, Slot]], cwd: Path) -> tuple[dict[int, Candidate | None], float]:
    """{index in shots: the page to show, or None when the comic has no page for it}."""
    pages = _pages(db, title)
    if not pages:
        return {i: None for i in range(len(shots))}, 0.0
    parts = len({source.id for _, source in pages}) > 1
    listing = []
    for number, (scene, source) in enumerate(pages):
        where = f"part {source.episode_number}, page {int(scene.start_time) + 1}" if parts else f"page {int(scene.start_time) + 1}"
        listing.append(f"[{number}] ({where}) {scene.context_description[:DESCRIPTION_CHARS]}")
    shot_lines = []
    previous_beat = None
    for number, (beat, slot) in enumerate(shots):
        if beat is not previous_beat:
            shot_lines.append(f"Narration: \"{beat.text}\"")
            previous_beat = beat
        shot_lines.append(f"  shot {number} ({slot.end - slot.start:.1f}s): {slot.text_query}")
    prompt = (
        f'A video essay shows pages of the comic "{title}" while its narration talks about the comic. Below are '
        "descriptions of the comic's pages, in order, and the shots of the video that show a page, each under "
        "the narration spoken over it, with a note of what the shot was meant to show - written without the "
        "comic at hand, so trust the narration over the note where they differ.\n\n"
        "Pick the page for every shot: the page where the moment the narration is talking about at that point "
        "actually happens - not one that only mentions it, recalls it or deals with the same people. When the "
        "narration walks through several events, follow them in order through the comic. Prefer a different "
        "page for each shot; the same page for two shots in a row is fine when the narration stays on one "
        "moment (they are then shown as one). Give -1 only when the comic has nothing close to it (for "
        "instance, when it happens in a part of the comic that is not listed).\n\n"
        "Pages:\n" + "\n".join(listing) + "\n\nShots:\n" + "\n".join(shot_lines)
    )
    result, cost = run_claude_json(prompt, SCHEMA, cwd=cwd, timeout=600)
    picked: dict[int, Candidate | None] = {i: None for i in range(len(shots))}
    for entry in result.get("shots", []):
        shot, page = int(entry.get("shot", -1)), int(entry.get("page", -1))
        if 0 <= shot < len(shots) and 0 <= page < len(pages):
            scene, source = pages[page]
            slot = shots[shot][1]
            picked[shot] = page_candidate(scene, source, slot.end - slot.start, PICKED_SCORE)
    return picked, cost or 0.0
