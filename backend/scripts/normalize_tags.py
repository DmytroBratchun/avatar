"""One-off maintenance: merge near-duplicate tag spellings across all scenes
(e.g. "Fire Nation Ship" / "Fire Nation ship", "Otter Penguin" / "Otter Penguins").

Doesn't touch embeddings - tags aren't part of the text/visual vectors, only
character_tags/location_tags/mood_action_tags on each scene row.

Usage:
    .venv/Scripts/python scripts/normalize_tags.py           # preview only
    .venv/Scripts/python scripts/normalize_tags.py --apply    # actually write changes
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.models import Scene
from app.db.session import SessionLocal
from app.services.tag_normalize import apply_canonical_map, build_canonical_map

FIELDS = ["character_tags", "location_tags", "mood_action_tags"]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Write changes (default: dry-run preview)")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        scenes = db.query(Scene).all()
        if not scenes:
            print("No scenes in database.")
            return

        total_changed = 0
        for field in FIELDS:
            all_lists = [getattr(s, field) or [] for s in scenes]
            mapping = build_canonical_map(all_lists)
            if not mapping:
                print(f"{field}: nothing to merge.")
                continue

            print(f"\n{field}: merging {len(mapping)} variant(s):")
            for raw, canonical in sorted(mapping.items()):
                print(f"  {raw!r:40s} -> {canonical!r}")

            if args.apply:
                for scene in scenes:
                    current = getattr(scene, field) or []
                    updated = apply_canonical_map(current, mapping)
                    if updated != current:
                        setattr(scene, field, updated)
                        total_changed += 1

        if args.apply:
            db.commit()
            print(f"\nApplied. {total_changed} scene field(s) updated.")
        else:
            print("\nDry run only - rerun with --apply to write these changes.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
