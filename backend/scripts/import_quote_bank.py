"""Fills the quote bank: reads curated quote collections, finds each quote in the
episodes (through the fan-wiki transcripts, so run import_wiki_transcripts.py first)
and stores it with its time range.

Collections are the Wikiquote pages listed on each universe (quotes_wiki, quote_pages)
and, optionally, a text file with one quote per line ("Speaker: text" or
'"text" - Speaker') - e.g. a list copied from a forum thread.

Usage (from backend/):
    .venv/Scripts/python scripts/import_quote_bank.py                       # preview
    .venv/Scripts/python scripts/import_quote_bank.py --apply                # replace the bank's Wikiquote quotes
    .venv/Scripts/python scripts/import_quote_bank.py --file quotes.txt --universe avatar --apply
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import delete

from app.db.models import BankQuote
from app.db.session import SessionLocal, init_db
from app.services.quote_bank import Locator, parse_plain, parse_wikiquote, wikiquote_page
from app.services.universes import all_universes


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Write changes (default: dry-run preview)")
    parser.add_argument("--file", help="Text file with one quote per line, instead of Wikiquote")
    parser.add_argument("--universe", help="Universe id (required with --file; default: all)")
    args = parser.parse_args()
    if args.file and not args.universe:
        parser.error("--file needs --universe")

    init_db()
    db = SessionLocal()
    try:
        for universe in all_universes():
            if args.universe and universe.id != args.universe:
                continue
            if args.file:
                origin = f"File: {Path(args.file).name}"
                collected = parse_plain(Path(args.file).read_text(encoding="utf-8"), origin)
            elif universe.quotes_wiki and universe.quote_pages:
                origin = "Wikiquote: "
                collected = [
                    quote
                    for page in universe.quote_pages
                    for quote in parse_wikiquote(wikiquote_page(universe.quotes_wiki, page), page)
                ]
            else:
                continue
            locator = Locator(db, universe.shows)
            per_quote = [locator.locate(quote) for quote in collected]
            found = sum(1 for clips in per_quote if clips)
            located = [clip for clips in per_quote for clip in clips]
            print(
                f"{universe.name}: {len(collected)} collected quotes, {found} found in episodes "
                f"({found / max(len(collected), 1):.0%}) -> {len(located)} clips"
            )
            if not args.apply:
                continue
            # a re-import replaces what the same collection gave before
            db.execute(
                delete(BankQuote).where(
                    BankQuote.origin.startswith(origin), BankQuote.source_id.in_([s.id for s in locator.sources])
                )
            )
            for clip in located:
                db.add(
                    BankQuote(
                        source_id=clip.source_id,
                        start_time=clip.start,
                        end_time=clip.end,
                        text="\n".join(f"{speaker}: {text}" if speaker else text for speaker, text in clip.lines),
                        speakers=", ".join(dict.fromkeys(speaker for speaker, _ in clip.lines if speaker)),
                        origin=clip.origin,
                        match_score=clip.score,
                    )
                )
            db.commit()
        if not args.apply:
            print("Preview only - nothing was changed. Run again with --apply.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
