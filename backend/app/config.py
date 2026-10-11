import os
from pathlib import Path

from dotenv import dotenv_values
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent


def _root_dir() -> Path:
    """The one folder everything the app keeps lives in, apart from the code:
    ROOT_DIR from the environment or backend/.env, else backend/data. Inside it:

        index/     the database, keyframes, audio, comic pages, settings, logs, caches
        media/     the source videos and PDFs, one folder per universe
        library/   what videos share: music, frame images, sound effects
        projects/  the video editor's projects

    Paths to anything in it are stored relative (see data_path, media_path), so the
    whole folder can be moved by changing ROOT_DIR alone."""
    value = os.environ.get("ROOT_DIR") or dotenv_values(BACKEND_DIR / ".env").get("ROOT_DIR")
    return Path(value).expanduser().resolve() if value else BACKEND_DIR / "data"


ROOT_DIR = _root_dir()
DATA_DIR = ROOT_DIR / "index"
MEDIA_DIR = ROOT_DIR / "media"
LIBRARY_DIR = ROOT_DIR / "library"
PROJECTS_DIR = ROOT_DIR / "projects"
KEYFRAMES_DIR = DATA_DIR / "keyframes"
AUDIO_DIR = DATA_DIR / "audio"
# logs, worker heartbeats and pid files
LOGS_DIR = DATA_DIR / "logs"
# what can be made or downloaded again: frame grabs, crop measurements, wiki pages
CACHE_DIR = DATA_DIR / "cache"
# copies of the database and of data taken out of it, kept before risky changes
BACKUPS_DIR = DATA_DIR / "backups"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_DIR / ".env", extra="ignore")

    database_path: Path = DATA_DIR / "index.db"
    anthropic_api_key: str = ""
    caption_provider: str = "claude_code"  # "claude_code" (uses your Claude subscription via headless CLI) | "api" (metered ANTHROPIC_API_KEY)
    caption_model: str = "claude-opus-5"
    whisper_model_size: str = "large-v3"
    whisper_device: str = "cuda"
    whisper_compute_type: str = "float16"
    visual_embedding_model: str = "google/siglip2-so400m-patch14-384"
    text_embedding_model: str = "Qwen/Qwen3-Embedding-0.6B"

    scene_gap_seconds: float = 8.0
    scene_min_duration_seconds: float = 5.0
    scene_max_duration_seconds: float = 30.0
    shot_min_duration_seconds: float = 0.4

    # keyframe count scales with scene length - a 3s clip doesn't need 3 near-identical
    # frames, and fewer images per scene means fewer/cheaper tokens sent to captioning
    keyframes_per_scene: int = 3
    keyframe_short_scene_seconds: float = 6.0
    keyframe_medium_scene_seconds: float = 15.0


settings = Settings()


def _relative(path: str | Path, base: Path) -> str:
    try:
        return Path(path).resolve().relative_to(base.resolve()).as_posix()
    except ValueError:
        return str(path)


def data_path(stored: str | Path) -> Path:
    """A file the app made in DATA_DIR, as the database or a project stores it:
    relative to DATA_DIR. (A path stored absolute is taken as it is.)"""
    path = Path(stored)
    return path if path.is_absolute() else DATA_DIR / path


def stored_path(path: str | Path) -> str:
    """How a file inside DATA_DIR is stored: relative to it, with forward slashes."""
    return _relative(path, DATA_DIR)


def media_path(stored: str) -> str:
    """A source video or PDF as the database or a project stores it - relative to
    MEDIA_DIR - as a full path. (One stored absolute, outside MEDIA_DIR, as it is.)"""
    if not stored or Path(stored).is_absolute():
        return stored
    return str(MEDIA_DIR / stored)


def media_stored(path: str) -> str:
    """How a source file is stored: relative to MEDIA_DIR when it lies in it. Also
    the name to hash it by, so caches survive moving the media folder."""
    return _relative(path, MEDIA_DIR) if path and Path(path).is_absolute() else path


for directory in (DATA_DIR, KEYFRAMES_DIR, AUDIO_DIR, LOGS_DIR, CACHE_DIR):
    directory.mkdir(parents=True, exist_ok=True)
