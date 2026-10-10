"""The music library: every track composed for a video, kept with the prompt it was
made from, so a later video can use it instead of ordering new music.

It lives in the media folder, outside any project: `music_library` in
settings.json names the folder (no folder - no library). Tracks are filed by
universe, <library>/<universe id>/<file>, and listed in <library>/library.json.
The prompts describe a sound and a mood, with no names or plot, so a track fits
any video of the same universe whose block asks for much the same.

A block takes tracks from the library in two cases: when one was made from a
prompt almost the same as its own (REUSE_SIMILARITY - then composing again would
only grow the library with more of the same), and when the project is set to use
the library only (out of Lumean tokens, or to spend nothing) - then the closest
tracks there are. Prompts are compared by meaning, with the text embedding model.
A track taken from the library is copied into the project, which stays complete
on its own; the library remembers which videos each track plays in, and prefers
the ones used least.
"""

import hashlib
import json
import shutil
import threading
from pathlib import Path

import numpy as np
from pydantic import BaseModel, Field

from app.editor import store
from app.editor.store import MusicBlock, MusicTrack, Project
from app.services.runtime_settings import editor_music_library

INDEX_FILE = "library.json"
# Prompts this alike ask for the same music. Different moods of one video already
# reach 0.94-0.97 with the embedding model in use, half of all pairs 0.84.
REUSE_SIMILARITY = 0.97
# with the library as the only source, a track must at least be of this kind
LIBRARY_ONLY_SIMILARITY = 0.80

_lock = threading.Lock()
_vectors: dict[str, np.ndarray] = {}  # prompt -> its embedding, for as long as the process lives


class LibraryTrack(BaseModel):
    id: str
    file: str  # under the library folder
    universe: str = ""
    prompt: str = ""
    mood: str = ""
    order_id: str = ""
    duration: float
    start: float = 0.0
    end: float
    loudness: float | None = None
    # ids of the projects it plays in
    used_in: list[str] = Field(default_factory=list)
    # unticked by the user in some video: not offered again
    rejected: bool = False
    added_at: str = ""


def root() -> Path | None:
    folder = editor_music_library()
    return Path(folder) if folder else None


def _load(folder: Path) -> list[LibraryTrack]:
    path = folder / INDEX_FILE
    if not path.is_file():
        return []
    return [LibraryTrack.model_validate(t) for t in json.loads(path.read_text(encoding="utf-8")).get("tracks", [])]


def _save(folder: Path, tracks: list[LibraryTrack]) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / INDEX_FILE
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"tracks": [t.model_dump() for t in tracks]}, indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def tracks() -> list[LibraryTrack]:
    folder = root()
    return _load(folder) if folder else []


_hashes: dict[tuple[str, int, int], str] = {}


def library_id(track: MusicTrack, folder: Path) -> str:
    """One id for a piece of music wherever a copy of it lies: a hash of the audio
    file. (Not the order that made it - two tracks of a project can share a file,
    and then they are one piece of music.) Empty when the file is gone."""
    if track.library_id:
        return track.library_id
    path = folder / track.file
    try:
        stat = path.stat()
    except OSError:
        return ""
    key = (str(path), stat.st_size, stat.st_mtime_ns)
    if key not in _hashes:
        _hashes[key] = hashlib.sha1(path.read_bytes()).hexdigest()[:16]
    return _hashes[key]


def add(project: Project, block: MusicBlock, track: MusicTrack, source: Path) -> str | None:
    """Files a project's track in the library (or notes one more video it plays
    in). Returns its library id; None when there is no library."""
    folder = root()
    if folder is None or not source.is_file():
        return None
    universe = store.project_universe(project).id or "other"
    track_id = library_id(track, source.parent)
    with _lock:
        known = _load(folder)
        entry = next((t for t in known if t.id == track_id), None)
        if entry is None:
            name = f"{universe}/{track_id}{source.suffix}"
            (folder / universe).mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, folder / name)
            entry = LibraryTrack(
                id=track_id, file=name, universe=universe, prompt=block.prompt, mood=block.mood,
                order_id=track.order_id, duration=track.duration, start=track.start, end=track.end,
                loudness=track.loudness, added_at=store.now(),
            )
            known.append(entry)
        if project.id not in entry.used_in:
            entry.used_in.append(project.id)
        if not track.enabled:
            entry.rejected = True
        _save(folder, known)
    return track_id


def set_rejected(track_id: str, rejected: bool) -> None:
    """The user left a track out of a video (or put it back): a track someone
    unticked is not offered to other videos."""
    folder = root()
    if folder is None or not track_id:
        return
    with _lock:
        known = _load(folder)
        for entry in known:
            if entry.id == track_id:
                entry.rejected = rejected
        _save(folder, known)


def forget_project(project_id: str, keep: set[str]) -> None:
    """A project no longer plays the tracks it dropped: `keep` is what it still has."""
    folder = root()
    if folder is None:
        return
    with _lock:
        known = _load(folder)
        changed = False
        for entry in known:
            if project_id in entry.used_in and entry.id not in keep:
                entry.used_in.remove(project_id)
                changed = True
        if changed:
            _save(folder, known)


def similar(universe: str, prompt: str, at_least: float) -> list[tuple[float, LibraryTrack]]:
    """The library's tracks of this universe whose prompt is at least this like the
    given one: the closest prompt first, and among tracks of one prompt the one
    used in the fewest videos."""
    from app.services.embeddings import embed_text

    folder = root()
    if folder is None:
        return []
    candidates = [t for t in _load(folder) if t.universe == universe and not t.rejected and (folder / t.file).is_file()]
    if not candidates or not prompt.strip():
        return []
    def vector(text: str) -> np.ndarray:
        if text not in _vectors:
            _vectors[text] = np.array(embed_text(text))  # comes normalised
        return _vectors[text]

    wanted = vector(prompt)
    likeness = {p: float(np.dot(wanted, vector(p))) for p in dict.fromkeys(t.prompt for t in candidates)}
    found = [(likeness[t.prompt], t) for t in candidates if likeness[t.prompt] >= at_least]
    return sorted(found, key=lambda pair: (-round(pair[0], 3), len(pair[1].used_in)))


def take(project: Project, block: MusicBlock, entry: LibraryTrack, out: Path) -> MusicTrack:
    """Copies a library track into the project and returns it as the block's track."""
    folder = root()
    name = f"{block.id}_lib_{entry.id}{Path(entry.file).suffix}"
    shutil.copyfile(folder / entry.file, out / name)
    with _lock:
        known = _load(folder)
        for known_entry in known:
            if known_entry.id == entry.id and project.id not in known_entry.used_in:
                known_entry.used_in.append(project.id)
        _save(folder, known)
    return MusicTrack(
        id=f"track_{entry.id}", file=name, order_id=entry.order_id, duration=entry.duration,
        start=entry.start, end=entry.end, loudness=entry.loudness, library_id=entry.id, from_library=True,
    )


def usage(project: Project) -> dict[str, dict]:
    """For the project's tracks that are in the library: {track id: {from_library,
    used_in: titles of the other videos it plays in}}."""
    by_id = {t.id: t for t in tracks()}
    titles: dict[str, str] = {}

    def title(project_id: str) -> str:
        if project_id not in titles:
            try:
                titles[project_id] = store.load_project(project_id).title
            except Exception:
                titles[project_id] = project_id
        return titles[project_id]

    out = {}
    folder = store.project_dir(project.id) / "music"
    for block in project.music:
        for track in block.tracks:
            entry = by_id.get(library_id(track, folder))
            if entry is not None:
                out[track.id] = {
                    "from_library": track.from_library,
                    "used_in": [title(p) for p in entry.used_in if p != project.id],
                }
    return out
