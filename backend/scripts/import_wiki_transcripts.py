"""Imports fan-wiki episode transcripts (see app/services/wiki_transcripts.py) for
every show whose universe names a transcripts wiki:

- sets the speaker of each transcript line;
- removes lines Whisper invented over music and silence - runs of three or more
  consecutive lines with no word in common with the wiki page, unless lines of the
  run are spoken elsewhere in the show (the opening narration, which the wiki only
  writes out once);
- stores the wiki's own lines and its descriptions of what happens on screen, with
  the time range each was matched to, in the wiki_entries table.

Scene captions and embeddings are not touched. An episode whose page matches poorly
is skipped. Removed lines are also written to a JSON file next to the database.

Usage (from backend/, with the backend stopped):
    .venv/Scripts/python scripts/import_wiki_transcripts.py           # preview only
    .venv/Scripts/python scripts/import_wiki_transcripts.py --apply    # write changes
"""

import argparse
import collections
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import delete, select

from app.config import BACKUPS_DIR
from app.db.models import Scene, Source, TranscriptLine, WikiEntry
from app.db.session import SessionLocal, init_db
from app.services.universes import all_universes
from app.services.wiki_transcripts import Wiki, tokens

MIN_PAGE_MATCH = 0.75  # share of the wiki page's words that must be found in our transcript
MIN_INVENTED_RUN = 3
KNOWN_LINE_TOKENS = 4  # shorter lines ("No!", "Earth.") occur everywhere and prove nothing


def invented_lines(lines: list[TranscriptLine], found: list[float], said_anywhere: str) -> list[TranscriptLine]:
    """Lines to remove: runs of MIN_INVENTED_RUN+ lines sharing no word with the wiki
    page. A run is spared when a third of its longer lines are spoken somewhere in the
    show - that is real speech the page simply doesn't repeat."""
    drop: list[TranscriptLine] = []
    i = 0
    while i < len(lines):
        if found[i] > 0:
            i += 1
            continue
        j = i
        while j < len(lines) and found[j] == 0:
            j += 1
        run = lines[i:j]
        longer = [line for line in run if len(tokens(line.text)) >= KNOWN_LINE_TOKENS]
        known = sum(1 for line in longer if " ".join(tokens(line.text)) in said_anywhere)
        if len(run) >= MIN_INVENTED_RUN and not (longer and known / len(longer) >= 1 / 3):
            drop.extend(run)
        i = j
    return drop


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Write changes (default: dry-run preview)")
    args = parser.parse_args()

    init_db()  # creates wiki_entries and the new columns if this is the first run
    db = SessionLocal()
    totals = collections.Counter()
    removed_log = []
    try:
        for universe in all_universes():
            if not universe.transcripts_wiki:
                continue
            print(f"Universe {universe.name}: reading {universe.transcripts_wiki}", flush=True)
            wiki = Wiki(universe.transcripts_wiki, report=lambda message: print("  " + message, flush=True))
            said_anywhere = " | ".join(
                " ".join(tokens(e.text)) for entries in wiki.pages.values() for e in entries if e.kind == "line"
            )
            sources = db.execute(
                select(Source).where(Source.show_title.in_(universe.shows)).order_by(Source.show_title, Source.book, Source.episode_number)
            ).scalars()
            for source in sources:
                lines = list(
                    db.execute(
                        select(TranscriptLine).where(TranscriptLine.source_id == source.id).order_by(TranscriptLine.start_time)
                    ).scalars()
                )
                label = f"{source.show_title} B{source.book}E{source.episode_number}"
                if not lines:
                    continue
                alignment = wiki.align([(line.start_time, line.end_time, line.text) for line in lines])
                if alignment is None or alignment.wiki_words_found < MIN_PAGE_MATCH:
                    totals["episodes skipped"] += 1
                    print(f"  SKIP {label}: no transcript page matches (best: {alignment.page if alignment else '-'})")
                    continue

                drop = invented_lines(lines, alignment.found, said_anywhere)
                dropped_ids = {line.id for line in drop}
                with_speaker = sum(1 for line, speaker in zip(lines, alignment.speakers) if speaker and line.id not in dropped_ids)
                descriptions = [e for e in alignment.entries if e.kind == "desc"]
                totals["episodes"] += 1
                totals["lines"] += len(lines)
                totals["lines removed"] += len(drop)
                totals["lines given a speaker"] += with_speaker
                totals["wiki lines stored"] += sum(1 for e in alignment.entries if e.kind == "line")
                totals["descriptions stored"] += len(descriptions)
                totals["descriptions with a time range"] += sum(1 for e in descriptions if e.start is not None)
                print(
                    f"  {label}: {alignment.page[11:]} | page words found {alignment.wiki_words_found:.0%} | "
                    f"speaker on {with_speaker}/{len(lines) - len(drop)} | remove {len(drop)} | descriptions {len(descriptions)}",
                    flush=True,
                )
                for line in drop:
                    removed_log.append({"source_id": source.id, "episode": label, "start": line.start_time, "end": line.end_time, "text": line.text})
                if not args.apply:
                    continue

                for line, speaker in zip(lines, alignment.speakers):
                    if line.id not in dropped_ids and speaker:
                        line.speaker = speaker
                if dropped_ids:
                    for scene in db.execute(select(Scene).where(Scene.source_id == source.id)).scalars():
                        kept = [i for i in (scene.linked_transcript_ids or []) if i not in dropped_ids]
                        if len(kept) != len(scene.linked_transcript_ids or []):
                            scene.linked_transcript_ids = kept
                    db.execute(delete(TranscriptLine).where(TranscriptLine.id.in_(dropped_ids)))
                db.execute(delete(WikiEntry).where(WikiEntry.source_id == source.id))
                db.add_all(
                    WikiEntry(
                        source_id=source.id, position=position, kind=entry.kind, speaker=entry.speaker or None,
                        text=entry.text, start_time=entry.start, end_time=entry.end,
                    )
                    for position, entry in enumerate(alignment.entries)
                )
                source.wiki_page = alignment.page
                db.commit()
    finally:
        db.close()

    print()
    for key, value in totals.items():
        print(f"{key}: {value}")
    if args.apply and removed_log:
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
        path = BACKUPS_DIR / f"transcript_lines_removed_{stamp}.json"
        path.write_text(json.dumps(removed_log, indent=1, ensure_ascii=False), encoding="utf-8")
        print(f"Removed lines written to {path}")
    if not args.apply:
        print("\nPreview only - nothing was changed. Run again with --apply.")


if __name__ == "__main__":
    main()
