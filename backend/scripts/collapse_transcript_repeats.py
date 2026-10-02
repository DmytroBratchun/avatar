"""One-off maintenance: remove Whisper's stuck-on-one-line repeats from existing
transcripts (see app/services/transcript_repeats.py), keeping the first line of
each run.

For every episode that changes it also:
- removes the deleted line ids from the scenes they were linked to;
- rebuilds its transcript chunks, since their text was built from those lines, and
  queues the chunks for re-embedding on the free local worker when the episode has
  a summary (chunks are embedded as summary + text and can't be embedded without one).

Scene captions and scene embeddings are not touched - they come from the captions,
not the transcript.

Usage (from backend/, with the backend stopped or idle):
    .venv/Scripts/python scripts/collapse_transcript_repeats.py           # preview only
    .venv/Scripts/python scripts/collapse_transcript_repeats.py --apply    # write changes
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import delete, select, text

from app.db.models import Scene, Source, TranscriptChunk, TranscriptLine
from app.db.session import SessionLocal
from app.pipeline.stages.transcript_chunks import run_transcript_chunking
from app.services.transcript_repeats import lines_to_drop


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Write changes (default: dry-run preview)")
    args = parser.parse_args()

    db = SessionLocal()
    total_dropped = 0
    changed_sources = 0
    queued_for_embedding = 0
    try:
        for source in db.execute(select(Source).order_by(Source.id)).scalars():
            lines = list(
                db.execute(
                    select(TranscriptLine).where(TranscriptLine.source_id == source.id).order_by(TranscriptLine.start_time)
                ).scalars()
            )
            drop = lines_to_drop(lines, lambda line: line.text)
            if not drop:
                continue
            changed_sources += 1
            total_dropped += len(drop)
            label = f"{source.show_title} B{source.book}E{source.episode_number}"
            print(f"{label}: drop {len(drop)} of {len(lines)} lines")
            if not args.apply:
                continue

            dropped_ids = {line.id for line in drop}
            for scene in db.execute(select(Scene).where(Scene.source_id == source.id)).scalars():
                kept = [i for i in (scene.linked_transcript_ids or []) if i not in dropped_ids]
                if len(kept) != len(scene.linked_transcript_ids or []):
                    scene.linked_transcript_ids = kept
            db.execute(delete(TranscriptLine).where(TranscriptLine.id.in_(dropped_ids)))

            chunk_ids = list(db.execute(select(TranscriptChunk.id).where(TranscriptChunk.source_id == source.id)).scalars())
            if chunk_ids:
                placeholders = ",".join(str(int(i)) for i in chunk_ids)
                db.execute(text(f"DELETE FROM transcript_chunk_vec WHERE chunk_id IN ({placeholders})"))
                db.execute(delete(TranscriptChunk).where(TranscriptChunk.source_id == source.id))
            db.flush()
            run_transcript_chunking(db, source)  # commits
            if source.episode_summary:
                source.transcript_chunk_reindex_requested = True
                queued_for_embedding += 1
            db.commit()
    finally:
        db.close()

    verb = "Removed" if args.apply else "Would remove"
    print(f"\n{verb} {total_dropped} lines in {changed_sources} episodes.")
    if args.apply:
        print(f"Rebuilt their transcript chunks; {queued_for_embedding} episodes queued for chunk re-embedding.")


if __name__ == "__main__":
    main()
