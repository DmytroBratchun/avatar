from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.scenes import apply_tag_filters
from app.db.models import Scene
from app.db.session import get_db
from app.schemas.scenes import SceneOut, SearchResult
from app.services.embeddings import embed_text, embed_text_for_visual_search
from app.services.rank_fusion import reciprocal_rank_fusion
from app.vectorstore.sqlite_vec_client import knn_text, knn_visual, knn_wiki

router = APIRouter(prefix="/api/search", tags=["search"])


def _candidate_ids(
    db: Session,
    book: int | None,
    episode_number: int | None,
    characters: list[str] | None,
    locations: list[str] | None,
    moods: list[str] | None,
    shows: list[str] | None,
) -> list[int] | None:
    filters_present = book is not None or episode_number is not None or characters or locations or moods or shows
    if not filters_present:
        return None

    stmt = apply_tag_filters(select(Scene.id), book, episode_number, characters, locations, moods, shows)
    return list(db.execute(stmt).scalars())


@router.get("", response_model=list[SearchResult])
def search_scenes(
    q: str,
    mode: str = Query("hybrid", pattern="^(hybrid|text|visual)$"),
    book: int | None = None,
    episode_number: int | None = None,
    characters: list[str] | None = Query(default=None),
    locations: list[str] | None = Query(default=None),
    moods: list[str] | None = Query(default=None),
    shows: list[str] | None = Query(default=None),
    limit: int = 20,
    db: Session = Depends(get_db),
) -> list[SearchResult]:
    candidate_ids = _candidate_ids(db, book, episode_number, characters, locations, moods, shows)
    if candidate_ids is not None and not candidate_ids:
        return []

    rankings: list[list[tuple[int, float]]] = []
    if mode in ("hybrid", "text"):
        text_vector = embed_text(q)
        rankings.append(knn_text(db, text_vector, candidate_ids, k=limit * 3))
        # the free fan-wiki descriptions cover episodes that have no caption yet
        rankings.append(knn_wiki(db, text_vector, candidate_ids, k=limit * 3))
    if mode in ("hybrid", "visual"):
        visual_vector = embed_text_for_visual_search(q)
        rankings.append(knn_visual(db, visual_vector, candidate_ids, k=limit * 3))

    fused = reciprocal_rank_fusion(rankings)[:limit]
    if not fused:
        return []

    scenes_by_id = {s.id: s for s in db.execute(select(Scene).where(Scene.id.in_([sid for sid, _ in fused]))).scalars()}
    matched_by = "hybrid" if mode == "hybrid" else mode

    results = []
    for scene_id, score in fused:
        scene = scenes_by_id.get(scene_id)
        if scene is None:
            continue
        results.append(
            SearchResult(scene=SceneOut.model_validate(scene, from_attributes=True), score=score, matched_by=matched_by)
        )
    return results
