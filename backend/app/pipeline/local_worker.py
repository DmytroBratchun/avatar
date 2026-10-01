"""Runs only the free local stages (transcribe/detect_shots/group_scenes/keyframes)
on every 'paused' source, never touching the captioning API. Leaves each source at
'ready_for_captioning' when done - use Reindex (or the main worker) to take it further.

Also drains the text/visual-only reindex queues (sources with text_reindex_requested
or visual_reindex_requested=True, queued from the UI's "Reindex T"/"Reindex V"
buttons) - BGE/CLIP embedding off existing context_description/keyframes, no
captioning and no LLM call required, so both belong on this free worker too.

Runs persistently - keeps polling for new work instead of exiting when its queues
are empty. Since none of this costs money or calls an LLM, `run_loop` is started
as a background thread from the FastAPI app itself (see app/main.py) - there is
nothing separate to remember to start. `python -m app.pipeline.local_worker` still
works standalone (e.g. to run it on another machine), stop with Ctrl+C.
"""

import datetime as dt
import json
import logging
import os
import threading
import time

from app.config import DATA_DIR
from app.db.session import SessionLocal, init_db
from app.pipeline.runner import (
    paused_sources,
    process_source_local_only,
    process_source_text_only,
    process_source_transcript_chunk_reindex,
    process_source_visual_only,
    requeue_interrupted_local_sources,
    text_reindex_queue,
    transcript_chunk_reindex_queue,
    visual_reindex_queue,
)

LOG_PATH = DATA_DIR / "local_worker.log"
HEARTBEAT_PATH = DATA_DIR / "local_worker_heartbeat.json"
POLL_INTERVAL_SECONDS = 5
HEARTBEAT_REFRESH_SECONDS = 10
PAID_HEARTBEAT_PATH = DATA_DIR / "worker_heartbeat.json"
PAID_HEARTBEAT_STALE_SECONDS = 30

# Own handlers (not logging.basicConfig) so this keeps logging to local_worker.log
# whether it's run standalone or as a background thread inside the FastAPI process,
# without reconfiguring/duplicating onto the app's root logger.
logger = logging.getLogger("local_worker")
logger.setLevel(logging.INFO)
logger.propagate = False
if not logger.handlers:
    _formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    _file_handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
    _file_handler.setFormatter(_formatter)
    _stream_handler = logging.StreamHandler()
    _stream_handler.setFormatter(_formatter)
    logger.addHandler(_file_handler)
    logger.addHandler(_stream_handler)


# A single source can take minutes (transcription, or the free reindex loops over
# hundreds of scenes) - writing the heartbeat only once per source made "last_seen"
# go stale (UI shows "stopped") while very much still running. A background thread
# re-flushes the same state on a fixed interval regardless of what the main loop is
# doing, so staleness only ever means "actually not running".
_heartbeat_lock = threading.Lock()
_heartbeat_state = {"status": "idle", "current_source_id": None, "current_episode": None}


def _write_heartbeat(status: str, source=None) -> None:
    with _heartbeat_lock:
        _heartbeat_state["status"] = status
        _heartbeat_state["current_source_id"] = source.id if source else None
        _heartbeat_state["current_episode"] = f"Book {source.book} Ep {source.episode_number}" if source else None
    _flush_heartbeat()


def _flush_heartbeat() -> None:
    with _heartbeat_lock:
        state = dict(_heartbeat_state)
    payload = {"pid": os.getpid(), "last_seen": dt.datetime.now(dt.timezone.utc).isoformat(), **state}
    HEARTBEAT_PATH.write_text(json.dumps(payload), encoding="utf-8")


def _heartbeat_refresher(stop_event: threading.Event) -> None:
    while not stop_event.wait(HEARTBEAT_REFRESH_SECONDS):
        _flush_heartbeat()


def _drain_once(db) -> bool:
    """Runs one poll iteration. Returns True if any work was found/processed."""
    sources = paused_sources(db)
    visual_sources = visual_reindex_queue(db)
    text_sources = text_reindex_queue(db)
    chunk_sources = transcript_chunk_reindex_queue(db)

    if not sources and not visual_sources and not text_sources and not chunk_sources:
        return False

    for source in sources:
        logger.info("Starting local prep for source %s: Book %s Ep %s", source.id, source.book, source.episode_number)
        _write_heartbeat("processing", source)
        process_source_local_only(db, source)
        logger.info("Finished source %s: status=%s", source.id, source.status)
    for source in text_sources:
        logger.info("Starting text-only reindex for source %s: Book %s Ep %s", source.id, source.book, source.episode_number)
        _write_heartbeat("processing", source)
        process_source_text_only(db, source)
        logger.info("Finished text reindex for source %s", source.id)
    for source in visual_sources:
        logger.info("Starting visual-only reindex for source %s: Book %s Ep %s", source.id, source.book, source.episode_number)
        _write_heartbeat("processing", source)
        process_source_visual_only(db, source)
        logger.info("Finished visual reindex for source %s", source.id)
    for source in chunk_sources:
        logger.info("Starting transcript chunk reindex for source %s: Book %s Ep %s", source.id, source.book, source.episode_number)
        _write_heartbeat("processing", source)
        process_source_transcript_chunk_reindex(db, source)
        logger.info("Finished transcript chunk reindex for source %s", source.id)
    return True


def _paid_worker_alive() -> bool:
    try:
        payload = json.loads(PAID_HEARTBEAT_PATH.read_text(encoding="utf-8"))
        last_seen = dt.datetime.fromisoformat(payload["last_seen"])
    except (OSError, ValueError, KeyError):
        return False
    return (dt.datetime.now(dt.timezone.utc) - last_seen).total_seconds() < PAID_HEARTBEAT_STALE_SECONDS


def _requeue_interrupted() -> None:
    # The paid worker runs these same local stages on 'pending' sources, so a
    # mid-stage status is only proof of a dead worker when it isn't running.
    if _paid_worker_alive():
        return
    db = SessionLocal()
    try:
        requeued = requeue_interrupted_local_sources(db)
        if requeued:
            logger.info("Requeued sources interrupted mid-stage by a restart: %s", requeued)
    except Exception:
        logger.exception("Failed to requeue interrupted sources")
    finally:
        db.close()


def run_loop(stop_event: threading.Event) -> None:
    logger.info("Local-only worker running (thread/pid %s) - no API calls will be made", os.getpid())
    _requeue_interrupted()
    refresher = threading.Thread(target=_heartbeat_refresher, args=(stop_event,), daemon=True)
    refresher.start()
    was_idle = False
    while not stop_event.is_set():
        db = SessionLocal()
        try:
            had_work = _drain_once(db)
            if not had_work:
                _write_heartbeat("idle")
                if not was_idle:
                    logger.info("Queue empty - idling, checking again every %ss", POLL_INTERVAL_SECONDS)
                was_idle = True
            else:
                was_idle = False
        except Exception:
            logger.exception("Local worker loop error")
        finally:
            db.close()
        stop_event.wait(POLL_INTERVAL_SECONDS)


def main() -> None:
    init_db()
    run_loop(threading.Event())


if __name__ == "__main__":
    main()
