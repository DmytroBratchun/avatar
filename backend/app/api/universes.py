from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Source
from app.db.session import get_db
from app.services.universes import all_universes

router = APIRouter(prefix="/api/universes", tags=["universes"])


@router.get("")
def list_universes(db: Session = Depends(get_db)) -> list[dict]:
    """Every universe, with which of its shows are actually in the library, plus a
    list of library shows that no universe claims yet."""
    in_library = {title for title in db.execute(select(Source.show_title).distinct()).scalars() if title}
    universes = all_universes()
    claimed = {show for u in universes for show in u.shows}
    return [
        {**u.model_dump(), "available_shows": [show for show in u.shows if show in in_library]} for u in universes
    ] + (
        [{"id": "", "name": "Unassigned", "available_shows": sorted(in_library - claimed), "shows": [], "regions": []}]
        if in_library - claimed
        else []
    )
