"""Photos for shots about real people and things the shows never showed (see
services/commons.py). The shot-queries stage writes Slot.photo_query for such a
shot; here the photos are found, Claude looks at them and keeps those that really
show it, best first, and they become the slot's first candidates.

What was found and what Claude kept is cached per query in <project>/photos/, so
running Find footage again costs nothing for a query already looked at.
"""

import hashlib
import json
import logging
from pathlib import Path

from app.editor import store
from app.editor.llm import run_claude_json
from app.editor.store import Candidate, Slot
from app.services import commons

logger = logging.getLogger(__name__)

# how many photos are fetched and shown to Claude per query
LOOK_AT = 8
# a kept photo outranks any clip from the index (whose scores are z-scores, ~3-10)
PHOTO_SCORE = 50.0

VERIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "keep": {
            "type": "array",
            "items": {"type": "integer"},
            "description": "Numbers of the photos that really show what is wanted, best first. Empty when none does.",
        }
    },
    "required": ["keep"],
}


def _cache_path(project_id: str) -> Path:
    return store.project_dir(project_id) / "photos" / "searches.json"


def load_cache(project_id: str) -> dict:
    path = _cache_path(project_id)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_cache(project_id: str, cache: dict) -> None:
    path = _cache_path(project_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, indent=1, ensure_ascii=False), encoding="utf-8")


def _look(project_id: str, query: str, narration: str) -> tuple[list[dict], float]:
    """Finds photos for the query and asks Claude which of them to use."""
    folder = store.project_dir(project_id) / "photos"
    found = []
    for photo in commons.search(query, limit=LOOK_AT * 2):
        try:
            path = commons.download(photo, folder)
        except Exception as exc:  # one broken file should not lose the rest
            logger.warning("could not fetch %s: %s", photo.url, exc)
            continue
        found.append({"path": str(path), "title": photo.title, "credit": photo.credit, "page_url": photo.page_url,
                      "width": photo.width, "height": photo.height})
        if len(found) >= LOOK_AT:
            break
    if not found:
        return [], 0.0
    listing = "\n".join(f"{i + 1}. {item['path']}  ({item['title']})" for i, item in enumerate(found))
    prompt = (
        "A video narration needs a photo on screen while it says:\n"
        f'"{narration}"\n\n'
        f"It wants a photo of: {query}\n\n"
        "These are photos found on Wikimedia Commons. Use your Read tool to look at each one:\n"
        f"{listing}\n\n"
        "Say which of them really show that, best first. Leave out photos of something else that only shares "
        "the name, maps, diagrams, logos, scans of text, collages, and photos where it is too small or unclear "
        "to make out. For a person, a clear photo of their face is best. Keep none rather than a wrong one."
    )
    result, cost = run_claude_json(prompt, VERIFY_SCHEMA, cwd=folder, timeout=300, task="photos", tools=["Read"])
    kept = []
    for number in result.get("keep", []):
        if 1 <= int(number) <= len(found) and found[int(number) - 1] not in kept:
            kept.append(found[int(number) - 1])
    return kept, cost or 0.0


def photo_candidates(project_id: str, slot: Slot, narration: str, cache: dict) -> tuple[list[Candidate], float]:
    """The photos to offer for a slot with a photo_query, best first. cache is the
    project's search cache (see load_cache), updated in place."""
    query = slot.photo_query.strip()
    if not query:
        return [], 0.0
    cost = 0.0
    if query not in cache:
        try:
            kept, cost = _look(project_id, query, narration)
        except Exception as exc:  # no photo is not a reason to fail the whole stage
            logger.warning("photo search for %r failed: %s", query, exc)
            return [], 0.0
        cache[query] = kept
    duration = round(slot.end - slot.start, 3)
    candidates = []
    for rank, item in enumerate(cache[query]):
        # stored by name: the project folder can move (older caches hold full paths)
        name = Path(item["path"]).name
        if not (store.project_dir(project_id) / "photos" / name).exists():
            continue
        # a negative id of its own, so the same photo is not used twice (see footage.Usage)
        ident = -int(hashlib.sha1(item["title"].encode("utf-8")).hexdigest()[:7], 16)
        candidates.append(
            Candidate(
                scene_id=ident,
                source_id=ident,
                file_path=f"photos/{name}",
                kind="photo",
                image_path=f"photos/{name}",
                show_title="Photo",
                scene_start=0.0,
                scene_end=0.0,
                clip_start=0.0,
                clip_end=duration,
                description=item["title"].removeprefix("File:"),
                credit=item["credit"],
                score=PHOTO_SCORE - rank,
            )
        )
    return candidates, cost

