import re
import subprocess
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from app.editor import jobs, music_library, store, voice_text, voiceover
from app.editor.store import Project, Word
from app.services.lumean import Lumean, LumeanError
from app.services.runtime_settings import (
    current_lumean_api_key,
    editor_projects_root,
    set_editor_default_look,
    set_editor_default_voice,
    editor_default_visual_notes,
    set_editor_default_visual_notes,
    set_editor_projects_root,
)

router = APIRouter(prefix="/api/editor", tags=["editor"])

AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aac"}
LAYOUTS = {"framed", "simple"}
SEARCH_MODES = {"smart_hybrid", "text", "visual"}


class EditorSettings(BaseModel):
    projects_root: str | None


class EditorSettingsIn(BaseModel):
    projects_root: str


class ProjectCreateIn(BaseModel):
    title: str
    script_text: str | None = None
    footage_notes: str | None = None
    visual_notes: str | None = None
    universe: str = ""
    shows: list[str] = []
    layout: str | None = None  # None = the saved default look's layout


class VoiceTextIn(BaseModel):
    text: str
    instructions: str = ""  # what the author wants from the delivery, in their own words


class ProjectUpdateIn(BaseModel):
    title: str | None = None
    script_text: str | None = None
    footage_notes: str | None = None
    visual_notes: str | None = None
    shows: list[str] | None = None
    layout: str | None = None
    search_mode: str | None = None
    max_clip_seconds: float | None = None
    frame: str | None = None
    blur: float | None = None
    dim: float | None = None
    corner_radius: int | None = None
    wide_fill: float | None = None
    clip_scale: float | None = None
    frame_scale: float | None = None
    corner_radii: list[int] | None = None
    background_speed: float | None = None
    wide_full_screen: bool | None = None
    quote_cuts: bool | None = None
    music_enabled: bool | None = None
    music_level: float | None = None
    music_library_only: bool | None = None
    comic_width: float | None = None
    comic_max_speed: float | None = None
    sound_effects: bool | None = None
    sfx_volume: float | None = None
    frame_rate: int | None = None


def _load(project_id: str) -> Project:
    try:
        return jobs.reconcile(store.load_project(project_id))
    except store.RootNotConfigured:
        raise HTTPException(409, "Editor projects folder is not set")
    except store.ProjectNotFound:
        raise HTTPException(404, "Project not found")


@router.get("/settings", response_model=EditorSettings)
def read_settings() -> EditorSettings:
    return EditorSettings(projects_root=editor_projects_root())


@router.put("/settings", response_model=EditorSettings)
def write_settings(payload: EditorSettingsIn) -> EditorSettings:
    path = Path(payload.projects_root.strip())
    if not path.is_absolute():
        raise HTTPException(400, "Projects folder must be an absolute path")
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise HTTPException(400, f"Cannot create folder: {exc}")
    set_editor_projects_root(str(path))
    return EditorSettings(projects_root=str(path))


@router.get("/projects", response_model=list[Project])
def list_projects() -> list[Project]:
    try:
        return [jobs.reconcile(p) for p in store.list_projects()]
    except store.RootNotConfigured:
        return []


@router.post("/projects", response_model=Project)
def create_project(payload: ProjectCreateIn) -> Project:
    title = payload.title.strip()
    if not title:
        raise HTTPException(400, "Title is required")
    if payload.layout is not None and payload.layout not in LAYOUTS:
        raise HTTPException(400, f"Unknown layout: {payload.layout}")
    try:
        return store.create_project(
            title, payload.script_text, payload.shows, payload.layout, payload.universe, payload.footage_notes,
            payload.visual_notes,
        )
    except store.RootNotConfigured:
        raise HTTPException(409, "Editor projects folder is not set")


@router.get("/projects/{project_id}", response_model=Project)
def get_project(project_id: str) -> Project:
    return _load(project_id)


@router.patch("/projects/{project_id}", response_model=Project)
def update_project(project_id: str, payload: ProjectUpdateIn) -> Project:
    _load(project_id)
    data = payload.model_dump(exclude_unset=True)
    if "layout" in data and data["layout"] not in LAYOUTS:
        raise HTTPException(400, f"Unknown layout: {data['layout']}")
    if "search_mode" in data and data["search_mode"] not in SEARCH_MODES:
        raise HTTPException(400, f"Unknown search mode: {data['search_mode']}")

    def apply(p: Project) -> None:
        if data.get("title"):
            p.title = data["title"].strip()
        if "script_text" in data:
            p.script_text = data["script_text"] or None
        if "footage_notes" in data:
            p.footage_notes = (data["footage_notes"] or "").strip() or None
        if "visual_notes" in data:
            p.visual_notes = (data["visual_notes"] or "").strip() or None
        if data.get("shows") is not None:
            p.shows = data["shows"]
        if data.get("layout"):
            p.settings.layout = data["layout"]
        if data.get("search_mode"):
            p.settings.search_mode = data["search_mode"]
        if data.get("frame"):
            p.settings.frame = data["frame"]
        if data.get("blur") is not None:
            p.settings.blur = min(100.0, max(0.0, data["blur"]))
        if data.get("dim") is not None:
            p.settings.dim = min(0.9, max(0.0, data["dim"]))
        if data.get("corner_radius") is not None:
            p.settings.corner_radius = min(300, max(0, data["corner_radius"]))
        if data.get("max_clip_seconds") is not None:
            p.settings.max_clip_seconds = min(15.0, max(2.0, data["max_clip_seconds"]))
        if data.get("frame_scale") is not None:
            p.settings.frame_scale = min(1.5, max(0.4, data["frame_scale"]))
        if data.get("clip_scale") is not None:
            p.settings.clip_scale = min(1.5, max(0.5, data["clip_scale"]))
        if "corner_radii" in data:
            radii = data["corner_radii"]
            p.settings.corner_radii = [min(300, max(0, int(r))) for r in radii] if radii and len(radii) == 4 else None
        if data.get("background_speed") is not None:
            p.settings.background_speed = min(1.0, max(0.25, data["background_speed"]))
        if data.get("wide_fill") is not None:
            p.settings.wide_fill = min(1.0, max(0.0, data["wide_fill"]))
        if data.get("wide_full_screen") is not None:
            p.settings.wide_full_screen = data["wide_full_screen"]
        if data.get("quote_cuts") is not None:
            p.settings.quote_cuts = data["quote_cuts"]
        if data.get("music_enabled") is not None:
            p.settings.music_enabled = data["music_enabled"]
        if data.get("music_level") is not None:
            p.settings.music_level = min(40.0, max(6.0, data["music_level"]))
        if data.get("music_library_only") is not None:
            p.settings.music_library_only = data["music_library_only"]
        if data.get("comic_width") is not None:
            p.settings.comic_width = min(1.0, max(0.3, data["comic_width"]))
        if data.get("comic_max_speed") is not None:
            p.settings.comic_max_speed = min(2000.0, max(0.0, data["comic_max_speed"]))
        if data.get("sound_effects") is not None:
            p.settings.sound_effects = data["sound_effects"]
        if data.get("sfx_volume") is not None:
            p.settings.sfx_volume = min(48.0, max(0.0, data["sfx_volume"]))
        if data.get("frame_rate") is not None:
            p.settings.frame_rate = 48 if data["frame_rate"] == 48 else 24

    return store.update_project(project_id, apply)


def _probe_duration(path: Path) -> float | None:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True,
        text=True,
    )
    try:
        return round(float(result.stdout.strip()), 3)
    except ValueError:
        return None


@router.put("/projects/{project_id}/audio", response_model=Project)
async def upload_audio(project_id: str, filename: str, request: Request) -> Project:
    """Raw request body = the audio file (no multipart, so no extra dependency).
    Replacing the audio discards the transcript and beats built from the old one."""
    project = _load(project_id)
    if project.job and project.job.status == "running":
        raise HTTPException(409, "A stage is running - wait for it to finish")

    extension = Path(filename).suffix.lower()
    if extension not in AUDIO_EXTENSIONS:
        raise HTTPException(400, f"Unsupported audio type: {extension or filename}")

    body = await request.body()
    if not body:
        raise HTTPException(400, "Empty upload")

    folder = store.project_dir(project_id)
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(filename).stem).strip("._") or "narration"
    target = folder / f"{stem}{extension}"
    target.write_bytes(body)

    duration = _probe_duration(target)
    if duration is None:
        target.unlink(missing_ok=True)
        raise HTTPException(400, "File is not readable audio")

    if project.audio_file and project.audio_file != target.name:
        (folder / project.audio_file).unlink(missing_ok=True)
    (folder / store.TRANSCRIPT_FILE).unlink(missing_ok=True)

    def apply(p: Project) -> None:
        p.audio_file = target.name
        p.audio_duration = duration
        p.word_count = 0
        p.timeline = []
        p.beats = []
        p.quotes = []
        p.job = None

    return store.update_project(project_id, apply)


@router.get("/projects/{project_id}/audio")
def get_audio(project_id: str):
    project = _load(project_id)
    if not project.audio_file:
        raise HTTPException(404, "Project has no audio")
    path = store.project_dir(project_id) / project.audio_file
    if not path.is_file():
        raise HTTPException(404, "Audio file missing on disk")
    return FileResponse(path)


@router.get("/projects/{project_id}/transcript", response_model=list[Word])
def get_transcript(project_id: str) -> list[Word]:
    _load(project_id)
    return store.load_words(project_id)


@router.post("/projects/{project_id}/run/{stage}", response_model=Project)
def run_stage(project_id: str, stage: str, unlock: bool = False) -> Project:
    """Runs the stage now, or queues it if the project is already running one.
    unlock (shot queries, find footage): re-pick the clips the user locked as well;
    the locks are dropped when the stage starts, not now, so a stage still running
    ahead of it can't write them back."""
    project = _load(project_id)
    # A stage queued behind others can't be checked yet - what it needs may be exactly
    # what the stages ahead of it produce. It checks for itself when its turn comes.
    if not jobs.is_busy(project_id):
        if stage == "voiceover":
            # it can place a paid order, so it is only started from the Voice tab
            raise HTTPException(400, "Start the voice-over from the Voice tab")
        if stage == "transcribe" and not project.audio_file:
            raise HTTPException(400, "Upload the narration audio first")
        if stage in ("beats", "quotes") and project.word_count == 0:
            raise HTTPException(400, "Transcribe the audio first")
        if stage == "music" and not current_lumean_api_key():
            raise HTTPException(400, "Add the Lumean API key in Settings first")
        if stage in ("queries", "background", "music") and not project.beats:
            raise HTTPException(400, "Build beats first")
        if stage == "render" and not any(slot.selected is not None for b in project.beats for slot in b.slots):
            raise HTTPException(400, "Find footage first")
        if stage == "select" and not any(slot.text_query or slot.visual_query for b in project.beats for slot in b.slots):
            raise HTTPException(400, "Write shot queries first")
    if unlock and stage in ("queries", "select"):
        store.update_project(project_id, lambda p: p.unlock_before.append(stage) if stage not in p.unlock_before else None)
    try:
        return jobs.start(project_id, stage)
    except KeyError:
        raise HTTPException(404, f"Unknown stage: {stage}")


def remaining_stages(project: Project, music: bool) -> list[str]:
    """The stages "Run to the end" queues: from the first one whose output is missing,
    every one after it too (each builds on the one before), then the render. Quotes
    are left out - they wait for the user's approval anyway - and so is music unless
    asked for, as it places paid orders."""
    from app.editor.music import blocks_fit

    slots = [slot for beat in project.beats for slot in beat.slots]
    done = {
        "transcribe": project.word_count > 0,
        "beats": bool(project.beats),
        "queries": bool(slots) and all(slot.text_query or slot.visual_query or slot.photo_query for slot in slots),
        "select": bool(slots) and all(slot.selected is not None for slot in slots),
        "background": project.settings.layout != "framed" or (bool(project.beats) and all(b.backgrounds for b in project.beats)),
    }
    order = ["transcribe", "beats", "queries", "select", "background"]
    first = next((n for n, stage in enumerate(order) if not done[stage]), len(order))
    stages = [s for s in order[first:] if s != "background" or project.settings.layout == "framed"]
    if music and (stages or not blocks_fit(project) or any(not any(t.enabled for t in b.tracks) for b in project.music)):
        stages.append("music")
    return [*stages, "render"]


@router.post("/projects/{project_id}/run-all", response_model=Project)
def run_all(project_id: str, music: bool = False) -> Project:
    """Queues every stage still to do, through the render (remaining_stages)."""
    project = _load(project_id)
    if not project.audio_file:
        raise HTTPException(400, "Add the narration first - upload it or voice the script")
    if music and not current_lumean_api_key():
        raise HTTPException(400, "Add the Lumean API key in Settings first")
    for stage in remaining_stages(project, music):
        project = jobs.start(project_id, stage)
    return project


@router.delete("/projects/{project_id}/queue/{stage}", response_model=Project)
def unqueue_stage(project_id: str, stage: str) -> Project:
    """Takes a waiting stage off the project's queue (the running one is not affected)."""
    _load(project_id)
    store.update_project(project_id, lambda p: p.unlock_before.remove(stage) if stage in p.unlock_before else None)
    return jobs.unqueue(project_id, stage)


# --- voice-over ---------------------------------------------------------------


class VoiceoverOut(BaseModel):
    has_key: bool
    state: voiceover.Voiceover
    # the script changed since it was sent, so the audio no longer matches it
    script_changed: bool


class FragmentRedoIn(BaseModel):
    text: str | None = None


def _voiceover(project: Project) -> VoiceoverOut:
    state = voiceover.load(project.id)
    script = (project.script_text or "").strip()
    return VoiceoverOut(
        has_key=bool(current_lumean_api_key()),
        state=state,
        script_changed=bool(state.order_id and state.order_text is not None and state.order_text != script),
    )


def _lumean(call):
    """Runs a call that talks to Lumean, turning its refusals into API errors."""
    try:
        return call()
    except LumeanError as exc:
        raise HTTPException(502 if exc.status is None or exc.status >= 500 else 400, str(exc))
    except RuntimeError as exc:
        raise HTTPException(400, str(exc))


def _idle(project_id: str) -> Project:
    project = _load(project_id)
    if project.job and project.job.status == "running":
        raise HTTPException(409, "A stage is running - wait for it to finish")
    return project


@router.get("/projects/{project_id}/voiceover", response_model=VoiceoverOut)
def get_voiceover(project_id: str) -> VoiceoverOut:
    return _voiceover(_load(project_id))


@router.put("/projects/{project_id}/voiceover/settings", response_model=VoiceoverOut)
def set_voiceover_settings(project_id: str, payload: voiceover.VoiceSettings) -> VoiceoverOut:
    """Voice and model for the next voice-over. Fragments voiced again later in the
    current one keep the settings it was ordered with."""
    project = _idle(project_id)
    state = voiceover.load(project_id)
    state.settings = payload
    voiceover.save(project_id, state)
    return _voiceover(project)


@router.get("/voiceover/voices")
def list_voices(search: str = "", page: int = 0, gender: str = "", language: str = "") -> dict:
    """A page of the ElevenLabs voice library (pages count from 0)."""
    return _lumean(
        lambda: Lumean().elevenlabs_voices(
            search=search.strip(), page=max(0, page), gender=gender, required_languages=language
        )
    )


@router.post("/voice-text")
def prepare_voice_text(payload: VoiceTextIn) -> dict:
    """A script made ready for the voice: directions like [whispers] where the
    delivery changes, pauses, numbers spelled out (one Claude call). `changes` lists
    the places where the wording itself came back different - there should be none."""
    if not payload.text.strip():
        raise HTTPException(400, "There is no script to prepare")
    try:
        text, changes, cost = voice_text.optimize(payload.text, payload.instructions)
    except (RuntimeError, subprocess.TimeoutExpired) as exc:
        raise HTTPException(502, f"Could not prepare the script: {exc}")
    return {"text": text, "changes": changes, "cost_usd": cost}


class VisualNotesIn(BaseModel):
    text: str


@router.get("/default-visual-notes")
def get_default_visual_notes() -> dict:
    """The visual wishes a new video starts with - the ones last saved as default."""
    return {"text": editor_default_visual_notes()}


@router.put("/default-visual-notes")
def save_default_visual_notes(payload: VisualNotesIn) -> dict:
    set_editor_default_visual_notes(payload.text.strip())
    return {"text": editor_default_visual_notes()}


@router.get("/voiceover/default-voice", response_model=voiceover.VoiceSettings)
def get_default_voice() -> voiceover.VoiceSettings:
    """The voice new projects start with (no voice_id when none was saved)."""
    return voiceover.default_voice()


@router.post("/projects/{project_id}/voiceover/save-default-voice", response_model=voiceover.VoiceSettings)
def save_default_voice(project_id: str) -> voiceover.VoiceSettings:
    """Makes this project's voice and its settings the starting point for new
    projects. Existing projects are not changed."""
    _load(project_id)
    settings = voiceover.load(project_id).settings
    if not settings.voice_id:
        raise HTTPException(400, "Choose a voice first")
    set_editor_default_voice(settings.model_dump())
    return voiceover.default_voice()


@router.get("/voiceover/own-voices")
def list_own_voices() -> list[dict]:
    """Voices already set up as templates in the Lumean account, with their settings."""
    return _lumean(voiceover.own_voices)


@router.get("/projects/{project_id}/voiceover/estimate")
def estimate_voiceover(project_id: str) -> dict:
    """What voicing the script would cost; orders nothing."""
    _idle(project_id)
    return _lumean(lambda: voiceover.estimate(project_id))


@router.post("/projects/{project_id}/voiceover/generate", response_model=Project)
def generate_voiceover(project_id: str) -> Project:
    """Sends the script to be voiced - a new, paid order, replacing the current one."""
    project = _idle(project_id)
    if not (project.script_text or "").strip():
        raise HTTPException(400, "The project has no script to voice")
    if not current_lumean_api_key():
        raise HTTPException(400, "Add the Lumean API key in Settings first")
    if not voiceover.load(project_id).settings.voice_id:
        raise HTTPException(400, "Choose a voice first")
    voiceover.start_new(project_id)
    return jobs.start(project_id, "voiceover")


@router.post("/projects/{project_id}/voiceover/sync", response_model=Project)
def sync_voiceover(project_id: str) -> Project:
    """Picks the current order up again: waits for it and fetches what changed. Free."""
    _idle(project_id)
    if not voiceover.load(project_id).order_id:
        raise HTTPException(400, "There is no voice-over yet")
    return jobs.start(project_id, "voiceover")


@router.post("/projects/{project_id}/voiceover/fragments/{index}/redo", response_model=Project)
def redo_voiceover_fragment(project_id: str, index: int, payload: FragmentRedoIn) -> Project:
    """Voices one fragment again, optionally with other wording, then waits for it."""
    _idle(project_id)
    _lumean(lambda: voiceover.redo_fragment(project_id, index, payload.text))
    return jobs.start(project_id, "voiceover")


@router.post("/projects/{project_id}/voiceover/retry-failed", response_model=Project)
def retry_failed_voiceover(project_id: str) -> Project:
    """Voices every technically failed fragment again; the ones the content policy
    rejected need other wording and are left alone."""
    _idle(project_id)
    _lumean(lambda: voiceover.retry_failed(project_id))
    return jobs.start(project_id, "voiceover")


@router.post("/projects/{project_id}/voiceover/use", response_model=Project)
def use_voiceover(project_id: str) -> Project:
    """Makes the finished voice-over the project's narration, discarding the
    transcript, beats and quotes built on the previous one."""
    _idle(project_id)
    return _lumean(lambda: voiceover.use_as_narration(project_id))


@router.get("/projects/{project_id}/voiceover/files/{name:path}")
def get_voiceover_file(project_id: str, name: str):
    _load(project_id)
    base = voiceover.folder(project_id).resolve()
    path = (base / name).resolve()
    if base not in path.parents or not path.is_file():
        raise HTTPException(404, "File not found")
    return FileResponse(path)


# --- music ---------------------------------------------------------------------


class MusicBlockIn(BaseModel):
    mood: str | None = None
    prompt: str | None = None


class MusicTrackIn(BaseModel):
    enabled: bool


def _music_block(project: Project, block_id: str):
    block = next((b for b in project.music if b.id == block_id), None)
    if block is None:
        raise HTTPException(404, "No such block of music")
    return block


@router.patch("/projects/{project_id}/music/{block_id}", response_model=Project)
def update_music_block(project_id: str, block_id: str, payload: MusicBlockIn) -> Project:
    """Changes what a block's music is described as. Its tracks stay until new ones
    are asked for."""
    _music_block(_idle(project_id), block_id)

    def apply(p: Project) -> None:
        block = _music_block(p, block_id)
        if payload.mood is not None:
            block.mood = payload.mood.strip()
        if payload.prompt is not None and payload.prompt.strip():
            block.prompt = payload.prompt.strip()

    return store.update_project(project_id, apply)


@router.post("/projects/{project_id}/music/{block_id}/compose", response_model=Project)
def recompose_music_block(project_id: str, block_id: str) -> Project:
    """Throws a block's tracks away and composes new ones from its prompt."""
    _music_block(_idle(project_id), block_id)
    if not current_lumean_api_key():
        raise HTTPException(400, "Add the Lumean API key in Settings first")
    def drop(p: Project) -> None:
        block = _music_block(p, block_id)
        # "again" means other music: what it had is not taken back from the library
        folder = store.project_dir(project_id) / "music"
        had = [music_library.library_id(t, folder) for t in block.tracks]
        block.avoid += [i for i in dict.fromkeys(had) if i and i not in block.avoid]
        block.tracks = []

    store.update_project(project_id, drop)
    return jobs.start(project_id, "music")


@router.patch("/projects/{project_id}/music/{block_id}/tracks/{track_id}", response_model=Project)
def update_music_track(project_id: str, block_id: str, track_id: str, payload: MusicTrackIn) -> Project:
    """Leaves a track out of the video, or puts it back. Running the music stage
    afterwards composes a replacement if the block is no longer filled."""
    _music_block(_idle(project_id), block_id)

    def apply(p: Project) -> None:
        for track in _music_block(p, block_id).tracks:
            if track.id == track_id:
                track.enabled = payload.enabled
                # a track left out of one video is not offered to the next
                music_library.set_rejected(
                    music_library.library_id(track, store.project_dir(project_id) / "music"), not payload.enabled
                )

    return store.update_project(project_id, apply)


@router.get("/projects/{project_id}/music/library")
def music_library_usage(project_id: str) -> dict:
    """Which of the project's tracks came from the music library, and which other
    videos each of its tracks plays in: {"enabled", "tracks": {track id: {...}}}."""
    project = _load(project_id)
    return {"enabled": music_library.root() is not None, "tracks": music_library.usage(project)}


@router.post("/projects/{project_id}/music/replan", response_model=Project)
def replan_music(project_id: str) -> Project:
    """Divides the narration into blocks again and composes everything anew."""
    project = _idle(project_id)
    if not project.beats:
        raise HTTPException(400, "Build beats first")
    if not current_lumean_api_key():
        raise HTTPException(400, "Add the Lumean API key in Settings first")
    store.update_project(project_id, lambda p: setattr(p, "music", []))
    return jobs.start(project_id, "music")


@router.get("/projects/{project_id}/music/files/{name}")
def get_music_file(project_id: str, name: str):
    _load(project_id)
    base = (store.project_dir(project_id) / "music").resolve()
    path = (base / name).resolve()
    if path.parent != base or not path.is_file():
        raise HTTPException(404, "File not found")
    return FileResponse(path)


# --- standard sound effects (shared by all projects) ---------------------------


class SoundOut(BaseModel):
    name: str
    label: str
    used_for: str
    prompt: str
    files: list[str]


def _sounds() -> list[SoundOut]:
    from app.editor import sounds

    library = sounds.load_library()
    return [
        SoundOut(name=s.name, label=s.label, used_for=s.used_for, prompt=s.prompt, files=library.get(s.name, []))
        for s in sounds.STANDARD
    ]


@router.get("/sounds", response_model=list[SoundOut])
def list_sounds() -> list[SoundOut]:
    try:
        return _sounds()
    except store.RootNotConfigured:
        return []


@router.post("/sounds/{name}/generate", response_model=list[SoundOut])
def generate_sound(name: str) -> list[SoundOut]:
    """Generates the sound's variants through Lumean (paid, a few seconds of sound
    each), replacing the ones it had. Every project uses them from then on."""
    from app.editor import sounds

    if not current_lumean_api_key():
        raise HTTPException(400, "Add the Lumean API key in Settings first")
    try:
        sounds.generate(name)
    except KeyError:
        raise HTTPException(404, f"Unknown sound: {name}")
    except (LumeanError, RuntimeError) as exc:
        raise HTTPException(502, str(exc))
    return _sounds()


@router.get("/sounds/files/{name}")
def get_sound_file(name: str):
    from app.editor import sounds

    base = sounds.library_dir().resolve()
    path = (base / name).resolve()
    if path.parent != base or not path.is_file():
        raise HTTPException(404, "File not found")
    return FileResponse(path)


@router.get("/projects/{project_id}/photos/{name}")
def get_photo_file(project_id: str, name: str):
    """A photo fetched from Wikimedia Commons for one of the project's shots."""
    _load(project_id)
    base = (store.project_dir(project_id) / "photos").resolve()
    path = (base / name).resolve()
    if path.parent != base or not path.is_file():
        raise HTTPException(404, "File not found")
    return FileResponse(path)


class SlotUpdateIn(BaseModel):
    selected: int | None = None
    locked: bool | None = None
    # the frame the selected candidate shows as a still (seconds in its file); sent
    # as null, goes back to the scene's keyframe
    still_at: float | None = None


class SlotSearchIn(BaseModel):
    text_query: str = ""
    visual_query: str = ""
    shows: list[str] | None = None  # None = keep the slot's current choice


def _editable(project_id: str, beat_id: str, slot_index: int) -> Project:
    project = _load(project_id)
    if project.job and project.job.status == "running":
        raise HTTPException(409, "A stage is running - wait for it to finish")
    beat = next((b for b in project.beats if b.id == beat_id), None)
    if beat is None or not 0 <= slot_index < len(beat.slots):
        raise HTTPException(404, "Slot not found")
    return project


@router.patch("/projects/{project_id}/beats/{beat_id}/slots/{slot_index}", response_model=Project)
def update_slot(project_id: str, beat_id: str, slot_index: int, payload: SlotUpdateIn) -> Project:
    """Picking a different candidate locks the slot, so re-running automatic
    selection keeps the user's choice."""
    project = _editable(project_id, beat_id, slot_index)
    slot = next(b for b in project.beats if b.id == beat_id).slots[slot_index]
    if payload.selected is not None and not 0 <= payload.selected < len(slot.candidates):
        raise HTTPException(400, "No such candidate")

    def apply(p: Project) -> None:
        target = next(b for b in p.beats if b.id == beat_id).slots[slot_index]
        if payload.selected is not None:
            target.selected = payload.selected
            target.locked = True
        if payload.locked is not None:
            target.locked = payload.locked
        if "still_at" in payload.model_fields_set and target.selected is not None:
            target.candidates[target.selected].still_at = payload.still_at
            target.locked = payload.locked if payload.locked is not None else True

    return store.update_project(project_id, apply)


@router.get("/projects/{project_id}/beats/{beat_id}/slots/{slot_index}/candidates/{index}/still")
def candidate_still(project_id: str, beat_id: str, slot_index: int, index: int, width: int = 640):
    """The frame a candidate would show as a still in the video."""
    from app.api.media import frame_jpeg
    from app.config import data_path
    from app.editor.render import still_time

    project = _load(project_id)
    beat = next((b for b in project.beats if b.id == beat_id), None)
    if beat is None or not 0 <= slot_index < len(beat.slots) or not 0 <= index < len(beat.slots[slot_index].candidates):
        raise HTTPException(404, "No such candidate")
    candidate = beat.slots[slot_index].candidates[index]
    if candidate.kind != "video" or not Path(candidate.file_path).exists():
        raise HTTPException(404, "Not footage")
    at = still_time(candidate)
    if candidate.still_at is None and width <= 768:
        # most stills show their scene's keyframe, which is already on disk at that size
        from app.db.models import Scene
        from app.db.session import SessionLocal
        from app.pipeline.stages.extract_keyframes import _OFFSETS_BY_COUNT

        db = SessionLocal()
        try:
            scene = db.get(Scene, candidate.scene_id)
            # a scene re-indexed since has other keyframes
            same = scene is not None and abs(scene.start_time - candidate.scene_start) < 1e-3 and abs(scene.end_time - candidate.scene_end) < 1e-3
            paths = list(scene.keyframe_paths or []) if same else []
        finally:
            db.close()
        duration = candidate.scene_end - candidate.scene_start
        for offset, path in zip(_OFFSETS_BY_COUNT.get(len(paths), []), paths):
            if abs(candidate.scene_start + duration * offset - at) < 1e-3 and data_path(path).exists():
                return FileResponse(data_path(path), media_type="image/jpeg")
    return FileResponse(frame_jpeg(candidate.file_path, at, width), media_type="image/jpeg")


@router.post("/projects/{project_id}/beats/{beat_id}/slots/{slot_index}/search", response_model=Project)
def search_slot_again(project_id: str, beat_id: str, slot_index: int, payload: SlotSearchIn) -> Project:
    """Re-searches one slot with edited queries (free, local), avoiding footage
    used by every other slot, and locks the result."""
    from app.db.session import SessionLocal
    from app.editor.footage import SearchContext, Usage, choose, search_slot

    project = _editable(project_id, beat_id, slot_index)
    if not payload.text_query.strip() and not payload.visual_query.strip():
        raise HTTPException(400, "Enter a query")

    target = next(b for b in project.beats if b.id == beat_id).slots[slot_index]
    target.text_query = payload.text_query.strip()
    target.visual_query = payload.visual_query.strip()
    if payload.shows is not None:
        target.shows = payload.shows

    db = SessionLocal()
    try:
        usage = Usage()
        for beat in project.beats:
            for slot in beat.slots:
                if slot is not target and slot.selected is not None:
                    usage.add(slot.candidates[slot.selected])
        choose(target, search_slot(SearchContext(db, project), target), usage)
    finally:
        db.close()
    target.locked = True

    def apply(p: Project) -> None:
        next(b for b in p.beats if b.id == beat_id).slots[slot_index] = target

    return store.update_project(project_id, apply)


@router.get("/frames", response_model=list[str])
def get_frames() -> list[str]:
    """Frame images available to the framed layout, besides the built-in ones."""
    from app.editor.render import list_frames

    try:
        return list_frames()
    except store.RootNotConfigured:
        return []


@router.put("/frames", response_model=list[str])
async def upload_frame(filename: str, request: Request) -> list[str]:
    """Raw request body = a PNG with a transparent opening for the clip."""
    from app.editor.render import frames_dir, list_frames

    if Path(filename).suffix.lower() != ".png":
        raise HTTPException(400, "Frame must be a PNG with transparency")
    name = re.sub(r"[^A-Za-z0-9_-]+", "-", Path(filename).stem).strip("-")
    if not name or name in ("border", "none"):
        raise HTTPException(400, "Choose a different file name")
    body = await request.body()
    if not body:
        raise HTTPException(400, "Empty upload")
    try:
        (frames_dir() / f"{name}.png").write_bytes(body)
    except store.RootNotConfigured:
        raise HTTPException(409, "Editor projects folder is not set")
    return list_frames()


@router.get("/projects/{project_id}/output")
def get_output(project_id: str):
    return FileResponse(_output_path(project_id), media_type="video/mp4")


def _output_path(project_id: str) -> Path:
    project = _load(project_id)
    if not project.output_file:
        raise HTTPException(404, "Project has not been rendered")
    path = store.project_dir(project_id) / project.output_file
    if not path.is_file():
        raise HTTPException(404, "Rendered file missing on disk")
    return path


@router.get("/projects/{project_id}/output-path")
def get_output_path(project_id: str) -> dict:
    """Where the rendered video is on disk, to copy."""
    return {"path": str(_output_path(project_id).resolve())}


@router.post("/projects/{project_id}/output/reveal")
def reveal_output(project_id: str) -> dict:
    """Opens Explorer on the rendered video's folder with the file selected."""
    path = _output_path(project_id).resolve()
    subprocess.Popen(["explorer", f"/select,{path}"])
    return {"path": str(path)}


class SlotManualIn(BaseModel):
    # either a scene (the clip is cut around its indexed picture, on shot cuts)...
    scene_id: int | None = None
    # ...or an episode and the exact second the clip should begin
    source_id: int | None = None
    start: float | None = None


@router.post("/projects/{project_id}/beats/{beat_id}/slots/{slot_index}/manual", response_model=Project)
def set_slot_manually(project_id: str, beat_id: str, slot_index: int, payload: SlotManualIn) -> Project:
    """Puts a moment the user picked by hand into a slot - for footage the search
    doesn't offer, because it is already used elsewhere or the index doesn't find it.
    The clip starts at the given time and runs for the slot's length; the slot is
    locked so automatic selection keeps it."""
    from sqlalchemy import select as sa_select

    from app.db.models import Scene, Source
    from app.db.session import SessionLocal
    from app.editor.footage import _clip_window, count_slivers
    from app.editor.store import Candidate

    project = _editable(project_id, beat_id, slot_index)
    slot = next(b for b in project.beats if b.id == beat_id).slots[slot_index]
    duration = slot.end - slot.start

    db = SessionLocal()
    try:
        if payload.scene_id is not None:
            scene = db.get(Scene, payload.scene_id)
            if scene is None:
                raise HTTPException(404, "Scene not found")
            source = db.get(Source, scene.source_id)
            # same placement as the automatic selection: around the indexed frame, no slivers
            start, _ = _clip_window(db, scene, source, duration, True)
        elif payload.source_id is not None and payload.start is not None:
            source = db.get(Source, payload.source_id)
            if source is None:
                raise HTTPException(404, "Episode not found")
            start = max(0.0, payload.start)
            if source.duration_seconds and start + duration > source.duration_seconds:
                raise HTTPException(400, "The clip would run past the end of the episode")
            # the scene at that moment, for the thumbnail and description
            scene = db.execute(
                sa_select(Scene).where(Scene.source_id == source.id, Scene.start_time <= start, Scene.end_time > start)
            ).scalars().first()
        else:
            raise HTTPException(400, "Give a scene, or an episode and a start time")
        if source.kind == "comic":
            from app.editor.comic_pages import page_candidate

            if scene is None or not scene.keyframe_paths:
                raise HTTPException(400, "Pick a page of the comic")
            candidate = page_candidate(scene, source, duration, 0.0)
        else:
            candidate = Candidate(
                scene_id=scene.id if scene else 0,
                source_id=source.id,
                file_path=source.file_path,
                show_title=source.show_title,
                book=source.book,
                episode_number=source.episode_number,
                scene_start=scene.start_time if scene else start,
                scene_end=scene.end_time if scene else start + duration,
                clip_start=round(start, 3),
                clip_end=round(start + duration, 3),
                description=scene.context_description if scene else None,
                score=0.0,
                slivers=count_slivers(db, source.id, start, start + duration),
            )
    finally:
        db.close()

    def apply(p: Project) -> None:
        target = next(b for b in p.beats if b.id == beat_id).slots[slot_index]
        # first in the list, so it sits where the chosen clip is shown; earlier hand
        # picks for this slot are replaced rather than piling up
        target.candidates = [candidate] + [c for c in target.candidates if c.score != 0.0 or c.text_score is not None or c.visual_score is not None]
        target.candidates = target.candidates[:9]
        target.selected = 0
        target.locked = True

    return store.update_project(project_id, apply)


class QuoteCreateIn(BaseModel):
    source_id: int
    clip_start: float
    clip_end: float
    # narration word the clip plays after; None = at the very end
    after_word: int | None = None


@router.post("/projects/{project_id}/quotes", response_model=Project)
def add_quote(project_id: str, payload: QuoteCreateIn) -> Project:
    """Adds a clip the user picked by hand - an episode and a time range - instead of
    one found by the quote search. It goes straight into the video."""
    import uuid

    from app.db.models import Source
    from app.db.session import SessionLocal
    from app.editor.quotes import resolve_placement, text_between
    from app.editor.store import Quote

    project = _load(project_id)
    if project.job and project.job.status == "running":
        raise HTTPException(409, "A stage is running - wait for it to finish")
    if payload.clip_end - payload.clip_start < 0.5:
        raise HTTPException(400, "The clip must end after it starts")
    if payload.clip_end - payload.clip_start > 180:
        raise HTTPException(400, "A clip can be at most three minutes long")

    db = SessionLocal()
    try:
        source = db.get(Source, payload.source_id)
        if source is None:
            raise HTTPException(404, "Episode not found")
        if source.duration_seconds and payload.clip_end > source.duration_seconds + 1:
            raise HTTPException(400, "The clip runs past the end of the episode")
        details = {
            "source_id": source.id, "file_path": source.file_path, "show_title": source.show_title,
            "book": source.book, "episode_number": source.episode_number,
        }
    finally:
        db.close()

    words = store.load_words(project_id)
    if not words:
        raise HTTPException(400, "Transcribe the narration first - a clip is placed between its sentences")
    text = text_between(payload.source_id, payload.clip_start, payload.clip_end)

    def apply(p: Project) -> None:
        target = len(words) - 1 if payload.after_word is None else payload.after_word
        after_word, at, before = resolve_placement(p, words, target)
        p.quotes.append(
            Quote(
                id=f"quote_{uuid.uuid4().hex[:8]}",
                status="approved",
                after_word=after_word,
                at=at,
                after_text=before,
                clip_start=round(max(0.0, payload.clip_start), 3),
                clip_end=round(payload.clip_end, 3),
                text=text or "(no dialogue)",
                speaker="",
                reason="Added by hand.",
                **details,
            )
        )
        p.quotes.sort(key=lambda q: q.at)

    return store.update_project(project_id, apply)


@router.delete("/projects/{project_id}/quotes/{quote_id}", response_model=Project)
def delete_quote(project_id: str, quote_id: str) -> Project:
    project = _load(project_id)
    if project.job and project.job.status == "running":
        raise HTTPException(409, "A stage is running - wait for it to finish")

    def apply(p: Project) -> None:
        p.quotes = [q for q in p.quotes if q.id != quote_id]

    return store.update_project(project_id, apply)


class QuoteUpdateIn(BaseModel):
    status: str | None = None
    after_word: int | None = None
    # seconds in the episode; either can be given alone to move one end of the clip
    clip_start: float | None = None
    clip_end: float | None = None
    # index of a piece to take out of a quote that is cut together from several
    drop_part: int | None = None


@router.patch("/projects/{project_id}/quotes/{quote_id}", response_model=Project)
def update_quote(project_id: str, quote_id: str, payload: QuoteUpdateIn) -> Project:
    """Approve/reject a proposed quote, move it, or change where its clip starts and
    ends in the episode. Once beats exist a quote can only sit between two beats, so
    the position snaps to the nearest boundary. Setting the range by hand makes a quote
    cut from pieces one continuous clip again; drop_part takes one piece out."""
    from app.editor.quotes import resolve_placement, text_between

    project = _load(project_id)
    if project.job and project.job.status == "running":
        raise HTTPException(409, "A stage is running - wait for it to finish")
    if not any(q.id == quote_id for q in project.quotes):
        raise HTTPException(404, "Quote not found")
    if payload.status is not None and payload.status not in ("proposed", "approved", "rejected"):
        raise HTTPException(400, f"Unknown status: {payload.status}")
    words = store.load_words(project_id)

    current = next(q for q in project.quotes if q.id == quote_id)
    new_start = current.clip_start if payload.clip_start is None else max(0.0, payload.clip_start)
    new_end = current.clip_end if payload.clip_end is None else payload.clip_end
    retimed = (new_start, new_end) != (current.clip_start, current.clip_end)
    if payload.drop_part is not None and (retimed or not 0 <= payload.drop_part < len(current.parts)):
        raise HTTPException(400, "No such piece in this quote")
    new_text = None
    if retimed:
        if new_end - new_start < 0.5:
            raise HTTPException(400, "The clip must end after it starts")
        if new_end - new_start > 180:
            raise HTTPException(400, "A quote can be at most three minutes long")
        # the words shown for the quote follow the clip
        new_text = text_between(current.source_id, new_start, new_end)

    def apply(p: Project) -> None:
        quote = next(q for q in p.quotes if q.id == quote_id)
        if retimed:
            quote.clip_start, quote.clip_end = round(new_start, 3), round(new_end, 3)
            quote.parts = []
            if quote.speech_end is not None and not quote.clip_start < quote.speech_end < quote.clip_end - 0.1:
                quote.speech_end = None
            if new_text:
                quote.text = new_text
        if payload.drop_part is not None and payload.drop_part < len(quote.parts):
            left = [part for i, part in enumerate(quote.parts) if i != payload.drop_part]
            quote.clip_start, quote.clip_end = left[0].start, left[-1].end
            quote.speech_end = left[-1].speech_end
            quote.text = " … ".join(part.text for part in left)
            quote.parts = left if len(left) > 1 else []
        if payload.status is not None:
            quote.status = payload.status
        if payload.after_word is not None:
            quote.after_word, quote.at, quote.after_text = resolve_placement(p, words, payload.after_word)
        p.quotes.sort(key=lambda q: q.at)

    return store.update_project(project_id, apply)


@router.get("/projects/{project_id}/preview")
def get_preview(project_id: str, slot: int = 0, format: str | None = None):
    """A still of one clip with the project's current look applied. `format` ("4:3",
    "16:9" or "wide") asks for a clip of that format - from the project, the library,
    or a placeholder; "comic" for a comic page, from the project or the library."""
    from app.editor.render import COMIC, FORMATS, render_preview

    project = _load(project_id)
    if format is not None and format not in (*FORMATS, COMIC):
        raise HTTPException(400, f"Unknown format: {format}")
    try:
        path = render_preview(project, slot, format)
    except RuntimeError as exc:
        raise HTTPException(400, str(exc))
    return Response(path.read_bytes(), media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@router.get("/projects/{project_id}/preview-info")
def get_preview_info(project_id: str) -> dict:
    """For each format: where its preview clips come from and how many there are."""
    from app.editor.render import COMIC, FORMATS, preview_clips

    project = _load(project_id)
    info = {}
    for fmt in (*FORMATS, COMIC):
        source, clips = preview_clips(project, fmt)
        info[fmt] = {"source": source, "count": len(clips)}
    return info


@router.get("/default-look")
def get_default_look() -> dict:
    """The look new projects start with."""
    return store.default_settings().model_dump()


@router.post("/projects/{project_id}/save-default-look")
def save_default_look(project_id: str) -> dict:
    """Makes this project's look (everything on its Look tab) the starting point for
    new projects. Existing projects are not changed."""
    project = _load(project_id)
    set_editor_default_look(project.settings.model_dump())
    return store.default_settings().model_dump()
