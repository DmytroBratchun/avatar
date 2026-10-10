"""Finishes the index of chosen sources - captions (paid: one Claude call per scene)
and embeddings - without starting the paid worker, which would take everything
waiting in its queue. Local stages already done are skipped.

Usage (from backend/):
    .venv/Scripts/python scripts/index_sources.py 156 157 158
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.models import Source
from app.db.session import SessionLocal, init_db
from app.pipeline.runner import process_source
from app.pipeline.stages.caption import CaptioningUnavailable


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    init_db()
    db = SessionLocal()
    try:
        for source_id in map(int, sys.argv[1:]):
            source = db.get(Source, source_id)
            if source is None:
                print(f"source {source_id}: not found")
                continue
            if source.status in ("paused", "transcribing", "detecting_shots", "grouping_scenes", "extracting_keyframes"):
                print(f"source {source_id}: the local worker still has it ({source.status}) - skipped")
                continue
            print(f"source {source_id}: {source.show_title} {source.book}x{source.episode_number} ({source.status})", flush=True)
            # a parked status would read as "removed from the queue" to the pipeline
            source.status = "pending"
            db.commit()
            try:
                process_source(db, source)
            except CaptioningUnavailable as exc:
                # Claude is not answering (limits, sign-in): park it rather than leave it for the paid worker
                source.status = "ready_for_captioning"
                db.commit()
                print(f"source {source_id}: captioning stopped - {exc}")
                break
            db.refresh(source)
            print(f"source {source_id}: {source.status}, captions cost ${source.caption_cost_usd or 0:.2f}"
                  + (f" - {source.error_message}" if source.error_message else ""), flush=True)
    finally:
        db.close()


if __name__ == "__main__":
    main()
