import json
import os
import subprocess

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import BACKEND_DIR
from app.db.models import Source, TranscriptLine
from app.pipeline.stages.caption import OLLAMA_URL, _client, _episode_primer, _real_api_cost
from app.services.runtime_settings import current_caption_model, current_caption_provider

SUMMARY_INSTRUCTION = (
    "Write a concise summary (3-6 sentences) of this episode's transcript covering: which "
    "characters appear, the main plot events in order, and key locations. This will be "
    "prepended as shared context to short, speaker-less transcript excerpts for retrieval, "
    "so include concrete character names and plot points, not vague generalities."
)


def _summarize_via_api(source: Source, transcript_text: str) -> tuple[str, float | None]:
    model = current_caption_model()
    prompt = f"{_episode_primer(source)}\n\n{SUMMARY_INSTRUCTION}\n\nFull transcript:\n{transcript_text}"
    response = _client().messages.create(
        model=model, max_tokens=512, messages=[{"role": "user", "content": prompt}]
    )
    return response.content[0].text, _real_api_cost(response.usage, model)


def _summarize_via_claude_code(source: Source, transcript_text: str) -> tuple[str, float | None]:
    prompt = f"{_episode_primer(source)}\n\n{SUMMARY_INSTRUCTION}\n\nFull transcript:\n{transcript_text}"
    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)
    result = subprocess.run(
        ["claude", "-p", prompt, "--model", current_caption_model(), "--output-format", "json"],
        cwd=str(BACKEND_DIR), env=env, capture_output=True, text=True, timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(f"claude CLI exited {result.returncode}: {result.stderr.strip()[:500]}")
    payload = json.loads(result.stdout)
    if payload.get("is_error"):
        raise RuntimeError(f"claude CLI reported an error: {payload.get('result')}")
    return payload["result"], payload.get("total_cost_usd")


def _summarize_via_local(source: Source, transcript_text: str) -> tuple[str, float | None]:
    import httpx

    prompt = f"{_episode_primer(source)}\n\n{SUMMARY_INSTRUCTION}\n\nFull transcript:\n{transcript_text}"
    response = httpx.post(
        OLLAMA_URL,
        json={"model": current_caption_model(), "messages": [{"role": "user", "content": prompt}], "stream": False},
        timeout=180,
    )
    response.raise_for_status()
    return response.json()["message"]["content"], None


def summarize_episode(source: Source, transcript_text: str) -> tuple[str, float | None]:
    provider = current_caption_provider()
    if provider == "claude_code":
        return _summarize_via_claude_code(source, transcript_text)
    if provider == "local":
        return _summarize_via_local(source, transcript_text)
    return _summarize_via_api(source, transcript_text)


def run_episode_summary(db: Session, source: Source) -> None:
    """One text-only LLM call per episode (no images) - much cheaper than scene
    captioning. Only runs when explicitly queued via episode_summary_requested."""
    lines = list(
        db.execute(select(TranscriptLine).where(TranscriptLine.source_id == source.id).order_by(TranscriptLine.start_time)).scalars()
    )
    transcript_text = "\n".join(line.text for line in lines)
    if not transcript_text.strip():
        source.episode_summary_requested = False
        source.current_stage_detail = None
        db.commit()
        return

    summary, cost = summarize_episode(source, transcript_text)
    source.episode_summary = summary
    source.episode_summary_model = f"{current_caption_provider()}:{current_caption_model()}"
    if cost is not None:
        source.caption_cost_usd = (source.caption_cost_usd or 0.0) + cost
    source.episode_summary_requested = False
    source.current_stage_detail = None
    db.commit()
