import time
from pathlib import Path

from fastapi import APIRouter, HTTPException

from app.config import BACKEND_DIR, LOGS_DIR

router = APIRouter(prefix="/api/logs", tags=["logs"])

# Fixed set - the name in the URL is only ever a key into this, never a path.
LOG_FILES: dict[str, tuple[str, Path]] = {
    "captioning": ("Captioning worker (paid)", LOGS_DIR / "worker.log"),
    "local": ("Local worker (free)", LOGS_DIR / "local_worker.log"),
    "backend": ("Backend / API / editor", LOGS_DIR / "uvicorn.log"),
    "frontend": ("Frontend dev server", BACKEND_DIR.parent / ".run" / "frontend.log"),
}
MAX_LINES = 2000
# enough for MAX_LINES of ordinary log lines without reading a whole 100MB file
TAIL_BYTES = 512 * 1024


@router.get("")
def list_logs() -> list[dict]:
    result = []
    for name, (label, path) in LOG_FILES.items():
        exists = path.is_file()
        stat = path.stat() if exists else None
        result.append(
            {
                "name": name,
                "label": label,
                "exists": exists,
                "size": stat.st_size if stat else 0,
                "modified": stat.st_mtime if stat else None,
                "active": bool(stat) and time.time() - stat.st_mtime < 60,
            }
        )
    return result


@router.get("/{name}")
def tail_log(name: str, lines: int = 300) -> dict:
    if name not in LOG_FILES:
        raise HTTPException(404, f"Unknown log: {name}")
    _, path = LOG_FILES[name]
    if not path.is_file():
        return {"name": name, "lines": [], "size": 0}

    size = path.stat().st_size
    with path.open("rb") as f:
        f.seek(max(0, size - TAIL_BYTES))
        text = f.read().decode("utf-8", errors="replace")
    all_lines = text.splitlines()
    if size > TAIL_BYTES:
        all_lines = all_lines[1:]  # the first line was probably cut in half by the seek
    return {"name": name, "lines": all_lines[-min(max(lines, 1), MAX_LINES):], "size": size}
