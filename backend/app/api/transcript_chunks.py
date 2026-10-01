from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Scene, Source, TranscriptChunk
from app.db.session import get_db
from app.schemas.scenes import SceneOut, TranscriptChunkContextOut, TranscriptChunkOut, TranscriptChunkSearchResult
from app.services.embeddings import embed_text
from app.vectorstore.sqlite_vec_client import knn_chunks

router = APIRouter(prefix="/api/transcript-chunks", tags=["transcript-chunks"])


@router.get("", response_model=list[TranscriptChunkOut])
def list_transcript_chunks(
    source_id: int,
    db: Session = Depends(get_db),
) -> list[TranscriptChunk]:
    return list(
        db.execute(
            select(TranscriptChunk)
            .where(TranscriptChunk.source_id == source_id)
            .order_by(TranscriptChunk.start_time)
        ).scalars()
    )


@router.get("/search", response_model=list[TranscriptChunkSearchResult])
def search_transcript_chunks(
    q: str,
    book: int | None = None,
    episode_number: int | None = None,
    shows: list[str] | None = Query(default=None),
    limit: int = 10,
    db: Session = Depends(get_db),
) -> list[TranscriptChunkSearchResult]:
    candidate_ids: list[int] | None = None
    filters_present = book is not None or episode_number is not None or shows
    if filters_present:
        stmt = select(TranscriptChunk.id)
        if book is not None:
            stmt = stmt.where(TranscriptChunk.book == book)
        if episode_number is not None:
            stmt = stmt.where(TranscriptChunk.episode_number == episode_number)
        if shows:
            stmt = stmt.where(TranscriptChunk.show_title.in_(shows))
        candidate_ids = list(db.execute(stmt).scalars())
        if not candidate_ids:
            return []

    query_vector = embed_text(q)
    ranked = knn_chunks(db, query_vector, candidate_ids, k=limit)
    if not ranked:
        return []

    chunk_ids = [cid for cid, _ in ranked]
    chunks_by_id = {c.id: c for c in db.execute(select(TranscriptChunk).where(TranscriptChunk.id.in_(chunk_ids))).scalars()}
    source_ids = {c.source_id for c in chunks_by_id.values()}
    summaries_by_source = {
        s.id: s.episode_summary for s in db.execute(select(Source).where(Source.id.in_(source_ids))).scalars()
    }

    results = []
    for chunk_id, distance in ranked:
        chunk = chunks_by_id.get(chunk_id)
        if chunk is None:
            continue
        results.append(
            TranscriptChunkSearchResult(
                chunk=TranscriptChunkOut.model_validate(chunk, from_attributes=True),
                episode_summary=summaries_by_source.get(chunk.source_id),
                score=distance,
            )
        )
    return results


@router.get("/{chunk_id}/context", response_model=TranscriptChunkContextOut)
def get_chunk_context(chunk_id: int, db: Session = Depends(get_db)) -> TranscriptChunkContextOut:
    """The chunk itself, plus every Scene in the same source whose time range
    overlaps the chunk's - lets a chunk hit link straight to the captioned
    scenes (keyframes, tags, video) that cover the same moment."""
    chunk = db.get(TranscriptChunk, chunk_id)
    if chunk is None:
        raise HTTPException(404, "Chunk not found")

    source = db.get(Source, chunk.source_id)
    overlapping = list(
        db.execute(
            select(Scene)
            .where(
                Scene.source_id == chunk.source_id,
                Scene.start_time < chunk.end_time,
                Scene.end_time > chunk.start_time,
            )
            .order_by(Scene.start_time)
        ).scalars()
    )

    return TranscriptChunkContextOut(
        chunk=TranscriptChunkOut.model_validate(chunk, from_attributes=True),
        episode_summary=source.episode_summary if source else None,
        overlapping_scenes=[SceneOut.model_validate(s, from_attributes=True) for s in overlapping],
    )
