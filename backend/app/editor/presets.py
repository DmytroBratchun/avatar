"""Presets: named sets of what a new video starts with - a channel, a series, a
language. A preset holds the universe and shows, the look, the voice, the visual
wishes, the footage prompt, the instructions for "Optimize for voice" and whether
to run to the end. A new video copies them from the preset it is made from, so
editing a preset later leaves existing videos alone; the project remembers its
preset, and the "save to preset" buttons on a project write back to it.

Kept in <data>/presets.json. There is always a "default" preset; on first use it
is made from the single defaults settings.json held before presets existed.
"""

import json
import re
import threading

from pydantic import BaseModel, Field

from app.config import DATA_DIR
from app.services.runtime_settings import _read_raw

PRESETS_PATH = DATA_DIR / "presets.json"
DEFAULT_ID = "default"
_lock = threading.Lock()


class Preset(BaseModel):
    id: str
    name: str
    universe: str = ""  # empty: chosen for each video
    shows: list[str] = Field(default_factory=list)  # empty: all of the universe's shows
    # a RenderSettings and a voiceover.VoiceSettings as dicts: validated where they
    # are used, so a field added there later just falls back to its default
    look: dict = Field(default_factory=dict)
    voice: dict = Field(default_factory=dict)
    visual_notes: str = ""
    footage_notes: str = ""
    voice_instructions: str = ""  # what "Optimize for voice" is told about the delivery
    run_all: bool = True


def _from_old_settings() -> Preset:
    raw = _read_raw()
    return Preset(
        id=DEFAULT_ID,
        name="Default",
        look=raw.get("editor_default_look") or {},
        voice=raw.get("editor_default_voice") or {},
        visual_notes=(raw.get("editor_default_visual_notes") or "").strip(),
    )


def _load() -> list[Preset]:
    if PRESETS_PATH.is_file():
        try:
            presets = [Preset.model_validate(p) for p in json.loads(PRESETS_PATH.read_text(encoding="utf-8"))]
        except ValueError:
            presets = []
    else:
        presets = []
    if not any(p.id == DEFAULT_ID for p in presets):
        presets.insert(0, _from_old_settings())
        _save(presets)
    return presets


def _save(presets: list[Preset]) -> None:
    tmp = PRESETS_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps([p.model_dump() for p in presets], indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(PRESETS_PATH)


def all_presets() -> list[Preset]:
    with _lock:
        return _load()


def get(preset_id: str) -> Preset:
    """The preset, or the default one when there is no such preset (a project made
    before presets, or from one since deleted)."""
    presets = all_presets()
    return next((p for p in presets if p.id == preset_id), None) or next(p for p in presets if p.id == DEFAULT_ID)


def create(name: str, copy_from: str = DEFAULT_ID) -> Preset:
    """A new preset with this name, starting as a copy of another."""
    with _lock:
        presets = _load()
        source = next((p for p in presets if p.id == copy_from), presets[0])
        base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "preset"
        preset_id, n = base, 2
        while any(p.id == preset_id for p in presets):
            preset_id, n = f"{base}-{n}", n + 1
        preset = source.model_copy(deep=True, update={"id": preset_id, "name": name})
        presets.append(preset)
        _save(presets)
        return preset


def update(preset_id: str, changes: dict) -> Preset:
    """Sets the given fields. Raises KeyError for an unknown preset."""
    with _lock:
        presets = _load()
        index = next((i for i, p in enumerate(presets) if p.id == preset_id), None)
        if index is None:
            raise KeyError(preset_id)
        presets[index] = Preset.model_validate({**presets[index].model_dump(), **changes, "id": preset_id})
        _save(presets)
        return presets[index]


def delete(preset_id: str) -> None:
    if preset_id == DEFAULT_ID:
        raise ValueError("The default preset can't be deleted")
    with _lock:
        presets = _load()
        _save([p for p in presets if p.id != preset_id])
