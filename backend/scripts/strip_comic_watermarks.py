"""Cuts the "Read more FREE comics on ReadComicOnline" strip off the pages of every
comic already in the library (see app/services/comic_watermarks.py). Needs the
pages' OCR text (scripts/import_wiki_comics.py makes it). The images as they were
are kept in each comic's original/ folder. Comics read with OCR from now on are
cleaned as part of that.

Usage (from backend/):
    .venv/Scripts/python scripts/strip_comic_watermarks.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.comic_watermarks import strip_comic
from app.services.comics import PAGES_DIR


def main() -> None:
    from rapidocr_onnxruntime import RapidOCR

    engine = RapidOCR()
    total = 0
    for cache in sorted(PAGES_DIR.glob("*/ocr.json")):
        source_id = int(cache.parent.name)
        changed = strip_comic(source_id, json.loads(cache.read_text(encoding="utf-8")), engine)
        if changed:
            print(f"comic {source_id}: pages {', '.join(map(str, changed))}", flush=True)
            total += len(changed)
    print(f"{total} pages cleaned")


if __name__ == "__main__":
    main()
