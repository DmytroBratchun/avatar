import re
import subprocess
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from app.editor import jobs, store
from app.editor.store import Project, Word
from app.services.runtime_settings import editor_projects_root, set_editor_default_look, set_editor_projects_root

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
    universe: str = ""
    shows: list[str] = []
    layout: str | None = None  # None = the saved default look's layout


class ProjectUpdateIn(BaseModel):
    title: str | None = None
    script_text: str | None = None
    shows: list[str] | None = None
    layout: str | None = None
    search_mode: str | None = None
    frame: str | None = None
    blur: float | None = None
    dim: float | None = None
    corner_radius: int | None = None
    clip_scale: float | None = None
    frame_scale: float | None = None
    corner_radii: list[int] | None = None
    background_speed: float | None = None
    wide_full_screen: bool | None = None


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
        return store.create_project(title, payload.script_text, payload.shows, payload.layout, payload.universe)
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
        if data.get("frame_scale") is not None:
            p.settings.frame_scale = min(1.5, max(0.4, data["frame_scale"]))
        if data.get("clip_scale") is not None:
            p.settings.clip_scale = min(1.5, max(0.5, data["clip_scale"]))
        if "corner_radii" in data:
            radii = data["corner_radii"]
            p.settings.corner_radii = [min(300, max(0, int(r))) for r in radii] if radii and len(radii) == 4 else None
        if data.get("background_speed") is not None:
            p.settings.background_speed = min(1.0, max(0.25, data["background_speed"]))
        if data.get("wide_full_screen") is not None:
            p.settings.wide_full_screen = data["wide_full_screen"]

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
def run_stage(project_id: str, stage: str) -> Project:
    """Runs the stage now, or queues it if the project is already running one."""
    project = _load(project_id)
    # A stage queued behind others can't be checked yet - what it needs may be exactly
    # what the stages ahead of it produce. It checks for itself when its turn comes.
    if not jobs.is_busy(project_id):
        if stage == "transcribe" and not project.audio_file:
            raise HTTPException(400, "Upload the narration audio first")
        if stage in ("beats", "quotes") and project.word_count == 0:
            raise HTTPException(400, "Transcribe the audio first")
        if stage in ("queries", "background") and not project.beats:
            raise HTTPException(400, "Build beats first")
        if stage == "render" and not any(slot.selected is not None for b in project.beats for slot in b.slots):
            raise HTTPException(400, "Find footage first")
        if stage == "select" and not any(slot.text_query or slot.visual_query for b in project.beats for slot in b.slots):
            raise HTTPException(400, "Write shot queries first")
    try:
        return jobs.start(project_id, stage)
    except KeyError:
        raise HTTPException(404, f"Unknown stage: {stage}")


@router.delete("/projects/{project_id}/queue/{stage}", response_model=Project)
def unqueue_stage(project_id: str, stage: str) -> Project:
    """Takes a waiting stage off the project's queue (the running one is not affected)."""
    _load(project_id)
    return jobs.unqueue(project_id, stage)


class SlotUpdateIn(BaseModel):
    selected: int | None = None
    locked: bool | None = None


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

    return store.update_project(project_id, apply)


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
    project = _load(project_id)
    if not project.output_file:
        raise HTTPException(404, "Project has not been rendered")
    path = store.project_dir(project_id) / project.output_file
    if not path.is_file():
        raise HTTPException(404, "Rendered file missing on disk")
    return FileResponse(path, media_type="video/mp4")


class QuoteUpdateIn(BaseModel):
    status: str | None = None
    after_word: int | None = None


@router.patch("/projects/{project_id}/quotes/{quote_id}", response_model=Project)
def update_quote(project_id: str, quote_id: str, payload: QuoteUpdateIn) -> Project:
    """Approve/reject a proposed quote, or move it. Once beats exist a quote can
    only sit between two beats, so the position snaps to the nearest boundary."""
    from app.editor.quotes import resolve_placement

    project = _load(project_id)
    if project.job and project.job.status == "running":
        raise HTTPException(409, "A stage is running - wait for it to finish")
    if not any(q.id == quote_id for q in project.quotes):
        raise HTTPException(404, "Quote not found")
    if payload.status is not None and payload.status not in ("proposed", "approved", "rejected"):
        raise HTTPException(400, f"Unknown status: {payload.status}")
    words = store.load_words(project_id)

    def apply(p: Project) -> None:
        quote = next(q for q in p.quotes if q.id == quote_id)
        if payload.status is not None:
            quote.status = payload.status
        if payload.after_word is not None:
            quote.after_word, quote.at, quote.after_text = resolve_placement(p, words, payload.after_word)
        p.quotes.sort(key=lambda q: q.at)

    return store.update_project(project_id, apply)


@router.get("/projects/{project_id}/preview")
def get_preview(project_id: str, slot: int = 0):
    """A still of one clip with the project's current look applied."""
    from app.editor.render import render_preview

    project = _load(project_id)
    try:
        path = render_preview(project, slot)
    except RuntimeError as exc:
        raise HTTPException(400, str(exc))
    return Response(path.read_bytes(), media_type="image/jpeg", headers={"Cache-Control": "no-store"})


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
