"""Phase 1 validation script: register one episode and run the full pipeline
synchronously (no worker loop), so you can inspect stage-by-stage output.

Usage:
    .venv/Scripts/python scripts/ingest_episode.py "C:/path/to/episode.mkv" --book 1 --episode 1 --title "The Boy in the Iceberg"
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from app.db.models import Source
from app.db.session import SessionLocal, init_db
from app.pipeline.runner import process_source


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("file_path")
    parser.add_argument("--book", type=int, required=True)
    parser.add_argument("--episode", type=int, required=True)
    parser.add_argument("--title", default=None)
    args = parser.parse_args()

    init_db()
    db = SessionLocal()
    try:
        source = db.execute(select(Source).where(Source.file_path == args.file_path)).scalar_one_or_none()
        if source is None:
            source = Source(
                file_path=args.file_path,
                book=args.book,
                episode_number=args.episode,
                episode_title=args.title,
                status="pending",
            )
            db.add(source)
            db.commit()
            db.refresh(source)

        print(f"Processing source {source.id}: {source.file_path}")
        process_source(db, source)
        db.refresh(source)
        print(f"Final status: {source.status}")
        if source.error_message:
            print(f"Error: {source.error_message}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
