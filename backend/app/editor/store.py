"""On-disk state for video-editor projects.

One folder per project under the user-chosen root:

    <root>/<project_id>/
        project.json      - the project state (this module's Project model)
        transcript.json   - word-level timestamps of the narration
        <audio file>      - the narration, copied in on upload

The editor never writes to the index database - it only reads the index through search.
"""

import datetime as dt
import json
import os
import re
import threading
import time
from pathlib import Path

from pydantic import BaseModel, Field, field_serializer, model_validator

from app.config import media_path, media_stored
from app.services.runtime_settings import editor_default_look, editor_projects_root
from app.services.universes import Universe, all_universes, get_universe, universe_for_show

SCHEMA_VERSION = 1
PROJECT_FILE = "project.json"
TRANSCRIPT_FILE = "transcript.json"

# One writer at a time per process - API handlers and stage threads both
# read-modify-write project.json.
_lock = threading.RLock()


class ProjectNotFound(Exception):
    pass


class RootNotConfigured(Exception):
    pass


class MediaFile(BaseModel):
    """A model holding a source video or PDF in file_path: saved relative to
    MEDIA_DIR, so the media folder can be moved, and a full path once loaded. (A
    photo's file_path is relative to its project's folder and is left alone.)"""

    @model_validator(mode="before")
    @classmethod
    def _full_media_path(cls, data):
        if isinstance(data, dict) and data.get("kind") != "photo" and isinstance(data.get("file_path"), str):
            data = {**data, "file_path": media_path(data["file_path"])}
        return data

    @field_serializer("file_path", check_fields=False)
    def _stored_media_path(self, value: str) -> str:
        return media_stored(value)


class Word(BaseModel):
    text: str
    start: float
    end: float


class Segment(BaseModel):
    """A stretch of the final video's timeline. Narration segments point into the
    narration audio; quote segments (added later) carry their own source audio and
    push everything after them later - so the timeline is never assumed to be the
    audio file's own clock."""

    id: str
    type: str = "narration"  # "narration" | "quote"
    audio_start: float
    audio_end: float


class Candidate(MediaFile):
    """One piece of footage that could fill a slot. Carries the file path and
    times itself - scene ids change when an episode is re-indexed, so scene_id is
    only a hint for thumbnails and de-duplication."""

    scene_id: int
    source_id: int
    file_path: str
    # "video", or "comic": a page of a comic, shown scrolling down the screen. For a
    # page, image_path is its picture (relative to DATA_DIR) and scene_start its number counted from 0;
    # clip_start/clip_end only carry the slot's length.
    kind: str = "video"
    image_path: str | None = None
    # "photo": a photo from Wikimedia Commons (see editor/photos.py), shown still;
    # credit is the attribution its licence asks for
    credit: str | None = None
    show_title: str | None = None
    book: int | None = None
    episode_number: int | None = None
    scene_start: float
    scene_end: float
    clip_start: float
    clip_end: float
    description: str | None = None
    # how many standard deviations closer to the query than a random scene, per index
    text_score: float | None = None
    wiki_score: float | None = None
    visual_score: float | None = None
    score: float
    # pieces of a shot too short to read (a flicker) that the clip would contain
    slivers: int = 0
    # Seconds at the clip's start and end where the picture stands still instead of
    # showing what the file has there - a quote's sound starts and stops with the
    # words, which can leave a flash of the neighbouring shot (see render._quote_holds)
    hold_start: float = 0.0
    hold_end: float = 0.0
    # for footage of a show that may only be shown as stills: the frame to show
    # (seconds in the file), picked by the user; None = the scene's keyframe
    still_at: float | None = None


class Slot(BaseModel):
    """One clip's worth of screen time inside a beat (at most the project's max_clip_seconds, give or take a shift onto a cut)."""

    # seconds on the narration clock
    start: float
    end: float
    text_query: str = ""
    visual_query: str = ""
    # which shows/films to look in for this clip; empty = everything the project allows
    shows: list[str] = Field(default_factory=list)
    # a real person or thing to show a photo of (searched on Wikimedia Commons), for a
    # shot about something the shows never showed; empty for most shots
    photo_query: str = ""
    # a particular event the narration names, which happens in one place in the show
    # (see editor/moments.py), and the episodes (source ids) it is expected in;
    # empty for a shot that only illustrates
    moment: str = ""
    moment_episodes: list[int] = Field(default_factory=list)
    candidates: list[Candidate] = Field(default_factory=list)
    selected: int | None = None  # index into candidates
    locked: bool = False  # chosen by the user - automatic selection leaves it alone


class Background(MediaFile):
    """One calm scenery shot shown blurred behind the frame. Like Candidate, it
    carries the file path and times rather than relying on scene_id."""

    scene_id: int
    source_id: int
    file_path: str
    clip_start: float
    clip_end: float


class Beat(BaseModel):
    id: str
    segment_id: str
    # inclusive word indexes into transcript.json
    word_start: int
    word_end: int
    # seconds in the narration audio
    start: float
    end: float
    text: str
    summary: str = ""
    slots: list[Slot] = Field(default_factory=list)
    background_query: str = ""
    # scenery shots that play behind this beat, in order (framed layout)
    backgrounds: list[Background] = Field(default_factory=list)


class MusicTrack(BaseModel):
    """One generated piece of music. Generated tracks fade out towards the end (and
    may start from silence), so only start..end of the file is played."""

    id: str
    file: str  # under <project>/music/
    order_id: str = ""
    duration: float
    start: float = 0.0
    end: float
    loudness: float | None = None  # LUFS of start..end
    enabled: bool = True  # unticked by the user: left out of the video
    # its id in the music library (editor/music_library.py), once it is filed there
    library_id: str = ""
    from_library: bool = False  # taken from the library, not composed for this video


class MusicBlock(BaseModel):
    """A stretch of the narration with one mood, and the music composed for it.
    Covers whole beats; times are on the narration clock."""

    id: str
    beat_first: str
    beat_last: str
    start: float
    end: float
    mood: str = ""
    prompt: str = ""  # what the music generator is asked for
    tracks: list[MusicTrack] = Field(default_factory=list)
    # library tracks this block had and the user threw away by composing it again
    avoid: list[str] = Field(default_factory=list)


class QuotePart(BaseModel):
    """One piece of a quote that is cut together from several."""

    start: float
    end: float
    text: str = ""
    speech_end: float | None = None  # where its last word stops, in episode time


class Quote(MediaFile):
    """A clip of dialogue from the show, played with its own sound between two
    pieces of narration. Only "approved" quotes are part of the video."""

    id: str
    status: str = "proposed"  # "proposed" | "approved" | "rejected"
    # plays after this narration word, i.e. at this time on the narration clock
    after_word: int
    at: float
    after_text: str = ""  # the narration just before it, for display
    source_id: int
    file_path: str
    show_title: str | None = None
    book: int | None = None
    episode_number: int | None = None
    clip_start: float
    clip_end: float
    # A quote cut together from pieces of one moment: they play back to back, with
    # what was said between them left out (a hard cut). Empty = one continuous clip;
    # otherwise clip_start/clip_end are the first piece's start and the last one's end.
    parts: list[QuotePart] = Field(default_factory=list)
    text: str
    speaker: str = ""
    reason: str = ""
    # where the last word of the quote stops, in episode time: after it the clip
    # lets the scene's sound die away instead of cutting it (None = not known)
    speech_end: float | None = None

    def spans(self) -> list[tuple[float, float]]:
        """The stretches of the episode that play, in order."""
        return [(part.start, part.end) for part in self.parts] or [(self.clip_start, self.clip_end)]


class Job(BaseModel):
    stage: str
    status: str  # "running" | "done" | "error"
    detail: str = ""
    # 0-1 when the stage reports counts ("12/151"); None when it can't say (an LLM call)
    progress: float | None = None
    error: str | None = None
    started_at: str = ""


class RenderSettings(BaseModel):
    layout: str = "framed"  # "framed" (clip in a frame over a blurred background) | "simple" (full screen)
    search_mode: str = "smart_hybrid"  # "smart_hybrid" | "text" | "visual"
    # Longest a single clip may run. A beat longer than this is split into equal
    # clips. Longer clips mean fewer, calmer cuts and less flicker, but more of the
    # footage around the moment that was actually searched for.
    max_clip_seconds: float = 7.0
    # framed layout only. frame: "border" (plain outline), "none", or the name of a
    # PNG in <root>/library/frames whose transparent opening the clip sits under
    frame: str = "border"
    clip_height: float = 0.75  # share of the screen height, for "border"/"none"; image frames define their own
    blur: float = 15.0  # background blur, 0-100
    dim: float = 0.4  # background darkening, 0-1
    background_speed: float = 1.0  # playback speed of the background scenery, 0.25-1
    frame_scale: float = 1.0  # size of the whole frame (artwork and clip together); 1 = as tall as the screen allows
    clip_scale: float = 1.0  # size of the clip relative to the frame's opening, to fine-tune the fit
    corner_radius: int = 0  # rounding of the clip's corners in output pixels, 0 = square
    # advanced: one radius per corner (top-left, top-right, bottom-right, bottom-left);
    # when set it replaces corner_radius
    corner_radii: list[int] | None = None
    wide_full_screen: bool = True  # framed layout: widescreen sources fill the screen, no frame
    # Footage wider than the screen (a 2.39:1 film) can't fill it without losing its
    # sides. 0 = show all of it, with the blurred background above and below;
    # 1 = zoom in until it fills the height, cropping the sides; anything between.
    wide_fill: float = 0.0
    # Experimental, quotes stage: a proposed quote may be cut together from two or
    # three pieces of a longer speech or exchange instead of one continuous stretch.
    quote_cuts: bool = False
    # music under the narration (the music stage): whether the video has it, and how
    # much quieter than the voice it plays, in loudness units
    music_enabled: bool = True
    music_level: float = 18.0
    # how wide a comic page is shown, as a share of the screen's width. Wider means
    # more of the page to scroll through, and a softer picture: the scans are small
    comic_width: float = 0.55
    # fastest a comic page may scroll, in pixels of the 1080p screen per second; a page
    # shown briefly then shows only the part it has time for. 0 = no limit.
    comic_max_speed: float = 0.0
    # compose no new music: fill the blocks from the music library only
    music_library_only: bool = False
    # standard sound effects (editor/sounds.py) at the start of comic pages and photos,
    # played this many dB below their normalised peak
    sound_effects: bool = True
    sfx_volume: float = 12.0
    # 24: the footage's own 23.976 fps. 48: twice that - footage shows each frame
    # twice, but a scrolling comic page or panning photo moves half as far per frame
    frame_rate: int = 24
    # the finished video's sound is raised (or lowered) so the narration sits at this
    # loudness, in LUFS. YouTube turns louder videos down to -14 but never turns a
    # quieter one up, so a video below it just plays quieter than the rest.
    loudness_target: float = -14.0


class Project(BaseModel):
    version: int = SCHEMA_VERSION
    id: str
    title: str
    created_at: str
    updated_at: str
    audio_file: str | None = None
    audio_duration: float | None = None
    script_text: str | None = None
    # the author's notes on where the script's material comes from ("Keung and June
    # are from the comic The Bounty Hunter and the Tea Brewer") - read by the
    # shot-queries call, so a shot is looked for where its story actually is
    footage_notes: str | None = None
    # the author's wishes for the picture, in their own words ("more close-ups of faces,
    # fights over talk, no comic pages in the first minute") - read by the shot-queries
    # and background calls
    visual_notes: str | None = None
    # the fictional world this video is about; decides which shows it can use and
    # what the prompts say about the world. Empty on projects made before universes.
    universe: str = ""
    shows: list[str] = Field(default_factory=list)
    settings: RenderSettings = Field(default_factory=RenderSettings)
    word_count: int = 0
    timeline: list[Segment] = Field(default_factory=list)
    beats: list[Beat] = Field(default_factory=list)
    quotes: list[Quote] = Field(default_factory=list)
    music: list[MusicBlock] = Field(default_factory=list)
    llm_cost_usd: float = 0.0
    output_file: str | None = None  # relative to the project folder
    rendered_at: str | None = None
    job: Job | None = None
    # stages waiting to run after the current one, in order
    queue: list[str] = Field(default_factory=list)
    # stages that are to re-pick the clips the user locked too, the next time they run
    unlock_before: list[str] = Field(default_factory=list)


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def root_dir() -> Path:
    root = editor_projects_root()
    if not root:
        raise RootNotConfigured("Editor projects folder is not set")
    path = Path(root)
    path.mkdir(parents=True, exist_ok=True)
    return path


def project_dir(project_id: str) -> Path:
    # ids are slugs we generated; refuse anything that could step outside the root
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", project_id):
        raise ProjectNotFound(project_id)
    path = root_dir() / project_id
    if not (path / PROJECT_FILE).is_file():
        raise ProjectNotFound(project_id)
    return path


def _write_json(path: Path, data: dict | list) -> None:
    # write-then-rename so a crash mid-write can't leave a truncated project.json
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    # Windows refuses to replace a file someone has open - another request reading it,
    # an antivirus or the search indexer looking at it - for the moment they hold it
    for attempt in range(20):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(0.05 * (attempt + 1))


def _slugify(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:60].strip("-")
    return slug or "project"


def default_settings(layout: str | None = None) -> RenderSettings:
    """Settings for a new project: the saved default look, falling back to the
    built-in defaults for anything missing or no longer valid."""
    try:
        settings = RenderSettings.model_validate(editor_default_look())
    except ValueError:
        settings = RenderSettings()
    if layout:
        settings.layout = layout
    return settings


def project_universe(project: "Project") -> Universe:
    """The project's universe; for older projects, worked out from its shows."""
    chosen = get_universe(project.universe)
    if chosen is not None:
        return chosen
    if project.shows:
        return universe_for_show(project.shows[0])
    return all_universes()[0]


def project_comics(project: "Project") -> list[str]:
    """Comics of the project's universe. A clip is looked for in one only when its
    shot was written for that comic (Slot.shows names it)."""
    return list(project_universe(project).comics)


def project_shows(project: "Project") -> list[str]:
    """Shows this project may take footage from: its own choice, or the whole universe."""
    return list(project.shows) or list(project_universe(project).shows)


def create_project(
    title: str, script_text: str | None, shows: list[str], layout: str | None, universe: str = "",
    footage_notes: str | None = None,
    visual_notes: str | None = None,
) -> Project:
    with _lock:
        root = root_dir()
        base = _slugify(title)
        project_id = base
        n = 2
        while (root / project_id).exists():
            project_id = f"{base}-{n}"
            n += 1
        (root / project_id).mkdir()

        created = now()
        project = Project(
            id=project_id,
            title=title,
            created_at=created,
            updated_at=created,
            script_text=script_text or None,
            footage_notes=(footage_notes or "").strip() or None,
            visual_notes=(visual_notes or "").strip() or None,
            universe=universe,
            shows=shows,
            settings=default_settings(layout),
        )
        _write_json(root / project_id / PROJECT_FILE, project.model_dump())
        return project


def load_project(project_id: str) -> Project:
    with _lock:
        path = project_dir(project_id) / PROJECT_FILE
        return Project.model_validate_json(path.read_text(encoding="utf-8"))


def save_project(project: Project) -> None:
    with _lock:
        project.updated_at = now()
        _write_json(project_dir(project.id) / PROJECT_FILE, project.model_dump())


def update_project(project_id: str, mutate) -> Project:
    """Load, apply mutate(project), save - under the lock, so a stage thread
    reporting progress and an API edit can't overwrite each other's fields."""
    with _lock:
        project = load_project(project_id)
        mutate(project)
        save_project(project)
        return project


def list_projects() -> list[Project]:
    root = root_dir()
    projects = []
    for path in sorted(root.iterdir()):
        if (path / PROJECT_FILE).is_file():
            try:
                with _lock:
                    text = (path / PROJECT_FILE).read_text(encoding="utf-8")
                projects.append(Project.model_validate_json(text))
            except ValueError:
                continue
    projects.sort(key=lambda p: p.created_at, reverse=True)
    return projects


def load_words(project_id: str) -> list[Word]:
    path = project_dir(project_id) / TRANSCRIPT_FILE
    if not path.is_file():
        return []
    return [Word.model_validate(w) for w in json.loads(path.read_text(encoding="utf-8"))]


def save_words(project_id: str, words: list[Word]) -> None:
    with _lock:
        _write_json(project_dir(project_id) / TRANSCRIPT_FILE, [w.model_dump() for w in words])
