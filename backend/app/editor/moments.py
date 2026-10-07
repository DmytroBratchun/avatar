"""Specific moments. Most shots only illustrate - any of a dozen similar clips will
do, and the vector search finds them. Some shots are about one particular event
the narration names ("Iroh redirects the lightning"), which happens in one place in
the show. For those the shot-queries stage writes Slot.moment and the episodes it
expects it in (Slot.moment_episodes), and Find footage hands them to a Claude
session with tools (editor/finder_tool.py: search the index, read an episode,
look at a scene) that finds each moment its own way and answers with the scene
and the second. A moment it can't find is left to the ordinary search.

This runs before anything else and the scenes found are reserved, so an
illustrative shot earlier in the video can't use up the clip a specific one needs.
Answers are cached per moment in <project>/moments.json.
"""

import json
import logging
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Scene, Source, TranscriptLine
from app.editor import store
from app.editor.footage import _clip_window, count_slivers
from app.editor.store import Beat, Candidate, Slot
from app.services.runtime_settings import editor_llm_model

logger = logging.getLogger(__name__)

# a found moment outranks anything the vector search offers
MOMENT_SCORE = 40.0
DESCRIPTION_CHARS = 450
CACHE_FILE = "moments.json"
# moments per Claude session, and sessions at a time
PER_SESSION = 7
PARALLEL = 3
SESSION_TIMEOUT = 1200
MAX_TURNS = 60
TOOL = Path(__file__).resolve().parent / "finder_tool.py"

SCHEMA = {
    "type": "object",
    "properties": {
        "moments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "shot": {"type": "integer", "description": "The shot number as given."},
                    "scene": {"type": "integer", "description": "The id of the scene the moment is in (S123 -> 123)."},
                    "at": {
                        "type": "string",
                        "description": "Where in the episode the moment is best seen, as M:SS from its start (e.g. '19:28').",
                    },
                },
                "required": ["shot", "scene", "at"],
            },
        }
    },
    "required": ["moments"],
}


def _clock(seconds: float) -> str:
    return f"{int(seconds // 60)}:{int(seconds % 60):02d}"


def episode_text(db: Session, source: Source, label: str, start: float = 0.0, end: float = float("inf")) -> tuple[str, set[int]]:
    """An episode (or the part of it between start and end) as Claude reads it:
    every scene in order, with what happens in it and what is said, with times.
    Returns the text and the scene ids in it."""
    scenes = [
        s for s in db.execute(select(Scene).where(Scene.source_id == source.id).order_by(Scene.start_time)).scalars()
        if s.end_time > start and s.start_time < end
    ]
    lines = list(
        db.execute(select(TranscriptLine).where(TranscriptLine.source_id == source.id).order_by(TranscriptLine.start_time)).scalars()
    )
    out = [f"=== Episode id {source.id}: {label}"]
    i = 0
    for scene in scenes:
        about = (scene.context_description or scene.wiki_description or "").strip().replace("\n", " ")
        out.append(f"S{scene.id} [{_clock(scene.start_time)}-{_clock(scene.end_time)}] {about[:DESCRIPTION_CHARS]}")
        while i < len(lines) and lines[i].start_time < scene.end_time:
            line = lines[i]
            if line.start_time >= scene.start_time:
                out.append(f"    {_clock(line.start_time)} " + (f"{line.speaker}: " if line.speaker else "") + line.text.strip())
            i += 1
    return "\n".join(out), {scene.id for scene in scenes}


def _seconds(clock: str) -> float | None:
    """'19:28' (or '1:02:05') -> seconds; None when it isn't a time."""
    try:
        parts = [float(p) for p in str(clock).strip().split(":")]
    except ValueError:
        return None
    seconds = 0.0
    for part in parts:
        seconds = seconds * 60 + part
    return seconds


def _key(slot: Slot) -> str:
    return json.dumps([sorted(slot.moment_episodes), slot.moment.strip()])


def _candidate(db: Session, scene: Scene, source: Source, at: float, duration: float) -> Candidate:
    start, end = _clip_window(db, scene, source, duration, False, anchor=at)
    return Candidate(
        scene_id=scene.id,
        source_id=source.id,
        file_path=source.file_path,
        show_title=source.show_title,
        book=source.book,
        episode_number=source.episode_number,
        scene_start=scene.start_time,
        scene_end=scene.end_time,
        clip_start=start,
        clip_end=end,
        slivers=count_slivers(db, source.id, start, end),
        description=scene.context_description or scene.wiki_description,
        score=MOMENT_SCORE,
    )


def _session(asks: list[tuple[int, Beat, Slot, list[str]]], cwd: Path) -> tuple[dict, float]:
    """One Claude session with the finder tools for a few moments."""
    tool = f'"{Path(sys.executable).as_posix()}" "{TOOL.as_posix()}"'
    prompt = (
        "A video essay about an animated show is being illustrated with clips. The shots below are each about "
        "one particular moment of the show the narration names. Find, for each, the scene where that moment "
        "actually happens on screen and the second in the episode where it is best seen.\n\n"
        "You have a library of the show's episodes, split into scenes, and these commands (run them with Bash):\n"
        f'  {tool} search "<what happens>" [--show "<show>"] [--season N] [--episode N] [--mode text|visual|hybrid]\n'
        "      searches the scenes by meaning (descriptions written for them, and fan-wiki accounts); "
        "--season/--episode narrow it to a season or one episode; visual mode searches the pictures (describe "
        "what is seen, no names)\n"
        f"  {tool} episode <episode id> [--from M:SS --to M:SS]\n"
        "      reads an episode (or part of it) scene by scene: description, then dialogue with times\n"
        f"  {tool} scene <scene id>\n"
        "      one scene in full, with its dialogue and the scenes before and after\n\n"
        "Find each moment however works best - a search is quick; read an episode, or part of it, when a search "
        "is not enough. Dialogue comes from speech recognition and may contain errors; some scenes have no "
        "description. Take the scene where it happens on screen, not one where it is only talked about or "
        "recalled (unless the show only ever shows it as a flashback). The episodes given for a shot are a "
        "guess and may be wrong. When you can't find a moment, leave that shot out - a wrong scene is worse "
        "than none.\n\nShots:\n"
        + "\n".join(
            f"shot {n}: {slot.moment}\n  narration: \"{beat.text}\"\n  probably in: {'; '.join(labels) or 'unknown'}"
            for n, beat, slot, labels in asks
        )
    )
    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)
    model = editor_llm_model()
    result = subprocess.run(
        [
            "claude", "-p",
            *(["--model", model] if model else []),
            "--output-format", "json",
            "--json-schema", json.dumps(SCHEMA),
            "--tools", "Bash",
            "--allowedTools", f"Bash({tool}:*)",
            "--max-turns", str(MAX_TURNS),
            "--no-session-persistence",
        ],
        input=prompt, cwd=str(cwd), env=env, capture_output=True, text=True, encoding="utf-8", timeout=SESSION_TIMEOUT,
    )
    if result.returncode != 0:
        raise RuntimeError(f"claude CLI exited {result.returncode}: {(result.stderr.strip() or result.stdout.strip())[:400]}")
    payload = json.loads(result.stdout)
    if payload.get("is_error") or not payload.get("structured_output"):
        raise RuntimeError(f"claude CLI gave no answer: {str(payload.get('result'))[:400]}")
    return payload["structured_output"], payload.get("total_cost_usd") or 0.0


def find_moments(db: Session, project_id: str, shots: list[tuple[Beat, Slot]], report) -> tuple[dict[int, Candidate], float]:
    """{id(slot): its clip} for the shots whose moment was found; the rest are left
    to the vector search."""
    from app.editor.quotes import _episode_label

    folder = store.project_dir(project_id)
    cache_path = folder / CACHE_FILE
    try:
        cache: dict = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cache = {}
    universe = store.project_universe(store.load_project(project_id))

    def labels(slot: Slot) -> list[str]:
        out = []
        for source_id in slot.moment_episodes:
            source = db.get(Source, source_id)
            if source is not None:
                out.append(f"{_episode_label(source, universe)} (episode id {source_id})")
        return out

    cost = 0.0
    fresh = [(n, beat, slot, labels(slot)) for n, (beat, slot) in enumerate(shots) if _key(slot) not in cache]
    if fresh:
        groups = [fresh[i : i + PER_SESSION] for i in range(0, len(fresh), PER_SESSION)]
        report(f"finding {len(fresh)} specific moment{'s' if len(fresh) > 1 else ''}")
        with ThreadPoolExecutor(PARALLEL) as pool:
            futures = [pool.submit(_session, group, folder) for group in groups]
            for group, future in zip(groups, futures):
                try:
                    result, spent = future.result()
                except Exception as exc:  # these shots go to the ordinary search
                    logger.warning("finding moments: %s", exc)
                    continue
                cost += spent
                answered = {int(m.get("shot", -1)): m for m in result.get("moments", [])}
                for n, _, slot, _ in group:
                    found = answered.get(n)
                    scene = db.get(Scene, int(found["scene"])) if found else None
                    at = _seconds(found["at"]) if found else None
                    if scene is not None and (at is None or not scene.start_time - 5 <= at <= scene.end_time + 5):
                        at = (scene.start_time + scene.end_time) / 2  # a time outside the scene: its middle
                    cache[_key(slot)] = {"scene_id": scene.id, "at": at} if scene else {"missing": True}
        cache_path.write_text(json.dumps(cache, indent=1, ensure_ascii=False), encoding="utf-8")

    picked: dict[int, Candidate] = {}
    previous: tuple[Beat, Candidate] | None = None
    for beat, slot in shots:
        hit = cache.get(_key(slot)) or {}
        scene = db.get(Scene, hit["scene_id"]) if hit.get("scene_id") else None
        if scene is None:
            previous = None
            continue
        source = db.get(Source, scene.source_id)
        at = min(max(hit["at"], scene.start_time), scene.end_time)
        duration = slot.end - slot.start
        # the same moment for the next shot of a beat: carry on from where the last clip ended
        if previous and previous[0] is beat and previous[1].scene_id == scene.id:
            at = previous[1].clip_end + duration / 2
        candidate = _candidate(db, scene, source, at, duration)
        picked[id(slot)] = candidate
        previous = (beat, candidate)
    return picked, cost
