"""Imports the fan wiki's transcripts of comics (see app/services/wiki_comics.py)
for every universe that names a transcripts wiki:

- reads every page of each comic with OCR (local, about a second a page, kept next
  to the page images);
- stores the wiki's lines and descriptions with the page each was placed on in the
  wiki_entries table (start_time = the page, counted from 0);
- gives pages that have no caption their character tags: who speaks on the page,
  and who its descriptions name.

Captions, their tags and their vectors are not touched. Run
scripts/index_wiki_descriptions.py afterwards to give the pages their texts and put
them in the search index.

Usage (from backend/):
    .venv/Scripts/python scripts/import_wiki_comics.py           # preview only
    .venv/Scripts/python scripts/import_wiki_comics.py --apply    # write changes
"""

import argparse
import collections
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import delete, select

from app.db.models import Scene, Source, WikiEntry
from app.db.session import SessionLocal, init_db
from app.services.universes import all_universes
from app.services.wiki_comics import ComicAlignment, WikiComics, page_texts

# a speaker the tags don't know yet becomes one when he speaks on this many pages of
# the comic - a character of its own, not "Guard 2"
MIN_PAGES_FOR_NEW_TAG = 3
# a tag in use is looked for in descriptions when it is on this many captioned scenes
MIN_USES_TO_LOOK_FOR = 5
# speaker labels that are not a character: several people at once, or lettering on the page
GROUP_SPEAKERS = {"both", "all", "everyone", "everybody", "together", "the crowd", "crowd", "textbox", "text box", "caption", "sign"}


def speaker_names(speaker: str) -> list[str]:
    """The characters behind a speaker label: "Katara and Sokka" is two, "Guard 2"
    is "Guard", "Both" is nobody in particular."""
    names = []
    for name in re.split(r"\s+and\s+|\s*[,/&]\s*", speaker):
        name = re.sub(r"\s+#?\d+$", "", name).strip()
        if name and name.lower() not in GROUP_SPEAKERS:
            names.append(name)
    return names


def page_tags(alignments: dict[int, ComicAlignment], in_use: dict[str, str], look_for: list[str]) -> dict[int, dict[int, list[str]]]:
    """source id -> page -> character tags, for the parts of one comic. in_use: tags
    already in the universe, by lower-case name, so a name keeps its spelling."""
    speaks_on: dict[str, set] = collections.defaultdict(set)
    for source_id, alignment in alignments.items():
        for item in alignment.placed:
            if item.entry.kind == "line" and item.page is not None:
                for name in speaker_names(item.entry.speaker):
                    speaks_on[name].add((source_id, item.page))
    cast = {
        name: in_use.get(name.lower(), name)
        for name, pages in speaks_on.items()
        if name.lower() in in_use or len(pages) >= MIN_PAGES_FOR_NEW_TAG
    }
    named = sorted(set(cast.values()) | set(look_for), key=len, reverse=True)
    mention = re.compile(r"(?<![\w'-])(" + "|".join(re.escape(name) for name in named) + r")(?![\w-])") if named else None

    tags: dict[int, dict[int, list[str]]] = {}
    for source_id, alignment in alignments.items():
        pages: dict[int, list[str]] = collections.defaultdict(list)
        for item in alignment.placed:
            if item.page is None:
                continue
            if item.entry.kind == "line":
                found = [cast[name] for name in speaker_names(item.entry.speaker) if name in cast]
            else:
                found = mention.findall(item.entry.text) if mention else []
            for name in found:
                if name not in pages[item.page]:
                    pages[item.page].append(name)
        tags[source_id] = pages
    return tags


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Write changes (default: dry-run preview)")
    args = parser.parse_args()

    init_db()
    db = SessionLocal()
    totals = collections.Counter()
    try:
        for universe in all_universes():
            if not universe.transcripts_wiki or not universe.comics:
                continue
            print(f"Universe {universe.name}: reading {universe.transcripts_wiki}", flush=True)
            wiki = WikiComics(universe.transcripts_wiki)

            uses: collections.Counter[str] = collections.Counter()
            for tags in db.execute(
                select(Scene.character_tags).where(
                    Scene.show_title.in_(universe.shows + universe.comics), Scene.caption_status == "done"
                )
            ).scalars():
                uses.update(tags or [])
            in_use: dict[str, str] = {}
            for name, _ in uses.most_common():
                in_use.setdefault(name.lower(), name)
            look_for = [name for name, count in uses.items() if count >= MIN_USES_TO_LOOK_FOR]

            sources = list(
                db.execute(
                    select(Source)
                    .where(Source.kind == "comic", Source.show_title.in_(universe.comics))
                    .order_by(Source.show_title, Source.episode_number)
                ).scalars()
            )
            by_title: dict[str, dict[int, ComicAlignment]] = collections.defaultdict(dict)
            for source in sources:
                label = source.show_title + (f" {source.episode_number}" if source.episode_number else "")
                texts = page_texts(source.id)
                if not texts:
                    print(f"  SKIP {label}: pages are not rendered yet")
                    continue
                alignment = wiki.align(texts)
                if alignment is None:
                    totals["comics with no transcript"] += 1
                    print(f"  SKIP {label}: no transcript on the wiki")
                    continue
                by_title[source.show_title][source.id] = alignment
                with_text = len({item.page for item in alignment.placed if item.page is not None})
                totals["comics"] += 1
                totals["pages"] += len(texts)
                totals["pages with wiki text"] += with_text
                totals["wiki lines"] += alignment.lines
                totals["wiki lines found on a page"] += alignment.lines_found
                totals["descriptions"] += sum(1 for item in alignment.placed if item.entry.kind == "desc")
                print(
                    f"  {label}: {', '.join(title[11:] for title in alignment.wiki_pages)} | "
                    f"lines found {alignment.lines_found}/{alignment.lines} | pages with text {with_text}/{len(texts)}",
                    flush=True,
                )

            for title, alignments in by_title.items():
                tags = page_tags(alignments, in_use, look_for)
                for source_id, alignment in alignments.items():
                    scenes = list(db.execute(select(Scene).where(Scene.source_id == source_id)).scalars())
                    tagged = [
                        (scene, tags[source_id].get(int(scene.start_time), []))
                        for scene in scenes
                        if scene.caption_status != "done" and not scene.edited_by_user
                    ]
                    totals["pages given character tags"] += sum(1 for _, names in tagged if names)
                    if not args.apply:
                        continue
                    for scene, names in tagged:
                        scene.character_tags = names
                    db.execute(delete(WikiEntry).where(WikiEntry.source_id == source_id))
                    db.add_all(
                        WikiEntry(
                            source_id=source_id, position=position, kind=item.entry.kind,
                            speaker=item.entry.speaker or None, text=item.entry.text,
                            start_time=None if item.page is None else float(item.page),
                            end_time=None if item.page is None else float(item.page + 1),
                        )
                        for position, item in enumerate(alignment.placed)
                    )
                    db.get(Source, source_id).wiki_page = "; ".join(alignment.wiki_pages)
                    db.commit()
                cast = collections.Counter(name for pages in tags.values() for names in pages.values() for name in names)
                print(f"  {title}: " + ", ".join(f"{name} {count}" for name, count in cast.most_common(14)))
    finally:
        db.close()

    print()
    for key, value in totals.items():
        print(f"{key}: {value}")
    if not args.apply:
        print("\nPreview only - nothing was changed. Run again with --apply.")
    else:
        print("\nNow run scripts/index_wiki_descriptions.py --apply to index the pages' texts.")


if __name__ == "__main__":
    main()
