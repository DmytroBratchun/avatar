from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import case, func, select, update
from sqlalchemy.orm import Session

from app.db.models import Scene, Source, TranscriptChunk
from app.db.session import get_db
from app.pipeline.runner import dequeue_source
from app.schemas.scenes import ScanFolderIn, ScanFolderResult, SourceCreateIn, SourceOut, SourceUpdateIn
from app.services.episode_parser import find_video_files, parse_episode_info

router = APIRouter(prefix="/api/sources", tags=["sources"])


@router.post("", response_model=SourceOut)
def create_source(payload: SourceCreateIn, db: Session = Depends(get_db)) -> Source:
    if not Path(payload.file_path).exists():
        raise HTTPException(400, f"File not found: {payload.file_path}")

    existing = db.execute(select(Source).where(Source.file_path == payload.file_path)).scalar_one_or_none()
    if existing:
        raise HTTPException(409, "Source with this file_path already registered")

    source = Source(
        file_path=payload.file_path,
        title=payload.title,
        show_title=payload.show_title,
        book=payload.book,
        episode_number=payload.episode_number,
        episode_title=payload.episode_title,
        status="pending",
    )
    db.add(source)
    db.flush()
    db.refresh(source)
    return source


@router.post("/scan-folder", response_model=ScanFolderResult)
def scan_folder(payload: ScanFolderIn, db: Session = Depends(get_db)) -> ScanFolderResult:
    root = Path(payload.folder_path)
    if not root.is_dir():
        raise HTTPException(400, f"Folder not found: {payload.folder_path}")

    show_title = payload.show_title or root.name

    existing_paths = {
        row[0] for row in db.execute(select(Source.file_path)).all()
    }

    added: list[SourceOut] = []
    skipped = 0
    for video_path in find_video_files(root):
        path_str = str(video_path)
        if path_str in existing_paths:
            skipped += 1
            continue

        parsed = parse_episode_info(video_path)
        source = Source(
            file_path=path_str,
            show_title=show_title,
            book=parsed.book,
            episode_number=parsed.episode_number,
            episode_title=parsed.episode_title,
            status="pending",
        )
        db.add(source)
        db.flush()
        db.refresh(source)
        added.append(SourceOut.model_validate(source, from_attributes=True))
        existing_paths.add(path_str)

    return ScanFolderResult(added=added, skipped_existing=skipped)


def _with_counts(db: Session, source: Source) -> SourceOut:
    total, text_n, visual_n = db.execute(
        select(
            func.count(Scene.id),
            func.sum(case((Scene.text_embedding_model.isnot(None), 1), else_=0)),
            func.sum(case((Scene.visual_embedding_model.isnot(None), 1), else_=0)),
        ).where(Scene.source_id == source.id)
    ).one()
    chunk_total, chunk_indexed = db.execute(
        select(
            func.count(TranscriptChunk.id),
            func.sum(case((TranscriptChunk.text_embedding_model.isnot(None), 1), else_=0)),
        ).where(TranscriptChunk.source_id == source.id)
    ).one()
    return SourceOut.model_validate(source, from_attributes=True).model_copy(
        update={
            "scene_count": total or 0,
            "text_indexed_count": text_n or 0,
            "visual_indexed_count": visual_n or 0,
            "chunk_count": chunk_total or 0,
            "chunk_indexed_count": chunk_indexed or 0,
        }
    )


@router.get("", response_model=list[SourceOut])
def list_sources(db: Session = Depends(get_db)) -> list[SourceOut]:
    sources = list(
        db.execute(select(Source).order_by(Source.show_title, Source.book, Source.episode_number)).scalars()
    )

    counts_by_source = {
        row[0]: (row[1], row[2] or 0, row[3] or 0)
        for row in db.execute(
            select(
                Scene.source_id,
                func.count(Scene.id),
                func.sum(case((Scene.text_embedding_model.isnot(None), 1), else_=0)),
                func.sum(case((Scene.visual_embedding_model.isnot(None), 1), else_=0)),
            ).group_by(Scene.source_id)
        ).all()
    }
    chunk_counts_by_source = {
        row[0]: (row[1], row[2] or 0)
        for row in db.execute(
            select(
                TranscriptChunk.source_id,
                func.count(TranscriptChunk.id),
                func.sum(case((TranscriptChunk.text_embedding_model.isnot(None), 1), else_=0)),
            ).group_by(TranscriptChunk.source_id)
        ).all()
    }

    result = []
    for source in sources:
        total, text_n, visual_n = counts_by_source.get(source.id, (0, 0, 0))
        chunk_total, chunk_indexed = chunk_counts_by_source.get(source.id, (0, 0))
        result.append(
            SourceOut.model_validate(source, from_attributes=True).model_copy(
                update={
                    "scene_count": total,
                    "text_indexed_count": text_n,
                    "visual_indexed_count": visual_n,
                    "chunk_count": chunk_total,
                    "chunk_indexed_count": chunk_indexed,
                }
            )
        )
    return result


@router.get("/{source_id}", response_model=SourceOut)
def get_source(source_id: int, db: Session = Depends(get_db)) -> SourceOut:
    source = db.get(Source, source_id)
    if source is None:
        raise HTTPException(404, "Source not found")
    return _with_counts(db, source)


@router.patch("/{source_id}", response_model=SourceOut)
def update_source(source_id: int, payload: SourceUpdateIn, db: Session = Depends(get_db)) -> Source:
    source = db.get(Source, source_id)
    if source is None:
        raise HTTPException(404, "Source not found")

    data = payload.model_dump(exclude_unset=True)
    for field, value in data.items():
        setattr(source, field, value)

    # scenes denormalize show_title/book/episode_number for cheap filtering; keep them in sync
    if "show_title" in data or "book" in data or "episode_number" in data:
        db.execute(
            update(Scene)
            .where(Scene.source_id == source.id)
            .values(show_title=source.show_title, book=source.book, episode_number=source.episode_number)
        )

    db.flush()
    db.refresh(source)
    return source


@router.post("/{source_id}/reindex", response_model=SourceOut)
def reindex_source(source_id: int, only_stale: bool = False, db: Session = Depends(get_db)) -> Source:
    source = db.get(Source, source_id)
    if source is None:
        raise HTTPException(404, "Source not found")
    if not only_stale:
        source.status = "pending"
        source.error_message = None
    db.flush()
    db.refresh(source)
    return source


@router.post("/{source_id}/dequeue", response_model=SourceOut)
def dequeue(source_id: int, db: Session = Depends(get_db)) -> Source:
    """Removes this source from the captioning / local-prep queue without losing
    work already done. If a worker is mid-run on it, it stops at its next check -
    after the current scene for captioning, after the current stage otherwise."""
    source = db.get(Source, source_id)
    if source is None:
        raise HTTPException(404, "Source not found")
    dequeue_source(db, source)
    db.flush()
    db.refresh(source)
    return source


@router.post("/{source_id}/reindex-visual", response_model=SourceOut)
def reindex_visual(source_id: int, db: Session = Depends(get_db)) -> Source:
    """Queues visual-only (CLIP) reindexing for this source - off existing keyframes,
    no captioning required. Safe to call repeatedly across sources; each queues
    independently and the local worker drains them in order."""
    source = db.get(Source, source_id)
    if source is None:
        raise HTTPException(404, "Source not found")
    source.visual_reindex_requested = True
    db.flush()
    db.refresh(source)
    return source


@router.post("/{source_id}/reindex-text", response_model=SourceOut)
def reindex_text(source_id: int, db: Session = Depends(get_db)) -> Source:
    """Queues text-only (BGE) reindexing for this source - off existing
    context_description, no captioning required. Safe to call repeatedly across
    sources; each queues independently and the local worker drains them in order."""
    source = db.get(Source, source_id)
    if source is None:
        raise HTTPException(404, "Source not found")
    source.text_reindex_requested = True
    db.flush()
    db.refresh(source)
    return source


@router.post("/{source_id}/cancel-visual-reindex", response_model=SourceOut)
def cancel_visual_reindex(source_id: int, db: Session = Depends(get_db)) -> Source:
    """Clears the visual-reindex flag. If it's still just queued, this removes it
    outright; if the worker is already mid-loop on it, the loop notices within one
    scene and stops there instead of finishing the whole source."""
    source = db.get(Source, source_id)
    if source is None:
        raise HTTPException(404, "Source not found")
    source.visual_reindex_requested = False
    db.flush()
    db.refresh(source)
    return source


@router.post("/{source_id}/cancel-text-reindex", response_model=SourceOut)
def cancel_text_reindex(source_id: int, db: Session = Depends(get_db)) -> Source:
    """Clears the text-reindex flag - see cancel_visual_reindex."""
    source = db.get(Source, source_id)
    if source is None:
        raise HTTPException(404, "Source not found")
    source.text_reindex_requested = False
    db.flush()
    db.refresh(source)
    return source


@router.post("/{source_id}/request-episode-summary", response_model=SourceOut)
def request_episode_summary(source_id: int, db: Session = Depends(get_db)) -> Source:
    """Queues one text-only LLM call (no images) to summarize the episode's
    transcript - used as shared context for transcript-chunk retrieval. Much
    cheaper than scene captioning; drained by the paid captioning worker since it
    still needs an LLM call."""
    source = db.get(Source, source_id)
    if source is None:
        raise HTTPException(404, "Source not found")
    source.episode_summary_requested = True
    db.flush()
    db.refresh(source)
    return source


@router.post("/{source_id}/cancel-episode-summary", response_model=SourceOut)
def cancel_episode_summary(source_id: int, db: Session = Depends(get_db)) -> Source:
    """Clears the episode-summary-request flag."""
    source = db.get(Source, source_id)
    if source is None:
        raise HTTPException(404, "Source not found")
    source.episode_summary_requested = False
    db.flush()
    db.refresh(source)
    return source


@router.post("/{source_id}/reindex-transcript-chunks", response_model=SourceOut)
def reindex_transcript_chunks(source_id: int, db: Session = Depends(get_db)) -> Source:
    """Queues (re-)embedding of transcript chunks with the episode summary as
    context - free/local, no LLM call, but needs episode_summary to exist first."""
    source = db.get(Source, source_id)
    if source is None:
        raise HTTPException(404, "Source not found")
    source.transcript_chunk_reindex_requested = True
    db.flush()
    db.refresh(source)
    return source


@router.post("/{source_id}/cancel-transcript-chunk-reindex", response_model=SourceOut)
def cancel_transcript_chunk_reindex(source_id: int, db: Session = Depends(get_db)) -> Source:
    """Clears the transcript-chunk-reindex flag - see cancel_visual_reindex."""
    source = db.get(Source, source_id)
    if source is None:
        raise HTTPException(404, "Source not found")
    source.transcript_chunk_reindex_requested = False
    db.flush()
    db.refresh(source)
    return source


@router.delete("/{source_id}", status_code=204)
def delete_source(source_id: int, db: Session = Depends(get_db)) -> None:
    source = db.get(Source, source_id)
    if source is None:
        raise HTTPException(404, "Source not found")
    db.delete(source)
