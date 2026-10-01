import datetime as dt
import json
import logging
import os
import threading
import time

from app.config import DATA_DIR
from app.db.session import SessionLocal, init_db
from app.pipeline.runner import episode_summary_queue, pending_sources, process_source, process_source_episode_summary
from app.pipeline.stages.caption import CaptioningUnavailable

LOG_PATH = DATA_DIR / "worker.log"
HEARTBEAT_PATH = DATA_DIR / "worker_heartbeat.json"
POLL_INTERVAL_SECONDS = 5
HEARTBEAT_REFRESH_SECONDS = 10
RETRY_AFTER_SECONDS = 15 * 60  # how long to wait when Claude stops answering (usually a usage limit)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(LOG_PATH, encoding="utf-8"), logging.StreamHandler()],
)
logger = logging.getLogger(__name__)

# A single caption_scene() call can take 10-20s+, and a source can have hundreds
# of scenes - _write_heartbeat used to only fire once per source, so the "last_seen"
# timestamp went stale (and the UI showed "stopped") for the whole time one source
# was mid-captioning even though the worker was actively working. A background
# thread now re-flushes the same state on a fixed interval regardless of what the
# main loop is doing, so staleness only ever means "actually not running".
_heartbeat_lock = threading.Lock()
_heartbeat_state = {"status": "idle", "current_source_id": None, "current_episode": None, "detail": ""}


def _write_heartbeat(status: str, source=None, extra: str = "") -> None:
    with _heartbeat_lock:
        _heartbeat_state["status"] = status
        _heartbeat_state["current_source_id"] = source.id if source else None
        _heartbeat_state["current_episode"] = f"Book {source.book} Ep {source.episode_number}" if source else None
        _heartbeat_state["detail"] = extra
    _flush_heartbeat()


def _flush_heartbeat() -> None:
    with _heartbeat_lock:
        state = dict(_heartbeat_state)
    payload = {"pid": os.getpid(), "last_seen": dt.datetime.now(dt.timezone.utc).isoformat(), **state}
    HEARTBEAT_PATH.write_text(json.dumps(payload), encoding="utf-8")


def _heartbeat_refresher(stop_event: threading.Event) -> None:
    while not stop_event.wait(HEARTBEAT_REFRESH_SECONDS):
        _flush_heartbeat()


def main() -> None:
    init_db()
    logger.info("Worker started (pid %s), polling every %ss", os.getpid(), POLL_INTERVAL_SECONDS)
    _write_heartbeat("idle")

    stop_event = threading.Event()
    refresher = threading.Thread(target=_heartbeat_refresher, args=(stop_event,), daemon=True)
    refresher.start()

    while True:
        db = SessionLocal()
        try:
            sources = pending_sources(db)
            summary_sources = episode_summary_queue(db)
            if not sources and not summary_sources:
                _write_heartbeat("idle")
            for source in sources:
                logger.info(
                    "Starting source %s: Book %s Ep %s (%s)",
                    source.id, source.book, source.episode_number, source.file_path,
                )
                _write_heartbeat("processing", source, "starting")
                try:
                    process_source(db, source)
                except CaptioningUnavailable as exc:
                    retry_at = dt.datetime.now() + dt.timedelta(seconds=RETRY_AFTER_SECONDS)
                    logger.warning(
                        "Captioning is unavailable (%s) - pausing, will retry at %s",
                        str(exc)[:300] or "no error text", retry_at.strftime("%H:%M"),
                    )
                    _write_heartbeat("waiting", source, f"Claude is not answering - retrying at {retry_at.strftime('%H:%M')}")
                    time.sleep(RETRY_AFTER_SECONDS)
                    break  # re-read the queue; this source is still in it
                logger.info(
                    "Finished source %s: status=%s error=%s",
                    source.id, source.status, source.error_message,
                )
            for source in summary_sources:
                logger.info("Starting episode summary for source %s: Book %s Ep %s", source.id, source.book, source.episode_number)
                _write_heartbeat("processing", source, "episode summary")
                process_source_episode_summary(db, source)
                logger.info("Finished episode summary for source %s", source.id)
        except Exception:
            logger.exception("Worker loop error")
        finally:
            db.close()
        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
