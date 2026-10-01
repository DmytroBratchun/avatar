from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BACKEND_DIR / "data"
KEYFRAMES_DIR = DATA_DIR / "keyframes"
AUDIO_DIR = DATA_DIR / "audio"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_DIR / ".env", extra="ignore")

    database_path: Path = DATA_DIR / "avatar.db"
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

for directory in (DATA_DIR, KEYFRAMES_DIR, AUDIO_DIR):
    directory.mkdir(parents=True, exist_ok=True)
