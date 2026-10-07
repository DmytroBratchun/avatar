from pathlib import Path
import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, text, or_
from sqlalchemy.orm import Session

from app.db.models import Scene, Source, TranscriptChunk, TranscriptLine
from app.config import settings as app_settings
from app.db.models import SceneShot, Shot
from app.db.session import get_db
from app.pipeline.stages.extract_keyframes import _OFFSETS_BY_COUNT
from app.schemas.scenes import FacetsOut, SceneDetailOut, SceneOut, SceneUpdateIn
from app.services.embeddings import embed_text, text_embedding_model_id
from app.vectorstore.sqlite_vec_client import upsert_text_vector
from app.services.runtime_settings import caption_only_undescribed

router = APIRouter(prefix="/api/scenes", tags=["scenes"])


def apply_tag_filters(
    stmt,
    book: int | None,
    episode_number: int | None,
    characters: list[str] | None,
    locations: list[str] | None,
    moods: list[str] | None,
    shows: list[str] | None = None,
):
    if book is not None:
        stmt = stmt.where(Scene.book == book)
    if episode_number is not None:
        stmt = stmt.where(Scene.episode_number == episode_number)
    if shows:
        stmt = stmt.where(Scene.show_title.in_(shows))
    for c in characters or []:
        stmt = stmt.where(Scene.character_tags.contains(c))
    for l in locations or []:
        stmt = stmt.where(Scene.location_tags.contains(l))
    for m in moods or []:
        stmt = stmt.where(Scene.mood_action_tags.contains(m))
    return stmt


@router.get("", response_model=list[SceneOut])
def list_scenes(
    book: int | None = None,
    episode_number: int | None = None,
    source_id: int | None = None,
    characters: list[str] | None = Query(default=None),
    locations: list[str] | None = Query(default=None),
    moods: list[str] | None = Query(default=None),
    shows: list[str] | None = Query(default=None),
    caption_status: str | None = None,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
) -> list[Scene]:
    stmt = apply_tag_filters(select(Scene), book, episode_number, characters, locations, moods, shows)
    if source_id is not None:
        stmt = stmt.where(Scene.source_id == source_id)
    if caption_status is not None:
        stmt = stmt.where(Scene.caption_status == caption_status)
    stmt = stmt.order_by(Scene.source_id, Scene.start_time).offset(offset).limit(limit)
    return list(db.execute(stmt).scalars())


@router.get("/facets", response_model=FacetsOut)
def get_facets(db: Session = Depends(get_db)) -> FacetsOut:
    def distinct_values(column: str) -> list[str]:
        rows = db.execute(
            text(f"SELECT DISTINCT je.value FROM scenes, json_each(scenes.{column}) je ORDER BY je.value")
        ).all()
        return [row[0] for row in rows]

    shows = db.execute(
        text("SELECT DISTINCT show_title FROM scenes WHERE show_title IS NOT NULL ORDER BY show_title")
    ).all()

    return FacetsOut(
        shows=[row[0] for row in shows],
        characters=distinct_values("character_tags"),
        locations=distinct_values("location_tags"),
        moods=distinct_values("mood_action_tags"),
    )


@router.get("/index-stats")
def get_index_stats(db: Session = Depends(get_db)) -> dict:
    total = db.execute(select(text("COUNT(*)")).select_from(Scene)).scalar_one()
    captioned = db.execute(
        select(text("COUNT(*)")).select_from(Scene).where(Scene.caption_status == "done")
    ).scalar_one()
    caption_error = db.execute(
        select(text("COUNT(*)")).select_from(Scene).where(Scene.caption_status == "error")
    ).scalar_one()
    text_indexed = db.execute(
        select(text("COUNT(*)")).select_from(Scene).where(Scene.text_embedding_model.isnot(None))
    ).scalar_one()
    visual_indexed = db.execute(
        select(text("COUNT(*)")).select_from(Scene).where(Scene.visual_embedding_model.isnot(None))
    ).scalar_one()
    wiki_indexed = db.execute(
        select(text("COUNT(*)")).select_from(Scene).where(Scene.wiki_description.isnot(None))
    ).scalar_one()
    # found by a text query through either index: a paid caption or the wiki's description
    described = db.execute(
        select(text("COUNT(*)")).select_from(Scene).where(
            or_(Scene.text_embedding_model.isnot(None), Scene.wiki_description.isnot(None))
        )
    ).scalar_one()
    in_both = db.execute(
        select(text("COUNT(*)")).select_from(Scene).where(
            Scene.text_embedding_model.isnot(None), Scene.wiki_description.isnot(None)
        )
    ).scalar_one()
    transcript_lines = db.execute(select(text("COUNT(*)")).select_from(TranscriptLine)).scalar_one()
    visual_reindex_queue = db.execute(
        select(text("COUNT(*)")).select_from(Source).where(Source.visual_reindex_requested.is_(True))
    ).scalar_one()
    text_reindex_queue = db.execute(
        select(text("COUNT(*)")).select_from(Source).where(Source.text_reindex_requested.is_(True))
    ).scalar_one()
    total_chunks = db.execute(select(text("COUNT(*)")).select_from(TranscriptChunk)).scalar_one()
    chunks_indexed = db.execute(
        select(text("COUNT(*)")).select_from(TranscriptChunk).where(TranscriptChunk.text_embedding_model.isnot(None))
    ).scalar_one()
    episodes_summarized = db.execute(
        select(text("COUNT(*)")).select_from(Source).where(Source.episode_summary.isnot(None))
    ).scalar_one()
    episode_summary_queue = db.execute(
        select(text("COUNT(*)")).select_from(Source).where(Source.episode_summary_requested.is_(True))
    ).scalar_one()
    transcript_chunk_reindex_queue = db.execute(
        select(text("COUNT(*)")).select_from(Source).where(Source.transcript_chunk_reindex_requested.is_(True))
    ).scalar_one()
    total_sources = db.execute(select(text("COUNT(*)")).select_from(Source)).scalar_one()

    return {
        "total_scenes": total,
        "captioned": captioned,
        "caption_error": caption_error,
        "text_indexed": text_indexed,
        "visual_indexed": visual_indexed,
        "wiki_indexed": wiki_indexed,
        "described": described,
        "in_both_text_indexes": in_both,
        "caption_only_undescribed": caption_only_undescribed(),
        "transcript_lines": transcript_lines,
        "visual_reindex_queue": visual_reindex_queue,
        "text_reindex_queue": text_reindex_queue,
        "total_chunks": total_chunks,
        "chunks_indexed": chunks_indexed,
        "total_sources": total_sources,
        "episodes_summarized": episodes_summarized,
        "episode_summary_queue": episode_summary_queue,
        "transcript_chunk_reindex_queue": transcript_chunk_reindex_queue,
    }


@router.get("/{scene_id}", response_model=SceneDetailOut)
def get_scene(scene_id: int, db: Session = Depends(get_db)) -> SceneDetailOut:
    scene = db.get(Scene, scene_id)
    if scene is None:
        raise HTTPException(404, "Scene not found")
    lines = list(
        db.execute(
            select(TranscriptLine).where(TranscriptLine.id.in_(scene.linked_transcript_ids or []))
        ).scalars()
    )
    return SceneDetailOut.model_validate(
        {
            **SceneOut.model_validate(scene, from_attributes=True).model_dump(),
            "transcript_lines": lines,
            "source_file_path": scene.source.file_path,
            "source_file_exists": Path(scene.source.file_path).exists(),
            "episode_title": scene.source.episode_title,
            "page_count": len(scene.source.scenes) if scene.source.kind == "comic" else None,
            "technical": _technical(db, scene),
        }
    )


def _technical(db: Session, scene: Scene) -> dict:
    shots = list(
        db.execute(
            select(Shot).join(SceneShot, SceneShot.shot_id == Shot.id)
            .where(SceneShot.scene_id == scene.id).order_by(SceneShot.shot_order)
        ).scalars()
    )
    duration = scene.end_time - scene.start_time
    offsets = _OFFSETS_BY_COUNT.get(len(scene.keyframe_paths or []), [])
    neighbours = db.execute(
        select(Scene.id, Scene.start_time).where(Scene.source_id == scene.source_id).order_by(Scene.start_time)
    ).all()
    ids = [row[0] for row in neighbours]
    position = ids.index(scene.id)
    next_id = ids[position + 1] if position + 1 < len(ids) else None

    # Re-derive why the grouping rules ended the scene here (see stages/group_scenes.py).
    gap = None
    if next_id is not None:
        before = db.execute(
            select(TranscriptLine.end_time).where(TranscriptLine.source_id == scene.source_id, TranscriptLine.end_time <= scene.end_time)
            .order_by(TranscriptLine.end_time.desc()).limit(1)
        ).scalar()
        after = db.execute(
            select(TranscriptLine.start_time).where(TranscriptLine.source_id == scene.source_id, TranscriptLine.start_time >= scene.end_time)
            .order_by(TranscriptLine.start_time).limit(1)
        ).scalar()
        if before is not None and after is not None:
            gap = round(after - before, 2)
    if next_id is None:
        reason = "end of the episode"
    elif gap is not None and gap > app_settings.scene_gap_seconds:
        reason = f"pause in dialogue longer than {app_settings.scene_gap_seconds:g}s"
    elif duration >= app_settings.scene_max_duration_seconds * 0.8:
        reason = f"length limit ({app_settings.scene_max_duration_seconds:g}s) - the next shot would have made it longer"
    else:
        reason = "no dialogue on one side of the cut"

    return {
        "shots": [{"shot_index": s.shot_index, "start_time": s.start_time, "end_time": s.end_time} for s in shots],
        "keyframe_times": [round(scene.start_time + duration * o, 2) for o in offsets],
        "previous_scene_id": ids[position - 1] if position > 0 else None,
        "next_scene_id": next_id,
        "end_reason": reason,
        "dialogue_gap_after": gap,
        "caption_attempts": scene.caption_attempts or 0,
        "caption_error": scene.caption_error,
        "created_at": scene.created_at.isoformat() if scene.created_at else None,
        "updated_at": scene.updated_at.isoformat() if scene.updated_at else None,
    }


@router.patch("/{scene_id}", response_model=SceneOut)
def update_scene(scene_id: int, payload: SceneUpdateIn, db: Session = Depends(get_db)) -> Scene:
    scene = db.get(Scene, scene_id)
    if scene is None:
        raise HTTPException(404, "Scene not found")

    data = payload.model_dump(exclude_unset=True)
    for field, value in data.items():
        setattr(scene, field, value)

    scene.edited_by_user = True
    scene.edited_at = dt.datetime.now(dt.timezone.utc)
    db.flush()

    if "context_description" in data and scene.context_description:
        vector = embed_text(scene.context_description)
        upsert_text_vector(db, scene.id, vector)
        scene.text_embedding_model = text_embedding_model_id()

    db.refresh(scene)
    return scene
