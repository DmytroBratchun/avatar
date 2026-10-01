from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.orm import Session

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
    range_header = request.headers.get("range")
    if range_header is None:
        return FileResponse(path, media_type="video/mp4")

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
    return StreamingResponse(iterator(), status_code=206, media_type="video/mp4", headers=headers)


@router.get("/scenes/{scene_id}/keyframes/{index}")
def get_keyframe(scene_id: int, index: int, db: Session = Depends(get_db)):
    scene = db.get(Scene, scene_id)
    if scene is None:
        raise HTTPException(404, "Scene not found")
    if index < 0 or index >= len(scene.keyframe_paths):
        raise HTTPException(404, "Keyframe index out of range")
    path = Path(scene.keyframe_paths[index])
    if not path.exists():
        raise HTTPException(404, "Keyframe file missing on disk")
    return FileResponse(path, media_type="image/jpeg")
