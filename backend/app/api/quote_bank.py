from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import BankQuote, Source
from app.db.session import get_db

router = APIRouter(prefix="/api/quote-bank", tags=["quote-bank"])


@router.get("")
def list_bank_quotes(
    q: str = "",
    speaker: str = "",
    shows: list[str] | None = Query(default=None),
    limit: int = 60,
    db: Session = Depends(get_db),
) -> dict:
    """Notable quotes located in the library's episodes, filtered by wording and
    speaker. Every word of `q` must be in the quote."""
    stmt = select(BankQuote, Source).join(Source, Source.id == BankQuote.source_id)
    if shows:
        stmt = stmt.where(Source.show_title.in_(shows))
    speakers = sorted(
        {name for row in db.execute(stmt.with_only_columns(BankQuote.speakers)).scalars() for name in row.split(", ") if name}
    )
    for word in q.lower().split():
        stmt = stmt.where(BankQuote.text.ilike(f"%{word}%"))
    if speaker:
        stmt = stmt.where(BankQuote.speakers.ilike(f"%{speaker}%"))
    rows = db.execute(stmt.order_by(Source.show_title, Source.book, Source.episode_number, BankQuote.start_time)).all()
    return {
        "total": len(rows),
        "speakers": speakers,
        "quotes": [
            {
                "id": quote.id,
                "source_id": source.id,
                "show_title": source.show_title,
                "book": source.book,
                "episode_number": source.episode_number,
                "start_time": quote.start_time,
                "end_time": quote.end_time,
                "text": quote.text,
                "speakers": quote.speakers,
                "origin": quote.origin,
            }
            for quote, source in rows[: max(1, min(limit, 300))]
        ],
    }
