from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Source, TranscriptLine
from app.db.session import get_db

router = APIRouter(prefix="/api/transcript", tags=["transcript"])


class TranscriptSearchResult(BaseModel):
    source_id: int
    book: int | None
    episode_number: int | None
    episode_title: str | None
    start_time: float
    end_time: float
    text: str


@router.get("/search", response_model=list[TranscriptSearchResult])
def search_transcript(
    q: str,
    book: int | None = None,
    episode_number: int | None = None,
    limit: int = 50,
    db: Session = Depends(get_db),
) -> list[TranscriptSearchResult]:
    """Plain keyword search over dialogue - works even before captioning/embedding
    has run, since it only needs the transcript table."""
    stmt = (
        select(TranscriptLine, Source)
        .join(Source, Source.id == TranscriptLine.source_id)
        .where(TranscriptLine.text.contains(q))
    )
    if book is not None:
        stmt = stmt.where(Source.book == book)
    if episode_number is not None:
        stmt = stmt.where(Source.episode_number == episode_number)
    stmt = stmt.order_by(Source.book, Source.episode_number, TranscriptLine.start_time).limit(limit)

    results = []
    for line, source in db.execute(stmt).all():
        results.append(
            TranscriptSearchResult(
                source_id=source.id,
                book=source.book,
                episode_number=source.episode_number,
                episode_title=source.episode_title,
                start_time=line.start_time,
                end_time=line.end_time,
                text=line.text,
            )
        )
    return results
