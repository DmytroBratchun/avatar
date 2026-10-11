"""User-editable overrides on top of the static env-based config (app.config.settings).

Stored in a local JSON file (like video-gen's settings.json) so they can be changed
from the UI at runtime without restarting the server or touching .env. Only
non-empty fields here override the env default; leave a field unset to fall back.
"""

import json
import os
import sys
import threading
from pathlib import Path

from app.config import DATA_DIR, LIBRARY_DIR, PROJECTS_DIR, settings

SETTINGS_PATH = DATA_DIR / "settings.json"
_lock = threading.Lock()
AUTO = "auto"  # a model setting that lets Claude Code use its own default model


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
        "caption_model": current_caption_model(),  # the model captions are made with now
        "caption_model_override": raw.get("caption_model") or "",  # "" = the default model
        "caption_model_from_env": not raw.get("caption_model"),
        "anthropic_api_key": raw.get("anthropic_api_key") or settings.anthropic_api_key,
        "anthropic_api_key_from_env": not raw.get("anthropic_api_key") and bool(settings.anthropic_api_key),
        "lumean_api_key": raw.get("lumean_api_key") or "",
        "lumean_api_key_from_env": not raw.get("lumean_api_key") and bool(_environment("LUMEAN_KEY")),
        "openai_api_key": raw.get("openai_api_key") or "",
        "openai_api_key_from_env": not raw.get("openai_api_key") and bool(_environment("OPENAI_API_KEY")),
        "editor_llm_model": (raw.get("editor_llm_model") or "sonnet").strip(),
        "editor_llm_models": editor_task_models(),
    }


def update_settings(
    caption_provider: str | None = None,
    caption_model: str | None = None,
    anthropic_api_key: str | None = None,
    lumean_api_key: str | None = None,
    openai_api_key: str | None = None,
    editor_llm_model: str | None = None,
    editor_llm_models: dict[str, str] | None = None,
) -> None:
    """None field = leave unchanged. Empty string = clear the override (fall back to .env)."""
    with _lock:
        raw = _read_raw()
        for key, value in (
            ("caption_provider", caption_provider),
            ("caption_model", caption_model),
            ("anthropic_api_key", anthropic_api_key),
            ("lumean_api_key", lumean_api_key),
            ("openai_api_key", openai_api_key),
            ("editor_llm_model", editor_llm_model),
        ):
            if value is None:
                continue
            if value == "":
                raw.pop(key, None)
            else:
                raw[key] = value
        if editor_llm_models is not None:
            # kept even when empty: no key means the default overrides
            raw["editor_llm_models"] = {k: v for k, v in editor_llm_models.items() if k in EDITOR_TASKS and v}
        SETTINGS_PATH.write_text(json.dumps(raw, indent=2), encoding="utf-8")


def editor_projects_root() -> str | None:
    """Folder holding one sub-folder per video-editor project: <root>/projects,
    unless the user picked another."""
    return _read_raw().get("editor_projects_root") or str(PROJECTS_DIR)


def set_editor_projects_root(path: str) -> None:
    with _lock:
        raw = _read_raw()
        raw["editor_projects_root"] = path
        SETTINGS_PATH.write_text(json.dumps(raw, indent=2), encoding="utf-8")


# The editor's Claude calls, each of which may use its own model instead of the
# editor-wide one (the settings modal's "Per-task model overrides").
EDITOR_TASKS = ("beats", "queries", "moments", "comic_pages", "photos", "background", "quotes", "music", "voice_text")
# Overrides in place until the user changes them. Quotes depend on the model
# remembering which line is spoken where: with Sonnet recall turned up lines that
# are not in the show at all; Opus found 6 of 7.
DEFAULT_TASK_MODELS = {"quotes": "opus"}


def editor_task_models() -> dict[str, str]:
    """Per-task model overrides ("editor_llm_models" in settings.json)."""
    value = _read_raw().get("editor_llm_models")
    return dict(value) if isinstance(value, dict) else dict(DEFAULT_TASK_MODELS)


def editor_llm_model(task: str | None = None) -> str | None:
    """Model for one of the video editor's Claude calls: the task's override, else
    "editor_llm_model" - Sonnet unless set otherwise, as these are text-only reading
    and planning tasks and Claude Code on its own would pick Opus (~2.5x the price).
    "auto" (None here) lets Claude Code choose."""
    value = (task and editor_task_models().get(task)) or _read_raw().get("editor_llm_model") or "sonnet"
    return None if value.strip().lower() == AUTO else value.strip()


def caption_only_undescribed() -> bool:
    """With "caption_scope": "undescribed" in settings.json, paid captioning skips
    scenes that already have a description from the fan wiki (Scene.wiki_description)
    and only fills the gaps. Default ("all") captions every scene."""
    return (_read_raw().get("caption_scope") or "all") == "undescribed"


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


def editor_music_library() -> str | None:
    """Folder of the music library - every composed track with its prompt, kept for
    later videos to reuse: <root>/library/music, unless settings.json names another
    ("music_library")."""
    return _read_raw().get("music_library") or str(LIBRARY_DIR / "music")


def editor_default_visual_notes() -> str:
    """The visual wishes ("more close-ups, fights over talk") new video projects start
    with - whatever the user last saved as default."""
    return (_read_raw().get("editor_default_visual_notes") or "").strip()


def set_editor_default_visual_notes(text: str) -> None:
    with _lock:
        raw = _read_raw()
        raw["editor_default_visual_notes"] = text
        SETTINGS_PATH.write_text(json.dumps(raw, indent=2), encoding="utf-8")


def editor_default_voice() -> dict:
    """The voice and its settings new video projects start with - whatever the user
    last saved as default from a project's Voice tab."""
    value = _read_raw().get("editor_default_voice")
    return value if isinstance(value, dict) else {}


def set_editor_default_voice(voice: dict) -> None:
    with _lock:
        raw = _read_raw()
        raw["editor_default_voice"] = voice
        SETTINGS_PATH.write_text(json.dumps(raw, indent=2), encoding="utf-8")


def current_caption_provider() -> str:
    raw = _read_raw()
    return raw.get("caption_provider") or settings.caption_provider


def current_caption_model() -> str:
    """The model captions and episode summaries are made with. For Claude it is the
    "captions" override ("caption_model"), else the default model shared with the
    editor ("editor_llm_model"); for Ollama, "caption_model" names a local model."""
    raw = _read_raw()
    value = (raw.get("caption_model") or "").strip()
    local = value.startswith("qwen")
    if current_caption_provider() == "local":
        return value if local else "qwen3-vl:8b"
    return value if value and not local else (raw.get("editor_llm_model") or "sonnet").strip()


def cli_model_args(model: str | None) -> list[str]:
    """--model for a headless `claude -p` call; none for "auto", so Claude Code picks
    the model it would use by default for the subscription."""
    return [] if not model or model.strip().lower() == AUTO else ["--model", model]


# The API takes a model's name, not Claude Code's aliases.
API_MODELS = {"sonnet": "claude-sonnet-5", "opus": "claude-opus-5", "haiku": "claude-haiku-4-5", AUTO: "claude-sonnet-5"}


def api_model(model: str) -> str:
    """The model for an Anthropic API call: an alias (or "auto", meant for the Claude
    Code CLI) becomes the model it stands for."""
    return API_MODELS.get(model.strip().lower(), model)


def current_anthropic_api_key() -> str:
    raw = _read_raw()
    return raw.get("anthropic_api_key") or settings.anthropic_api_key


def current_lumean_api_key() -> str:
    """Key for the Lumean API, which voices the editor's narration (app/services/lumean.py):
    the one saved in settings, else the LUMEAN_KEY environment variable."""
    return (_read_raw().get("lumean_api_key") or "").strip() or _environment("LUMEAN_KEY")


def current_openai_api_key() -> str:
    """Key for the OpenAI API (planned: generating characters on a clear background
    as elements of a video): the one saved in settings, else OPENAI_API_KEY."""
    return (_read_raw().get("openai_api_key") or "").strip() or _environment("OPENAI_API_KEY")


def _environment(name: str) -> str:
    """An environment variable, also when it was set for the Windows user after this
    process (or the terminal it was started from) began - those are read from the
    registry, where Windows keeps them."""
    value = os.environ.get(name, "").strip()
    if value or sys.platform != "win32":
        return value
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            return str(winreg.QueryValueEx(key, name)[0]).strip()
    except OSError:
        return ""
