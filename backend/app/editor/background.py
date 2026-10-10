"""Chooses the blurred background footage for the framed layout: calm scenery
shots for every beat, themed to what the narration is talking about.

The background must never pull attention - no faces, no action - and nobody
reviews it, so candidates are limited to scenes whose indexed keyframe looks like
empty scenery, and only the single shot around that keyframe is used (the rest of
the scene may well cut to a character).
"""

import numpy as np
from PIL import Image, ImageStat
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.config import data_path
from app.db.models import Scene, Shot, Source
from app.editor.bgtrack import FADE_FRAMES, FPS, clip_frames
from app.editor.llm import run_claude_json
from app.editor.store import Background, Project, project_shows, project_universe
from app.pipeline.stages.extract_keyframes import _OFFSETS_BY_COUNT
from app.services.embeddings import embed_text_for_visual_search
from app.services.universes import still_shows
from app.vectorstore.sqlite_vec_client import knn_visual

# Share of scenes, ranked by how much more they look like scenery than like
# characters, allowed as backgrounds. Checked by eye on Avatar: the top ~6% is
# almost all landscapes and establishing shots; below that characters and
# near-black frames start to appear.
SCENIC_TOP_SHARE = 0.06
MIN_SHOT_SECONDS = 2.5  # long enough to fade in, be seen, and fade out
SPARE_FACTOR, SPARE_SECONDS = 1.3, 6.0
MAX_SHOTS_PER_BEAT = 8
MIN_BRIGHTNESS = 40  # mean luminance of the keyframe, 0-255
KNN_K = 40

SCENERY_PROMPTS = [
    "a wide landscape with no people",
    "scenery of mountains, sea, sky or forest",
    "an establishing shot of a town or building seen from far away",
    "a calm empty view of nature",
]
DISTRACTION_PROMPTS = [
    "a close-up of a character's face",
    "people talking to each other",
    "a person standing in the centre of the frame",
    "a fight with fire and explosions",
    "a crowd of people",
]

BACKGROUND_SCHEMA = {
    "type": "object",
    "properties": {
        "beats": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "beat": {"type": "integer", "description": "The beat number as given."},
                    "background_query": {
                        "type": "string",
                        "description": (
                            "A calm, empty scenery shot described by what is visible, with no names and no "
                            "characters, e.g. 'white temple towers on a mountain peak above the clouds'."
                        ),
                    },
                },
                "required": ["beat", "background_query"],
            },
        }
    },
    "required": ["beats"],
}


def scenic_scene_ids(db: Session, shows: list[str]) -> list[int]:
    """Scene ids whose indexed keyframe looks like empty scenery. Computed from the
    visual vectors already in the index - no images are read."""
    query = "SELECT v.scene_id, v.embedding FROM scene_visual_vec v"
    params: dict = {}
    if shows:
        placeholders = ",".join(f":show{i}" for i in range(len(shows)))
        query += f" JOIN scenes s ON s.id = v.scene_id WHERE s.show_title IN ({placeholders})"
        params = {f"show{i}": show for i, show in enumerate(shows)}
    rows = db.execute(text(query), params).all()
    if not rows:
        return []

    ids = np.array([row[0] for row in rows])
    vectors = np.frombuffer(b"".join(row[1] for row in rows), dtype=np.float32).reshape(len(rows), -1)
    scenery = np.array([embed_text_for_visual_search(p) for p in SCENERY_PROMPTS], dtype=np.float32)
    distraction = np.array([embed_text_for_visual_search(p) for p in DISTRACTION_PROMPTS], dtype=np.float32)
    score = (vectors @ scenery.T).max(axis=1) - (vectors @ distraction.T).max(axis=1)

    keep = max(1, int(len(ids) * SCENIC_TOP_SHARE))
    return [int(i) for i in ids[np.argsort(-score)[:keep]]]


def _keyframe_shot(db: Session, scene: Scene) -> tuple[float, float] | None:
    """The shot containing the keyframe the scene was indexed by."""
    offsets = _OFFSETS_BY_COUNT.get(len(scene.keyframe_paths or []))
    if not offsets:
        return None
    moment = scene.start_time + (scene.end_time - scene.start_time) * offsets[0]
    shot = db.execute(
        select(Shot).where(Shot.source_id == scene.source_id, Shot.start_time <= moment, Shot.end_time > moment)
    ).scalars().first()
    if shot is None:
        return None
    start, end = max(shot.start_time, scene.start_time), min(shot.end_time, scene.end_time)
    # a few frames in from each cut, so a transition never bleeds into the background
    start, end = start + 0.15, end - 0.15
    return (start, end) if end - start >= MIN_SHOT_SECONDS else None


def _too_dark(scene: Scene) -> bool:
    """Night shots and fades score as 'empty scenery' but come out as a plain black
    background once blurred and dimmed."""
    try:
        with Image.open(data_path(scene.keyframe_paths[0])) as image:
            return ImageStat.Stat(image.convert("L").resize((64, 48))).mean[0] < MIN_BRIGHTNESS
    except OSError:
        return False


def write_background_queries(project: Project, cwd) -> float:
    lines = [f"[{i}] {beat.text}" for i, beat in enumerate(project.beats)]
    universe = project_universe(project)
    shows = ", ".join(project_shows(project)) or universe.name
    scenery = "".join(f"\n- {region}: {look}" for region, look in universe.scenery.items())
    prompt = (
        f"A video essay about {shows} shows each clip in a frame over a blurred background. The background is "
        "calm scenery from the show - landscapes, skies, seas, cities and buildings seen from afar - with no "
        "characters and no action, chosen to match what the narration is talking about at that moment.\n\n"
        "For every numbered beat of the narration below, describe the scenery that should be behind it. Match "
        "the place or mood being discussed."
        + (f" What the parts of this world look like:{scenery}\n\n" if scenery else " ")
        + "Keep neighbouring beats on the same subject in the same kind of place, but word each one a little "
        "differently so they are not the same shot. When a beat has no particular place, use neutral scenery "
        "such as clouds, sky or open water.\n\n"
        "Describe only what is visible. Do not use names of places or people - the image search does not "
        "know them.\n\n"
        + (
            "The author's wishes for the picture, as far as they concern the background:\n"
            + project.visual_notes.strip() + "\n\n"
            if project.visual_notes and project.visual_notes.strip()
            else ""
        )
        + "Beats:\n" + "\n".join(lines)
    )
    result, cost = run_claude_json(prompt, BACKGROUND_SCHEMA, cwd=cwd, timeout=600, task="background")
    for entry in result["beats"]:
        index = int(entry["beat"])
        if 0 <= index < len(project.beats):
            project.beats[index].background_query = entry["background_query"].strip()
    return cost or 0.0


def choose_backgrounds(db: Session, project: Project, report) -> None:
    report("finding calm scenery in the index")
    # scenery is moving footage: a show that may only be shown as stills gives none
    stills = still_shows()
    shows = [show for show in project_shows(project) if show not in stills]
    if not shows:
        raise RuntimeError("This project's shows may only be shown as stills - none can give moving scenery")
    scenic_ids = scenic_scene_ids(db, shows)
    if not scenic_ids:
        raise RuntimeError("No scenes with a visual index to use as backgrounds")

    used: set[int] = set()
    for n, beat in enumerate(project.beats):
        beat.backgrounds = []
        if not beat.background_query:
            continue
        # Shots play at normal speed, one fading into the next, so a beat needs enough
        # of them to last its whole length - with some to spare, because an approved
        # quote after the beat keeps this scenery on screen for longer.
        needed = (beat.end - beat.start) * SPARE_FACTOR + SPARE_SECONDS
        vector = embed_text_for_visual_search(beat.background_query)
        hits = knn_visual(db, vector, scenic_ids, k=KNN_K)
        covered = 0.0
        # unused scenes first; only if those run out, allow ones already used elsewhere
        for allow_reuse in (False, True):
            for scene_id, _ in hits:
                if covered >= needed or len(beat.backgrounds) >= MAX_SHOTS_PER_BEAT:
                    break
                if (scene_id in used) != allow_reuse:
                    continue
                row = db.execute(
                    select(Scene, Source).join(Source, Scene.source_id == Source.id).where(Scene.id == scene_id)
                ).first()
                if row is None:
                    continue
                scene, source = row
                if any(b.scene_id == scene.id for b in beat.backgrounds):
                    continue
                window = _keyframe_shot(db, scene)
                if window is None or _too_dark(scene):
                    continue
                clip = Background(
                    scene_id=scene.id,
                    source_id=source.id,
                    file_path=source.file_path,
                    clip_start=round(window[0], 3),
                    clip_end=round(window[1], 3),
                )
                beat.backgrounds.append(clip)
                used.add(scene_id)
                covered += (clip_frames(clip) - FADE_FRAMES) / FPS
        report(f"choosing backgrounds {n + 1}/{len(project.beats)}")
