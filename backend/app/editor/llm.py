import json
import os
import subprocess
from pathlib import Path

from app.services.runtime_settings import editor_llm_model


def run_claude_json(
    prompt: str, schema: dict, cwd: Path, timeout: int = 300, task: str | None = None, tools: list[str] | None = None
) -> tuple[dict, float | None]:
    """One text-only headless Claude Code call returning schema-validated JSON,
    billed to the Claude subscription like captioning. The model is the one set for
    `task` (one of runtime_settings.EDITOR_TASKS), else the editor-wide one.

    The prompt goes over stdin, not argv - a whole narration transcript blows past
    Windows' ~32k command-line limit. cwd should be outside this repo so the call
    doesn't load the repo's CLAUDE.md as context. tools: e.g. ["Read"], for a call
    that has to look at images; none by default."""
    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)

    model = editor_llm_model(task)
    result = subprocess.run(
        [
            "claude", "-p",
            *(["--model", model] if model else []),
            "--output-format", "json",
            "--json-schema", json.dumps(schema),
            "--tools", ",".join(tools or []),
            *(["--allowedTools", ",".join(tools)] if tools else []),
            "--no-session-persistence",
        ],
        input=prompt,
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=timeout,
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
