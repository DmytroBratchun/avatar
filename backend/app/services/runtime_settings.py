"""User-editable overrides on top of the static env-based config (app.config.settings).

Stored in a local JSON file (like video-gen's settings.json) so they can be changed
from the UI at runtime without restarting the server or touching .env. Only
non-empty fields here override the env default; leave a field unset to fall back.
"""

import json
import threading
from pathlib import Path

from app.config import DATA_DIR, settings

SETTINGS_PATH = DATA_DIR / "settings.json"
_lock = threading.Lock()


def _read_raw() -> dict:
    if not SETTINGS_PATH.exists():
        return {}
    try:
        return json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def get_effective_settings() -> dict:
    """Current effective values, with source info so the UI can show 'using .env value'."""
    raw = _read_raw()

    return {
        "caption_provider": raw.get("caption_provider") or settings.caption_provider,
        "caption_provider_from_env": not raw.get("caption_provider"),
        "caption_model": raw.get("caption_model") or settings.caption_model,
        "caption_model_from_env": not raw.get("caption_model"),
        "anthropic_api_key": raw.get("anthropic_api_key") or settings.anthropic_api_key,
        "anthropic_api_key_from_env": not raw.get("anthropic_api_key") and bool(settings.anthropic_api_key),
    }


def update_settings(
    caption_provider: str | None = None,
    caption_model: str | None = None,
    anthropic_api_key: str | None = None,
) -> None:
    """None field = leave unchanged. Empty string = clear the override (fall back to .env)."""
    with _lock:
        raw = _read_raw()
        for key, value in (
            ("caption_provider", caption_provider),
            ("caption_model", caption_model),
            ("anthropic_api_key", anthropic_api_key),
        ):
            if value is None:
                continue
            if value == "":
                raw.pop(key, None)
            else:
                raw[key] = value
        SETTINGS_PATH.write_text(json.dumps(raw, indent=2), encoding="utf-8")


def editor_projects_root() -> str | None:
    """Folder holding one sub-folder per video-editor project. No default - the
    user picks where their projects live."""
    return _read_raw().get("editor_projects_root") or None


def set_editor_projects_root(path: str) -> None:
    with _lock:
        raw = _read_raw()
        raw["editor_projects_root"] = path
        SETTINGS_PATH.write_text(json.dumps(raw, indent=2), encoding="utf-8")


def editor_default_look() -> dict:
    """The look (frame, sizes, blur...) new video projects start with - whatever the
    user last saved as default from a project's Look tab."""
    value = _read_raw().get("editor_default_look")
    return value if isinstance(value, dict) else {}


def set_editor_default_look(look: dict) -> None:
    with _lock:
        raw = _read_raw()
        raw["editor_default_look"] = look
        SETTINGS_PATH.write_text(json.dumps(raw, indent=2), encoding="utf-8")


def current_caption_provider() -> str:
    raw = _read_raw()
    return raw.get("caption_provider") or settings.caption_provider


def current_caption_model() -> str:
    raw = _read_raw()
    return raw.get("caption_model") or settings.caption_model


def current_anthropic_api_key() -> str:
    raw = _read_raw()
    return raw.get("anthropic_api_key") or settings.anthropic_api_key
