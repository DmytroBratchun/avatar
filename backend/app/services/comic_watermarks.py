"""Removes the "Read more FREE comics on ReadComicOnline" strip that the site some
of the comic scans came from stamped at the foot of their pages.

A page carries it when its OCR text (wiki_comics.page_texts) has those words. The
page is read again with OCR to find where the words are, the black band they sit
in is followed up from there, and the page image is cut off above it. The image
as it was is kept in <page folder>/original/, so nothing is lost and running it
again does nothing.
"""

import re
from pathlib import Path

import numpy as np
from PIL import Image

from app.services.comics import PAGES_DIR

WATERMARK = re.compile(r"read\s*more\s*free|free\s*comics\s*on|readcomic|comiconline", re.I)
DARK = 60  # a pixel of the band
BAND_DARK_SHARE = 0.5  # a row of the band is at least this dark
MAX_SHARE = 0.12  # never cut more than this much of the page


def band_top(image: Image.Image, text_top: int) -> int | None:
    """The first row of the dark band around the watermark, which starts at or above
    the top of its words; None when no such band is found."""
    gray = np.asarray(image.convert("L"))
    height = gray.shape[0]
    dark = (gray < DARK).mean(axis=1)
    row = min(max(0, text_top), height - 1)
    if dark[row:].mean() < 0.3:
        return None  # the words are not on a dark band: leave the page alone
    while row > 0 and dark[row - 1] >= BAND_DARK_SHARE:
        row -= 1
    return row if height - row <= MAX_SHARE * height else None


def strip_page(page: Path, engine) -> bool:
    """Cuts the watermark off one page. Returns whether the page was changed."""
    original = page.parent / "original" / page.name
    source = original if original.exists() else page
    found, _ = engine(str(source))
    tops = [min(point[1] for point in box) for box, text, _ in found or [] if WATERMARK.search(text)]
    if not tops:
        return False
    with Image.open(source) as image:
        image.load()
    top = band_top(image, int(min(tops)))
    if top is None:
        return False
    if not original.exists():
        original.parent.mkdir(exist_ok=True)
        page.replace(original)
    image.crop((0, 0, image.width, top)).save(page, "JPEG", quality=92)
    return True


def strip_comic(source_id: int, texts: list[str], engine=None) -> list[int]:
    """Cuts the watermark off every page of a comic whose OCR text has it. Returns
    the numbers (from 1) of the pages changed."""
    pages = sorted((PAGES_DIR / str(source_id)).glob("page_*.jpg"))
    flagged = [page for page, text in zip(pages, texts) if WATERMARK.search(text)]
    if not flagged:
        return []
    if engine is None:
        from rapidocr_onnxruntime import RapidOCR

        engine = RapidOCR()
    return [pages.index(page) + 1 for page in flagged if strip_page(page, engine)]
