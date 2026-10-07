import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Scene, Source
from app.services.embeddings import embed_image, embed_text, text_embedding_model_id, visual_embedding_model_id
from app.vectorstore.sqlite_vec_client import upsert_text_vector, upsert_visual_vector


def run_embedding(db: Session, source: Source) -> None:
    scenes = list(
        db.execute(
            select(Scene).where(
                Scene.source_id == source.id,
                Scene.caption_status == "done",
                Scene.embedded_at.is_(None),
            )
        ).scalars()
    )
    if not scenes:
        return

    source.status = "embedding"
    db.commit()

    total = len(scenes)
    for i, scene in enumerate(scenes):
        if scene.context_description:
            upsert_text_vector(db, scene.id, embed_text(scene.context_description))
            scene.text_embedding_model = text_embedding_model_id()
        # a comic's pages are found by what happens on them, not by how they look
        if scene.keyframe_paths and source.kind != "comic":
            upsert_visual_vector(db, scene.id, embed_image(scene.keyframe_paths[0]))
            scene.visual_embedding_model = visual_embedding_model_id()
        scene.embedded_at = dt.datetime.now(dt.timezone.utc)
        source.current_stage_detail = f"embedding {i + 1}/{total}"
        db.commit()


def run_visual_embedding_only(db: Session, source: Source) -> None:
    """Re-embeds every scene's keyframe with CLIP, independent of caption_status.
    Used for the 'index visual only' queue - needs no LLM call, just existing keyframes."""
    scenes = list(db.execute(select(Scene).where(Scene.source_id == source.id)).scalars())
    scenes = [s for s in scenes if s.keyframe_paths]
    total = len(scenes)
    for i, scene in enumerate(scenes):
        # re-check on every scene - a "Cancel" click clears this flag from another
        # request/session, and re-embedding scenes already done costs real GPU time
        # for nothing, so we stop as soon as the cancellation is visible, not just
        # before the next source starts.
        db.refresh(source, attribute_names=["visual_reindex_requested"])
        if not source.visual_reindex_requested:
            source.current_stage_detail = None
            db.commit()
            return
        upsert_visual_vector(db, scene.id, embed_image(scene.keyframe_paths[0]))
        scene.visual_embedding_model = visual_embedding_model_id()
        source.current_stage_detail = f"visual reindex {i + 1}/{total}"
        db.commit()


def run_text_embedding_only(db: Session, source: Source) -> None:
    """Re-embeds every scene's context_description with BGE, independent of running
    captioning again - just needs an existing context_description. No LLM call."""
    scenes = list(db.execute(select(Scene).where(Scene.source_id == source.id)).scalars())
    scenes = [s for s in scenes if s.context_description]
    total = len(scenes)
    for i, scene in enumerate(scenes):
        db.refresh(source, attribute_names=["text_reindex_requested"])
        if not source.text_reindex_requested:
            source.current_stage_detail = None
            db.commit()
            return
        upsert_text_vector(db, scene.id, embed_text(scene.context_description))
        scene.text_embedding_model = text_embedding_model_id()
        source.current_stage_detail = f"text reindex {i + 1}/{total}"
        db.commit()
