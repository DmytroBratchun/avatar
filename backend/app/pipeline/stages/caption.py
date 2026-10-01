import base64
import json
import os
import subprocess
import time
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.config import BACKEND_DIR, settings
from app.db.models import Scene, Source, TranscriptLine
from app.services.runtime_settings import current_anthropic_api_key, current_caption_model, current_caption_provider

CAPTION_SCHEMA = {
    "type": "object",
    "properties": {
        "visual_description": {
            "type": "string",
            "description": "Literal, generic description of what is visually happening (one sentence).",
        },
        "context_description": {
            "type": "string",
            "description": (
                "Specific narrative description using character names and plot context, "
                "e.g. 'Azula attacks Aang during the solar eclipse invasion'. Not generic."
            ),
        },
        "character_tags": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "One array entry per character who appears, e.g. ['Aang', 'Katara', 'Sokka'] - never combine "
                "multiple characters into a single comma-separated entry. Use the same name each time a "
                "recurring named character appears (reuse spelling/capitalization from the known-tags hint "
                "below when given)."
            ),
        },
        "location_tags": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "One array entry per distinct location, e.g. ['Southern Water Tribe village', 'ice cliffs'] - "
                "never combine multiple locations into a single comma-separated entry. Reuse the same spelling "
                "each time a recurring location appears."
            ),
        },
        "mood_action_tags": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "One array entry per tag, e.g. ['firebending', 'betrayal', 'comic-relief', 'grief'] - never "
                "combine multiple tags into a single comma-separated entry."
            ),
        },
    },
    "required": [
        "visual_description",
        "context_description",
        "character_tags",
        "location_tags",
        "mood_action_tags",
    ],
}

CAPTION_TOOL = {
    "name": "record_scene_description",
    "description": "Record a structured description of a scene from a TV show or film.",
    "input_schema": CAPTION_SCHEMA,
}

INSTRUCTION_TEXT = (
    "Describe this scene: what's literally visible, a specific narrative description using character "
    "names and plot context (not generic), which characters/locations appear, and mood/action tags."
)

# --- local-model variant: small open VLMs confidently invent character names, episode
# titles and plot events they don't actually know (tested and confirmed on qwen3-vl) -
# being specific but wrong pollutes the text index worse than being generic but accurate.
# This scopes the ask down to what a local model can actually do reliably: describe
# composition/setting/mood, which is exactly what's needed to find b-roll footage for
# blurred backgrounds and scenic transitions, not precise plot lookup.

LOCAL_CAPTION_SCHEMA = {
    "type": "object",
    "properties": {
        "visual_description": {
            "type": "string",
            "description": (
                "Literal description of what's visible: composition (wide/establishing shot vs "
                "close-up), setting (outdoor nature/landscape vs indoor/interior vs action), colors, "
                "weather, time of day."
            ),
        },
        "context_description": {
            "type": "string",
            "description": (
                "Only describe plot/characters if you can tell with high confidence from clear, "
                "distinctive visual features. Otherwise just restate the visual composition generically "
                "(e.g. 'a wide shot of a snowy coastline at dusk') - do not guess character names, "
                "episode titles, or invent plot events. Being generic but accurate is far better than "
                "being specific but wrong."
            ),
        },
        "character_tags": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Only include a name if you recognize the character with high confidence. Leave this "
                "empty rather than guessing - do not invent or assume characters not clearly visible."
            ),
        },
        "location_tags": {
            "type": "array",
            "items": {"type": "string"},
            "description": "One array entry per distinct location type, e.g. ['snowy coastline', 'ocean'].",
        },
        "mood_action_tags": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "One array entry per tag, e.g. ['wide-shot', 'scenic', 'calm'] - never combine multiple tags "
                "into a single comma-separated entry. Include 'scenic' or 'wide-shot' when the frame is a "
                "landscape/nature/establishing shot suitable for a blurred background."
            ),
        },
    },
    "required": [
        "visual_description",
        "context_description",
        "character_tags",
        "location_tags",
        "mood_action_tags",
    ],
}

LOCAL_INSTRUCTION_TEXT = (
    "This is a frame from a hand-drawn animated show. Do not guess specific character names, episode "
    "titles, or plot events - you cannot reliably know these from a single frame, and being wrong is "
    "worse than being generic. Describe only what's literally visible: composition, setting, colors, "
    "weather, mood. Only name a character in character_tags if you recognize them with high confidence; "
    "otherwise leave it empty. This description is used to find footage for blurred background b-roll "
    "and scenic transitions, not for precise plot lookup."
)


def _prompt_version(schema: dict, instruction: str) -> str:
    """Short fingerprint of the actual schema+instructions used, instead of a
    hand-maintained 'v2' string someone (me) forgets to bump on every prompt edit -
    any change here automatically changes what gets written to Scene.model_version,
    so stale-prompt scenes are always findable by filtering on it."""
    import hashlib

    fingerprint = json.dumps(schema, sort_keys=True) + instruction
    return "caption-" + hashlib.sha256(fingerprint.encode()).hexdigest()[:8]


def _episode_primer(source: Source) -> str:
    show = source.show_title or "this show"
    return (
        f"This is a scene from {show}, Book {source.book}, "
        f"Episode {source.episode_number} ('{source.episode_title}')."
    )


def build_tag_hint(db: Session, max_per_field: int = 60) -> str:
    """Existing location/mood tags already in the DB, so captioning reuses the same
    spelling/casing instead of inventing near-duplicate variants scene to scene."""

    def distinct_values(column: str) -> list[str]:
        rows = db.execute(
            text(f"SELECT DISTINCT je.value FROM scenes, json_each(scenes.{column}) je ORDER BY je.value")
        ).all()
        return [row[0] for row in rows][:max_per_field]

    locations = distinct_values("location_tags")
    moods = distinct_values("mood_action_tags")
    if not locations and not moods:
        return ""

    parts = ["Tags already used elsewhere in this project - reuse the exact same spelling/casing when one applies, instead of inventing a new variant:"]
    if locations:
        parts.append("Known locations: " + ", ".join(locations))
    if moods:
        parts.append("Known mood/action tags: " + ", ".join(moods))
    return "\n".join(parts)


# --- provider: direct Anthropic API (needs ANTHROPIC_API_KEY, metered billing) ---

# $/1M tokens (input, output). Cache write ~1.25x input rate, cache read ~0.1x input rate.
_API_PRICING = {
    "claude-opus-5": (5.00, 25.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-haiku-4-5": (1.00, 5.00),
}


def _real_api_cost(usage, model: str) -> float | None:
    pricing = _API_PRICING.get(model)
    if pricing is None:
        return None
    price_in, price_out = pricing
    cost = usage.input_tokens * price_in / 1e6
    cost += usage.output_tokens * price_out / 1e6
    cost += (usage.cache_creation_input_tokens or 0) * price_in * 1.25 / 1e6
    cost += (usage.cache_read_input_tokens or 0) * price_in * 0.1 / 1e6
    return cost


def _client():
    from anthropic import Anthropic

    return Anthropic(api_key=current_anthropic_api_key())


def _image_block(path: str) -> dict:
    data = base64.standard_b64encode(Path(path).read_bytes()).decode()
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": data}}


def caption_scene_via_api(source: Source, scene: Scene, transcript_text: str, tag_hint: str) -> tuple[dict, float | None]:
    primer = _episode_primer(source)
    if tag_hint:
        primer = f"{primer}\n\n{tag_hint}"
    content = [
        {"type": "text", "text": primer, "cache_control": {"type": "ephemeral"}},
        *[_image_block(p) for p in scene.keyframe_paths],
        {"type": "text", "text": f"Dialogue during this scene:\n{transcript_text or '(no dialogue)'}"},
    ]
    model = current_caption_model()
    response = _client().messages.create(
        model=model,
        max_tokens=1024,
        tools=[CAPTION_TOOL],
        tool_choice={"type": "tool", "name": "record_scene_description"},
        messages=[{"role": "user", "content": content}],
    )
    for block in response.content:
        if block.type == "tool_use":
            return block.input, _real_api_cost(response.usage, model)
    raise RuntimeError("Claude response did not include a tool_use block")


# --- provider: headless Claude Code CLI (billed to your Claude subscription, not a metered key) ---


def caption_scene_via_claude_code(
    source: Source, scene: Scene, transcript_text: str, tag_hint: str
) -> tuple[dict, float | None]:
    image_list = "\n".join(f"- {p}" for p in scene.keyframe_paths)
    prompt = (
        f"{_episode_primer(source)}\n\n"
        f"Use your Read tool to look at these {len(scene.keyframe_paths)} keyframe image(s) from the scene:\n"
        f"{image_list}\n\n"
        f"Dialogue during this scene:\n{transcript_text or '(no dialogue)'}\n\n"
        f"{INSTRUCTION_TEXT}"
        + (f"\n\n{tag_hint}" if tag_hint else "")
    )

    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)

    result = subprocess.run(
        [
            "claude", "-p", prompt,
            "--model", current_caption_model(),
            "--output-format", "json",
            "--json-schema", json.dumps(CAPTION_SCHEMA),
            "--tools", "Read",
            "--allowedTools", "Read",
            "--no-session-persistence",
        ],
        cwd=str(BACKEND_DIR),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(f"claude CLI exited {result.returncode}: {result.stderr.strip()[:500]}")

    payload = json.loads(result.stdout)
    if payload.get("is_error"):
        raise RuntimeError(f"claude CLI reported an error: {payload.get('result')}")

    structured = payload.get("structured_output")
    if not structured:
        raise RuntimeError(f"claude CLI did not return structured_output: {result.stdout[:500]}")
    return structured, payload.get("total_cost_usd")


# --- provider: local model via Ollama (free, no API/subscription cost) ---

OLLAMA_URL = "http://localhost:11434/api/chat"


def caption_scene_via_local(source: Source, scene: Scene, transcript_text: str, tag_hint: str) -> tuple[dict, float | None]:
    import httpx

    primer = _episode_primer(source)
    if tag_hint:
        primer = f"{primer}\n\n{tag_hint}"
    prompt = f"{primer}\n\n{LOCAL_INSTRUCTION_TEXT}\n\nDialogue during this scene:\n{transcript_text or '(no dialogue)'}"
    images = [base64.standard_b64encode(Path(p).read_bytes()).decode() for p in scene.keyframe_paths]

    try:
        response = httpx.post(
            OLLAMA_URL,
            json={
                "model": current_caption_model(),
                "messages": [{"role": "user", "content": prompt, "images": images}],
                "format": LOCAL_CAPTION_SCHEMA,
                "stream": False,
            },
            timeout=180,
        )
        response.raise_for_status()
    except httpx.ConnectError as exc:
        raise RuntimeError(
            "Could not reach Ollama at localhost:11434 - is it running? (`ollama serve`)"
        ) from exc

    content = response.json()["message"]["content"]
    return json.loads(content), None


def caption_scene(source: Source, scene: Scene, transcript_text: str, tag_hint: str) -> tuple[dict, float | None]:
    provider = current_caption_provider()
    if provider == "claude_code":
        return caption_scene_via_claude_code(source, scene, transcript_text, tag_hint)
    if provider == "local":
        return caption_scene_via_local(source, scene, transcript_text, tag_hint)
    return caption_scene_via_api(source, scene, transcript_text, tag_hint)


def run_captioning(db: Session, source: Source) -> None:
    scenes = list(
        db.execute(
            select(Scene).where(Scene.source_id == source.id, Scene.caption_status != "done")
        ).scalars()
    )
    if not scenes:
        return

    source.status = "captioning"
    db.commit()

    tag_hint = build_tag_hint(db)
    total = len(scenes)
    for i, scene in enumerate(scenes):
        if not scene.keyframe_paths:
            continue

        # re-check on every scene - "remove from queue" in the UI changes the status
        # from another session, and every further scene here costs a paid LLM call
        db.refresh(source, attribute_names=["status"])
        if source.status != "captioning":
            return

        lines = list(
            db.execute(
                select(TranscriptLine).where(TranscriptLine.id.in_(scene.linked_transcript_ids or []))
            ).scalars()
        )
        transcript_text = "\n".join(line.text for line in lines)

        # the claude CLI occasionally fails transiently (rate limit/server hiccup) when
        # fired off in a long rapid sequence - a couple of retries with backoff clears
        # most of these instead of leaving the scene stuck in caption_status='error'
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                result, cost = caption_scene(source, scene, transcript_text, tag_hint)
                scene.visual_description = result["visual_description"]
                scene.context_description = result["context_description"]
                scene.character_tags = result["character_tags"]
                scene.location_tags = result["location_tags"]
                scene.mood_action_tags = result["mood_action_tags"]
                scene.caption_status = "done"
                scene.caption_error = None
                provider = current_caption_provider()
                schema, instruction = (
                    (LOCAL_CAPTION_SCHEMA, LOCAL_INSTRUCTION_TEXT)
                    if provider == "local"
                    else (CAPTION_SCHEMA, INSTRUCTION_TEXT)
                )
                scene.model_version = f"{provider}:{current_caption_model()}:{_prompt_version(schema, instruction)}"
                if cost is not None:
                    source.caption_cost_usd = (source.caption_cost_usd or 0.0) + cost
                last_exc = None
                break
            except Exception as exc:
                last_exc = exc
                if attempt < 2:
                    time.sleep(3 * (attempt + 1))

        if last_exc is not None:
            scene.caption_status = "error"
            scene.caption_error = str(last_exc)
            scene.caption_attempts += 1

        source.current_stage_detail = f"captioning {i + 1}/{total}"
        db.commit()
