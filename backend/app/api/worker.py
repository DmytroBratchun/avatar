import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException

from app.config import BACKEND_DIR, DATA_DIR

router = APIRouter(prefix="/api/worker", tags=["worker"])

STALE_AFTER_SECONDS = 30
WORKER_PID_FILE = DATA_DIR / "worker.pid"


def _read_heartbeat(path: Path) -> dict:
    if not path.exists():
        return {"running": False, "reason": "no heartbeat file - worker never started"}

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"running": False, "reason": "heartbeat file unreadable"}

    if payload.get("status") == "stopped":
        return {"running": False, "reason": "stopped", "last_seen": payload.get("last_seen")}

    last_seen = dt.datetime.fromisoformat(payload["last_seen"])
    age_seconds = (dt.datetime.now(dt.timezone.utc) - last_seen).total_seconds()
    alive = age_seconds < STALE_AFTER_SECONDS

    return {
        "running": alive,
        "reason": None if alive else f"heartbeat stale ({age_seconds:.0f}s old)",
        "status": payload.get("status"),
        "current_source_id": payload.get("current_source_id"),
        "current_episode": payload.get("current_episode"),
        "detail": payload.get("detail"),
        "last_seen": payload.get("last_seen"),
    }


@router.get("/status")
def worker_status() -> dict:
    """Main worker - runs the full pipeline including paid captioning."""
    return _read_heartbeat(DATA_DIR / "worker_heartbeat.json")


@router.get("/local-status")
def local_worker_status() -> dict:
    """Local-only worker - transcribe/shots/scenes/keyframes, never calls the API."""
    return _read_heartbeat(DATA_DIR / "local_worker_heartbeat.json")


def _pid_is_alive(pid: int) -> bool:
    result = subprocess.run(
        ["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True
    )
    return str(pid) in result.stdout


def _find_worker_pids() -> list[int]:
    """Every process running the captioning worker, found by command line. The venv's
    python.exe is a launcher that starts the real interpreter (python3.x.exe) as a
    child, so there are normally two, and the pid file only knows the launcher."""
    result = subprocess.run(
        [
            "powershell", "-NoProfile", "-Command",
            "Get-CimInstance Win32_Process "
            "| Where-Object { $_.Name -like 'python*' -and $_.CommandLine -like '*app.pipeline.worker*' } "
            "| Select-Object -ExpandProperty ProcessId",
        ],
        capture_output=True,
        text=True,
    )
    return [int(line) for line in result.stdout.split() if line.strip().isdigit()]


def _mark_stopped() -> None:
    """A killed worker can't update its own heartbeat, and the old one keeps looking
    alive for another 30s - so say it was stopped."""
    payload = {"status": "stopped", "last_seen": dt.datetime.now(dt.timezone.utc).isoformat()}
    (DATA_DIR / "worker_heartbeat.json").write_text(json.dumps(payload), encoding="utf-8")


@router.post("/start")
def start_worker() -> dict:
    """Spawns the paid captioning worker (app.pipeline.worker) as a subprocess.
    This is the one stage that costs real money/subscription usage, so it's never
    auto-started - only ever launched here, explicitly, from a person clicking a
    button that says so."""
    if WORKER_PID_FILE.exists():
        pid = int(WORKER_PID_FILE.read_text(encoding="utf-8").strip())
        if _pid_is_alive(pid):
            raise HTTPException(409, "Captioning worker is already running")

    existing = _find_worker_pids()
    if existing:
        WORKER_PID_FILE.write_text(str(existing[0]), encoding="utf-8")
        raise HTTPException(409, "Captioning worker is already running")

    process = subprocess.Popen(
        [sys.executable, "-m", "app.pipeline.worker"],
        cwd=str(BACKEND_DIR),
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
    WORKER_PID_FILE.write_text(str(process.pid), encoding="utf-8")
    return {"started": True, "pid": process.pid}


@router.post("/stop")
def stop_worker() -> dict:
    """Force-kills the paid captioning worker and anything it started. A scene
    mid-caption-call when this hits just doesn't get committed - it stays
    caption_status='pending'/'error' and picks back up cleanly next run, nothing
    corrupts. Safe to call when nothing is running."""
    pids = set(_find_worker_pids())
    if WORKER_PID_FILE.exists():
        try:
            pid = int(WORKER_PID_FILE.read_text(encoding="utf-8").strip())
            if _pid_is_alive(pid):
                pids.add(pid)
        except ValueError:
            pass

    for pid in pids:
        # /T: the whole tree - the real interpreter and any claude call in flight
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True)
    WORKER_PID_FILE.unlink(missing_ok=True)
    _mark_stopped()
    return {"stopped": True, "was_running": bool(pids)}


# --- Claude subscription limits -----------------------------------------------

# Captioning and every editor LLM call run through the Claude Code CLI and spend the
# Claude subscription, which has a 5-hour and a weekly limit. This asks Anthropic the
# way Claude Code's own /usage does, with the token the CLI keeps after `claude login`;
# the endpoint is not a documented API, so anything unexpected is reported as unknown.
CLAUDE_CREDENTIALS = Path.home() / ".claude" / ".credentials.json"
CLAUDE_USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
USAGE_CACHE_SECONDS = 60
_usage_cache: tuple[float, dict] | None = None


@router.get("/claude-usage")
def claude_usage() -> dict:
    """{"available", "session": {"percent", "resets_at"}, "week": {...}} - how much
    of the subscription's 5-hour and weekly limits is used."""
    import time
    import urllib.error
    import urllib.request

    global _usage_cache
    if _usage_cache and time.monotonic() - _usage_cache[0] < USAGE_CACHE_SECONDS:
        return _usage_cache[1]
    result: dict = {"available": False}
    try:
        token = json.loads(CLAUDE_CREDENTIALS.read_text(encoding="utf-8"))["claudeAiOauth"]["accessToken"]
        request = urllib.request.Request(
            CLAUDE_USAGE_URL, headers={"Authorization": f"Bearer {token}", "anthropic-beta": "oauth-2025-04-20"}
        )
        with urllib.request.urlopen(request, timeout=15) as response:
            data = json.load(response)

        def window(key: str) -> dict | None:
            value = data.get(key)
            return {"percent": value.get("utilization"), "resets_at": value.get("resets_at")} if value else None

        result = {"available": True, "session": window("five_hour"), "week": window("seven_day")}
    except (OSError, KeyError, ValueError, urllib.error.URLError) as exc:
        # an expired token is refreshed by the CLI the next time it runs
        result = {"available": False, "reason": str(exc)[:200]}
    _usage_cache = (time.monotonic(), result)
    return result
