from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.orm import Session

from app.config import CACHE_DIR, data_path, media_stored
from app.db.models import Scene, Source
from app.db.session import get_db

router = APIRouter(prefix="/api/media", tags=["media"])

CHUNK_SIZE = 1024 * 1024


@router.get("/{source_id}/stream")
def stream_source(source_id: int, request: Request, db: Session = Depends(get_db)):
    source = db.get(Source, source_id)
    if source is None:
        raise HTTPException(404, "Source not found")

    path = Path(source.file_path)
    if not path.exists():
        raise HTTPException(404, "Source file missing on disk")

    file_size = path.stat().st_size
    media_type = "application/pdf" if path.suffix.lower() == ".pdf" else "video/mp4"
    range_header = request.headers.get("range")
    if range_header is None:
        return FileResponse(path, media_type=media_type, headers={"Accept-Ranges": "bytes"})

    start_str, _, end_str = range_header.replace("bytes=", "").partition("-")
    start = int(start_str) if start_str else 0
    end = int(end_str) if end_str else min(start + CHUNK_SIZE, file_size - 1)
    end = min(end, file_size - 1)
    length = end - start + 1

    def iterator():
        with path.open("rb") as f:
            f.seek(start)
            remaining = length
            while remaining > 0:
                chunk = f.read(min(CHUNK_SIZE, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    headers = {
        "Content-Range": f"bytes {start}-{end}/{file_size}",
        "Accept-Ranges": "bytes",
        "Content-Length": str(length),
    }
    return StreamingResponse(iterator(), status_code=206, media_type=media_type, headers=headers)


FRAME_CACHE_DIR = CACHE_DIR / "frames"


def frame_jpeg(video_path: str, at: float, width: int) -> Path:
    """One frame of a video as a JPEG of the given width, cached on disk."""
    import hashlib
    import subprocess

    width = max(160, min(1920, width // 2 * 2))
    key = hashlib.sha1(f"{media_stored(video_path)}|{at:.3f}|{width}".encode()).hexdigest()[:20]
    path = FRAME_CACHE_DIR / f"{key}.jpg"
    if not path.exists():
        FRAME_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        partial = path.with_suffix(".part.jpg")
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{max(0.0, at):.3f}", "-i", video_path,
             "-frames:v", "1", "-vf", f"scale={width}:-2", "-q:v", "3", str(partial)],
            capture_output=True,
        )
        if result.returncode != 0 or not partial.exists():
            raise HTTPException(500, "Could not read that frame")
        partial.replace(path)
    return path


@router.get("/{source_id}/frame")
def get_frame(source_id: int, t: float, width: int = 640, db: Session = Depends(get_db)):
    """The frame of a source at t seconds (for picking the frame a still shows)."""
    source = db.get(Source, source_id)
    if source is None or not Path(source.file_path).exists():
        raise HTTPException(404, "Source not found")
    return FileResponse(frame_jpeg(source.file_path, t, width), media_type="image/jpeg")


@router.get("/scenes/{scene_id}/keyframes/{index}")
def get_keyframe(scene_id: int, index: int, db: Session = Depends(get_db)):
    scene = db.get(Scene, scene_id)
    if scene is None:
        raise HTTPException(404, "Scene not found")
    if index < 0 or index >= len(scene.keyframe_paths):
        raise HTTPException(404, "Keyframe index out of range")
    path = data_path(scene.keyframe_paths[index])
    if not path.exists():
        raise HTTPException(404, "Keyframe file missing on disk")
    return FileResponse(path, media_type="image/jpeg")
