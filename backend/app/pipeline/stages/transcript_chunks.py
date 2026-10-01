import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Source, TranscriptChunk, TranscriptLine
from app.services.embeddings import embed_text, text_embedding_model_id
from app.vectorstore.sqlite_vec_client import upsert_chunk_vector

WINDOW_SECONDS = 60.0
STRIDE_SECONDS = 30.0


def run_transcript_chunking(db: Session, source: Source) -> None:
    """Groups TranscriptLine rows into ~60s sliding-window chunks (50% overlap),
    independent of Scene boundaries - free/local, built for coarser RAG-style
    retrieval rather than precise search. Skips if already chunked."""
    existing = db.execute(select(TranscriptChunk).where(TranscriptChunk.source_id == source.id)).first()
    if existing:
        return

    lines = list(
        db.execute(select(TranscriptLine).where(TranscriptLine.source_id == source.id).order_by(TranscriptLine.start_time)).scalars()
    )
    if not lines:
        return

    last_end = max(line.end_time for line in lines)
    window_start = 0.0
    while window_start < last_end:
        window_end = window_start + WINDOW_SECONDS
        window_lines = [line for line in lines if line.start_time < window_end and line.end_time > window_start]
        if window_lines:
            raw_text = " ".join(line.text.strip() for line in window_lines if line.text.strip())
            if raw_text:
                db.add(
                    TranscriptChunk(
                        source_id=source.id,
                        show_title=source.show_title,
                        book=source.book,
                        episode_number=source.episode_number,
                        start_time=window_lines[0].start_time,
                        end_time=window_lines[-1].end_time,
                        raw_text=raw_text,
                    )
                )
        window_start += STRIDE_SECONDS
    db.commit()


def run_transcript_chunk_embedding(db: Session, source: Source) -> None:
    """(Re-)embeds every chunk as episode_summary + raw_text, so each chunk carries
    whole-episode context even though the raw transcript has no speaker
    attribution. Requires an episode_summary - without one, chunks would embed
    with no more context than the transcript search already gives you for free."""
    if not source.episode_summary:
        return

    chunks = list(db.execute(select(TranscriptChunk).where(TranscriptChunk.source_id == source.id)).scalars())
    total = len(chunks)
    for i, chunk in enumerate(chunks):
        db.refresh(source, attribute_names=["transcript_chunk_reindex_requested"])
        if not source.transcript_chunk_reindex_requested:
            source.current_stage_detail = None
            db.commit()
            return
        combined = f"{source.episode_summary}\n\n{chunk.raw_text}"
        upsert_chunk_vector(db, chunk.id, embed_text(combined))
        chunk.text_embedding_model = text_embedding_model_id()
        chunk.embedded_at = dt.datetime.now(dt.timezone.utc)
        source.current_stage_detail = f"transcript chunk embedding {i + 1}/{total}"
        db.commit()
