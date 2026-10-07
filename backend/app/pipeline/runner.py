import datetime as dt
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Scene, Source
from app.pipeline.stages.caption import CaptioningUnavailable, run_captioning
from app.pipeline.stages.comic_pages import run_comic_pages
from app.services.runtime_settings import caption_only_undescribed
from app.pipeline.stages.detect_shots import run_shot_detection
from app.pipeline.stages.embed import run_embedding, run_text_embedding_only, run_visual_embedding_only
from app.pipeline.stages.episode_summary import run_episode_summary
from app.pipeline.stages.extract_keyframes import run_keyframe_extraction
from app.pipeline.stages.group_scenes import run_scene_grouping
from app.pipeline.stages.transcribe import run_transcription
from app.pipeline.stages.transcript_chunks import run_transcript_chunk_embedding, run_transcript_chunking

logger = logging.getLogger(__name__)

# free, local-only stages - no Anthropic API calls, no cost
LOCAL_STAGES = [
    run_transcription,
    run_shot_detection,
    run_scene_grouping,
    run_keyframe_extraction,
    run_transcript_chunking,
]

STAGES = LOCAL_STAGES + [run_captioning, run_embedding]

# a comic has no sound and no shots: its pages are rendered, then described and embedded
COMIC_LOCAL_STAGES = [run_comic_pages]


def local_stages_for(source: Source) -> list:
    return COMIC_LOCAL_STAGES if source.kind == "comic" else LOCAL_STAGES


def stages_for(source: Source) -> list:
    return local_stages_for(source) + [run_captioning, run_embedding]

# "unqueued" = taken off a queue from the UI before local prep finished - parked,
# neither worker touches it until it's queued again
TERMINAL_STATUSES = ["indexed", "failed", "paused", "ready_for_captioning", "unqueued"]

# statuses the local stages set while they run - a source only sits in one of these
# when a worker is actively on it, or when that worker was killed mid-stage
LOCAL_IN_PROGRESS_STATUSES = ["transcribing", "detecting_shots", "grouping_scenes", "extracting_keyframes"]


def requeue_interrupted_local_sources(db: Session) -> list[int]:
    """Puts sources left mid-local-stage by a killed worker back to 'paused', so the
    local worker picks them up again instead of leaving them stuck forever with no
    error. Stages are idempotent, so finished ones are skipped on the re-run. Only
    call this when no other worker can be mid-stage on these sources."""
    sources = list(
        db.execute(select(Source).where(Source.status.in_(LOCAL_IN_PROGRESS_STATUSES))).scalars()
    )
    for source in sources:
        source.status = "paused"
        source.current_stage_detail = None
    db.commit()
    return [source.id for source in sources]


def _is_fully_indexed(db: Session, source: Source) -> bool:
    stmt = select(Scene.id).where(Scene.source_id == source.id, Scene.embedded_at.is_(None))
    if caption_only_undescribed():
        # scenes described by the fan wiki are not captioned in this mode, so they
        # don't count as unfinished
        stmt = stmt.where(Scene.wiki_description.is_(None))
    return db.execute(stmt).first() is None


def dequeue_source(db: Session, source: Source) -> None:
    """Takes a source off the captioning/local-prep queue by parking it in the
    terminal status that matches how far it actually got, so nothing already done
    is lost and it can be queued again later. A worker mid-run on it notices at
    its next check (see was_dequeued) and stops."""
    if source.status in ("indexed", "failed", "ready_for_captioning", "unqueued"):
        return
    has_scenes = db.execute(select(Scene.id).where(Scene.source_id == source.id)).first() is not None
    missing_keyframes = db.execute(
        select(Scene.id).where(Scene.source_id == source.id, Scene.keyframe_paths == [])
    ).first()
    if not has_scenes or missing_keyframes is not None:
        source.status = "unqueued"
    elif _is_fully_indexed(db, source):
        source.status = "indexed"
    else:
        source.status = "ready_for_captioning"
    source.current_stage_detail = None


def was_dequeued(db: Session, source: Source, parked_statuses: list[str]) -> bool:
    """True if the UI parked this source since the worker picked it up - stages only
    ever set in-progress statuses, so a parked status mid-run can only come from
    dequeue_source in another session."""
    db.refresh(source, attribute_names=["status"])
    return source.status in parked_statuses


def process_source(db: Session, source: Source) -> None:
    try:
        for stage in stages_for(source):
            if was_dequeued(db, source, TERMINAL_STATUSES):
                logger.info("  [source %s] removed from queue - stopping", source.id)
                return
            logger.info("  [source %s] stage: %s", source.id, stage.__name__)
            stage(db, source)
            db.commit()

        if was_dequeued(db, source, TERMINAL_STATUSES):
            logger.info("  [source %s] removed from queue - stopping", source.id)
            return

        if _is_fully_indexed(db, source):
            source.status = "indexed"
            source.indexed_at = dt.datetime.now(dt.timezone.utc)
            source.current_stage_detail = None
            source.error_message = None
        else:
            source.status = "failed"
            source.error_message = "Some scenes failed captioning/embedding; see caption_error per scene."
        db.commit()
    except CaptioningUnavailable:
        # not this source's fault - leave it queued and let the worker wait and retry
        db.rollback()
        source.status = "pending"
        source.current_stage_detail = None
        db.commit()
        raise
    except Exception as exc:
        logger.exception("Pipeline failed for source %s", source.id)
        db.rollback()
        source.status = "failed"
        source.error_message = str(exc)
        db.commit()


def process_source_local_only(db: Session, source: Source) -> None:
    """Runs transcription/shot-detection/scene-grouping/keyframes only - never calls
    the captioning API. Leaves the source at 'ready_for_captioning' rather than
    'indexed', since scenes still have no description/tags/embeddings."""
    try:
        # 'paused' is this queue's own waiting status, so only the other parked
        # statuses mean it was removed
        parked = [s for s in TERMINAL_STATUSES if s != "paused"]
        for stage in local_stages_for(source):
            if was_dequeued(db, source, parked):
                logger.info("  [source %s] removed from queue - stopping", source.id)
                return
            logger.info("  [source %s] local stage: %s", source.id, stage.__name__)
            stage(db, source)
            db.commit()

        if was_dequeued(db, source, parked):
            logger.info("  [source %s] removed from queue - stopping", source.id)
            return
        source.status = "ready_for_captioning"
        source.current_stage_detail = None
        source.error_message = None
        db.commit()
    except Exception as exc:
        logger.exception("Local-only pipeline failed for source %s", source.id)
        db.rollback()
        source.status = "failed"
        source.error_message = str(exc)
        db.commit()


def process_source_visual_only(db: Session, source: Source) -> None:
    """Runs only CLIP visual embedding off existing keyframes - no captioning, no LLM
    call. Used for the 'index visual only' queue triggered from the UI."""
    try:
        run_visual_embedding_only(db, source)
        source.visual_reindex_requested = False
        source.current_stage_detail = None
        db.commit()
    except Exception as exc:
        logger.exception("Visual-only reindex failed for source %s", source.id)
        db.rollback()
        source.visual_reindex_requested = False
        source.current_stage_detail = None
        source.error_message = f"visual reindex error: {exc}"
        db.commit()


def process_source_text_only(db: Session, source: Source) -> None:
    """Runs only BGE text embedding off existing context_description - no captioning,
    no LLM call. Used for the 'index text only' queue triggered from the UI."""
    try:
        run_text_embedding_only(db, source)
        source.text_reindex_requested = False
        source.current_stage_detail = None
        db.commit()
    except Exception as exc:
        logger.exception("Text-only reindex failed for source %s", source.id)
        db.rollback()
        source.text_reindex_requested = False
        source.current_stage_detail = None
        source.error_message = f"text reindex error: {exc}"
        db.commit()


def process_source_episode_summary(db: Session, source: Source) -> None:
    """One text-only LLM call (no images) summarizing the episode - much cheaper
    than scene captioning, and only runs when explicitly queued."""
    try:
        run_episode_summary(db, source)
    except Exception as exc:
        logger.exception("Episode summary failed for source %s", source.id)
        db.rollback()
        source.episode_summary_requested = False
        source.error_message = f"episode summary error: {exc}"
        db.commit()


def process_source_transcript_chunk_reindex(db: Session, source: Source) -> None:
    """(Re-)embeds transcript chunks with the episode summary as shared context -
    free/local, no LLM call, but needs episode_summary to already exist."""
    try:
        run_transcript_chunk_embedding(db, source)
        source.transcript_chunk_reindex_requested = False
        source.current_stage_detail = None
        db.commit()
    except Exception as exc:
        logger.exception("Transcript chunk reindex failed for source %s", source.id)
        db.rollback()
        source.transcript_chunk_reindex_requested = False
        source.current_stage_detail = None
        source.error_message = f"transcript chunk reindex error: {exc}"
        db.commit()


def pending_sources(db: Session) -> list[Source]:
    return list(
        db.execute(select(Source).where(Source.status.notin_(TERMINAL_STATUSES))).scalars()
    )


def paused_sources(db: Session) -> list[Source]:
    return list(db.execute(select(Source).where(Source.status == "paused")).scalars())


def visual_reindex_queue(db: Session) -> list[Source]:
    return list(db.execute(select(Source).where(Source.visual_reindex_requested.is_(True))).scalars())


def text_reindex_queue(db: Session) -> list[Source]:
    return list(db.execute(select(Source).where(Source.text_reindex_requested.is_(True))).scalars())


def episode_summary_queue(db: Session) -> list[Source]:
    return list(db.execute(select(Source).where(Source.episode_summary_requested.is_(True))).scalars())


def transcript_chunk_reindex_queue(db: Session) -> list[Source]:
    return list(db.execute(select(Source).where(Source.transcript_chunk_reindex_requested.is_(True))).scalars())
