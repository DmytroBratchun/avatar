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

    last_seen = dt.datetime.fromisoformat(payload["last_seen"])
    age_seconds = (dt.datetime.now(dt.timezone.utc) - last_seen).total_seconds()
    alive = age_seconds < STALE_AFTER_SECONDS

    return {
        "running": alive,
        "reason": None if alive else f"heartbeat stale ({age_seconds:.0f}s old)",
        "status": payload.get("status"),
        "current_source_id": payload.get("current_source_id"),
        "current_episode": payload.get("current_episode"),
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


def _find_worker_pid_by_command_line() -> int | None:
    """Falls back to searching for the worker process directly when worker.pid is
    missing/stale (e.g. it was started outside /start, or the pidfile was lost) -
    without this, Stop 404s even though a real worker process is running."""
    result = subprocess.run(
        [
            "powershell", "-NoProfile", "-Command",
            "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" "
            "| Where-Object { $_.CommandLine -like '*app.pipeline.worker*' } "
            "| Select-Object -ExpandProperty ProcessId",
        ],
        capture_output=True,
        text=True,
    )
    for line in result.stdout.splitlines():
        line = line.strip()
        if line.isdigit():
            return int(line)
    return None


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

    existing_pid = _find_worker_pid_by_command_line()
    if existing_pid is not None:
        WORKER_PID_FILE.write_text(str(existing_pid), encoding="utf-8")
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
    """Force-kills the paid captioning worker. A scene mid-caption-call when this
    hits just doesn't get committed - it stays caption_status='pending'/'error'
    and picks back up cleanly next run, nothing corrupts."""
    pid: int | None = None
    if WORKER_PID_FILE.exists():
        pid = int(WORKER_PID_FILE.read_text(encoding="utf-8").strip())
        if not _pid_is_alive(pid):
            pid = None

    if pid is None:
        pid = _find_worker_pid_by_command_line()

    if pid is None:
        WORKER_PID_FILE.unlink(missing_ok=True)
        raise HTTPException(404, "No captioning worker process found")

    subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
    WORKER_PID_FILE.unlink(missing_ok=True)
    return {"stopped": True}
