from fastapi import APIRouter
from pydantic import BaseModel

from app.services.runtime_settings import get_effective_settings, update_settings

router = APIRouter(prefix="/api/settings", tags=["settings"])


class SettingsOut(BaseModel):
    caption_provider: str
    caption_provider_from_env: bool
    caption_model: str
    caption_model_override: str
    caption_model_from_env: bool
    anthropic_api_key: str
    anthropic_api_key_from_env: bool
    lumean_api_key: str
    lumean_api_key_from_env: bool
    openai_api_key: str
    openai_api_key_from_env: bool
    editor_llm_model: str
    editor_llm_models: dict[str, str]


class SettingsUpdateIn(BaseModel):
    caption_provider: str | None = None
    caption_model: str | None = None
    anthropic_api_key: str | None = None
    lumean_api_key: str | None = None
    openai_api_key: str | None = None
    editor_llm_model: str | None = None
    editor_llm_models: dict[str, str] | None = None


@router.get("", response_model=SettingsOut)
def read_settings() -> dict:
    return get_effective_settings()


@router.patch("", response_model=SettingsOut)
def write_settings(payload: SettingsUpdateIn) -> dict:
    update_settings(
        caption_provider=payload.caption_provider,
        caption_model=payload.caption_model,
        anthropic_api_key=payload.anthropic_api_key,
        lumean_api_key=payload.lumean_api_key,
        openai_api_key=payload.openai_api_key,
        editor_llm_model=payload.editor_llm_model,
        editor_llm_models=payload.editor_llm_models,
    )
    return get_effective_settings()
