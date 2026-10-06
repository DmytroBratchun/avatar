import base64
import json
import logging
import os
import re
import subprocess
import time
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.config import BACKEND_DIR, settings
from app.db.models import Scene, Source, TranscriptLine
from app.services.runtime_settings import (
    caption_only_undescribed,
    current_anthropic_api_key,
    current_caption_model,
    current_caption_provider,
)
from app.services.universes import Universe, universe_for_show

logger = logging.getLogger(__name__)

# This many scenes failing in a row means captioning itself is down (usage limit
# reached, CLI logged out, no network) rather than something about one scene.
MAX_CONSECUTIVE_FAILURES = 5


class CaptioningUnavailable(Exception):
    """Raised to stop captioning instead of failing every remaining scene."""

def build_caption_schema(universe: Universe) -> dict:
    """What the model returns for a scene. Tags exist for exact filtering - who is
    in the scene and where it is - so there are only two kinds, both narrow.
    Everything else about the scene belongs in the descriptions, which are searched
    by meaning. (Free-form mood/action tags were dropped: thousands of one-off tags
    filter nothing.)"""
    properties = {
        "visual_description": {
            "type": "string",
            "description": "Literal, generic description of what is visually happening (one sentence).",
        },
        "context_description": {
            "type": "string",
            "description": (
                "Specific narrative description of this moment using character names and plot context - "
                "who does what to whom, and why it matters in the story. Not generic."
            ),
        },
        "character_tags": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "One entry per named character who appears in the scene. Use each character's usual name, "
                "spelled the same way every time (reuse the spelling from the known characters below when "
                "given). Never combine several characters into one entry. Leave out unnamed extras."
            ),
        },
        "place_tags": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "The named place where this scene happens, or an empty list. Use the name the show itself "
                "uses for a city, village, island, temple, ship or landmark. Stay at the level of the place, "
                "not a spot inside it: the city, not a street, room or building in it - details belong in "
                "the description. One place per scene; two only if the scene clearly moves between two "
                "named places. Leave it empty when the place has no name in the show (a forest, a road, "
                "open sea, a cave) or when you cannot tell where this is - do not describe the scenery here "
                "and do not guess. A dream, vision or flashback is not a place: give where it is shown to "
                "happen, or nothing. Reuse the exact spelling of a known place below when one applies."
            ),
        },
    }
    required = ["visual_description", "context_description", "character_tags", "place_tags"]
    if universe.regions:
        properties["region"] = {
            "type": "string",
            "enum": universe.regions,
            "description": (
                "The part of the world this scene takes place in. " + universe.region_notes
            ).strip(),
        }
        required.append("region")
    return {"type": "object", "properties": properties, "required": required}


INSTRUCTION_TEXT = (
    "Describe this scene: what's literally visible, a specific narrative description using character "
    "names and plot context (not generic), which named characters appear, and where it takes place."
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
    """One or two sentences telling the model what it is looking at. A film has no
    season or episode, so those parts are only mentioned when they exist."""
    universe = universe_for_show(source.show_title)
    where = source.show_title or "this show"
    if source.book is not None:
        where += f", {universe.season_word} {source.book}"
    if source.episode_number is not None:
        where += f", Episode {source.episode_number}"
    if source.episode_title:
        where += f" ('{source.episode_title}')"
    primer = f"This is a scene from {where}."
    if universe.description:
        primer += f" {universe.description}"
    return primer


HINT_SIZE = 20


def build_tag_hint(db: Session, universe: Universe) -> str:
    """The most used character and place names in this universe, so new captions
    spell them the same way. Deliberately short: the commonest names are the ones
    worth repeating, and a long list teaches the model to add to it.

    Places are taken only from scenes captioned with the region/place prompt -
    older captions tagged locations at every level of detail and would teach the
    wrong one."""
    if not universe.shows:
        return ""
    placeholders = ",".join(f":show{i}" for i in range(len(universe.shows)))
    params = {f"show{i}": show for i, show in enumerate(universe.shows)}

    def most_used(column: str, extra: str = "") -> list[str]:
        rows = db.execute(
            text(
                f"SELECT je.value, COUNT(*) AS uses FROM scenes, json_each(scenes.{column}) je "
                f"WHERE scenes.show_title IN ({placeholders}) {extra} "
                f"GROUP BY je.value ORDER BY uses DESC LIMIT {HINT_SIZE}"
            ),
            params,
        ).all()
        return [row[0] for row in rows]

    characters = most_used("character_tags")
    places = most_used("location_tags", "AND scenes.region IS NOT NULL")
    parts = []
    if characters:
        parts.append("Known characters: " + ", ".join(characters))
    if places:
        parts.append("Known places: " + ", ".join(places))
    if not parts:
        return ""
    return "\n".join(["Names already in use - keep the exact spelling when one applies:", *parts])


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


def caption_scene_via_api(
    source: Source, scene: Scene, transcript_text: str, tag_hint: str, schema: dict
) -> tuple[dict, float | None]:
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
        tools=[
            {
                "name": "record_scene_description",
                "description": "Record a structured description of a scene from a TV show or film.",
                "input_schema": schema,
            }
        ],
        tool_choice={"type": "tool", "name": "record_scene_description"},
        messages=[{"role": "user", "content": content}],
    )
    for block in response.content:
        if block.type == "tool_use":
            return block.input, _real_api_cost(response.usage, model)
    raise RuntimeError("Claude response did not include a tool_use block")


# --- provider: headless Claude Code CLI (billed to your Claude subscription, not a metered key) ---


def caption_scene_via_claude_code(
    source: Source, scene: Scene, transcript_text: str, tag_hint: str, schema: dict
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
            "--json-schema", json.dumps(schema),
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
        # with --output-format json the CLI reports failures (rate limits, auth) on
        # stdout, leaving stderr empty - include both or the reason is lost
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"claude CLI exited {result.returncode}: {detail[:500]}")

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


def caption_scene(
    source: Source, scene: Scene, transcript_text: str, tag_hint: str, schema: dict
) -> tuple[dict, float | None]:
    provider = current_caption_provider()
    if provider == "claude_code":
        return caption_scene_via_claude_code(source, scene, transcript_text, tag_hint, schema)
    if provider == "local":
        # the local model keeps its own, narrower schema (see LOCAL_CAPTION_SCHEMA)
        return caption_scene_via_local(source, scene, transcript_text, tag_hint)
    return caption_scene_via_api(source, scene, transcript_text, tag_hint, schema)


def _tag_list(value) -> list[str]:
    """Tags as a clean list of strings, whatever the model sent. Once in a while a
    model returns a list as a broken string; stored as-is, that one scene makes
    every listing that includes it fail validation."""
    if isinstance(value, str):
        value = [part for part in re.split(r"[\[\],]", value)]
    if not isinstance(value, list):
        return []
    cleaned = [str(item).strip().strip("\"'").strip() for item in value]
    return [item for item in cleaned if item]


def run_captioning(db: Session, source: Source) -> None:
    stmt = select(Scene).where(Scene.source_id == source.id, Scene.caption_status != "done")
    if caption_only_undescribed():
        # the fan wiki already describes the rest for free
        stmt = stmt.where(Scene.wiki_description.is_(None))
    scenes = list(db.execute(stmt.order_by(Scene.start_time)).scalars())
    if not scenes:
        return

    source.status = "captioning"
    db.commit()

    universe = universe_for_show(source.show_title)
    caption_schema = build_caption_schema(universe)
    tag_hint = build_tag_hint(db, universe)
    total = len(scenes)
    consecutive_failures = 0
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
        transcript_text = "\n".join((f"{line.speaker}: " if line.speaker else "") + line.text for line in lines)

        # the claude CLI occasionally fails transiently (rate limit/server hiccup) when
        # fired off in a long rapid sequence - a couple of retries with backoff clears
        # most of these instead of leaving the scene stuck in caption_status='error'
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                result, cost = caption_scene(source, scene, transcript_text, tag_hint, caption_schema)
                scene.visual_description = result["visual_description"]
                scene.context_description = result["context_description"]
                scene.character_tags = _tag_list(result["character_tags"])
                # the local model's schema still uses the older field names
                scene.location_tags = _tag_list(result.get("place_tags", result.get("location_tags", [])))
                scene.mood_action_tags = _tag_list(result.get("mood_action_tags", []))
                scene.region = result.get("region")
                scene.caption_status = "done"
                scene.caption_error = None
                provider = current_caption_provider()
                schema, instruction = (
                    (LOCAL_CAPTION_SCHEMA, LOCAL_INSTRUCTION_TEXT)
                    if provider == "local"
                    else (caption_schema, INSTRUCTION_TEXT)
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
            logger.warning("  [source %s] scene %s caption failed after 3 attempts: %s", source.id, scene.id, last_exc)
            scene.caption_status = "error"
            scene.caption_error = str(last_exc)
            scene.caption_attempts += 1
            consecutive_failures += 1
        else:
            consecutive_failures = 0

        source.current_stage_detail = f"captioning {i + 1}/{total}"
        db.commit()
        if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            raise CaptioningUnavailable(str(last_exc))
