"""Comics as sources. A comic is a PDF; each page is indexed the way a scene of an
episode is - it gets a description and goes into the text index - so that footage
search can offer a page where the narration is about something from a comic.

A page is a Scene whose start_time is the page's number counted from 0 and whose
one "keyframe" is the page rendered to an image. There is no transcript, no shots
and no visual index: pages are found by what happens on them.
"""

import re
from pathlib import Path

from app.config import DATA_DIR
from app.services.universes import Universe, all_universes

PAGES_DIR = DATA_DIR / "comics"
# Pages are rendered this wide. The PDFs are scans of some 700-900px, so this is
# already an enlargement - enough to stay smooth when a page fills the screen.
PAGE_WIDTH = 1400

COMIC_SUFFIXES = {".pdf"}


def is_comic_file(path: str) -> bool:
    return Path(path).suffix.lower() in COMIC_SUFFIXES


def _plain(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def name_from_file(path: str) -> tuple[str, int | None, Universe | None]:
    """(series title, part number, universe) guessed from a comic's file name:
    "Avatar-The Last Airbender-The Promise 2.pdf" is part 2 of "The Promise", in the
    universe one of whose shows the name starts with."""
    stem = _shelf_name(Path(path).stem)
    universe = None
    title = stem
    for candidate in all_universes():
        for show in sorted(candidate.shows, key=len, reverse=True):
            if _plain(stem).startswith(_plain(show)):
                universe = candidate
                # drop as many leading words as the show's title has
                words = re.findall(r"[A-Za-z0-9']+", stem)
                rest = words[len(_plain(show).split()):]
                title = " ".join(rest) or stem
                break
        if universe:
            break
    number = None
    # "The Promise - Part 2", "The Kyoshi Warriors - Issue 2 of 3", "The Promise 2"
    numbered = re.match(
        r"^(.*?)[\s_#-]*(?:(?:part|issue|volume|vol\.?|book)\s*)?#?(\d{1,3})(?:\s+of\s+\d+)?$", title.strip(), re.IGNORECASE
    )
    if numbered and numbered.group(1).strip(" -_"):
        title, number = numbered.group(1).strip(" -_"), int(numbered.group(2))
    return title.strip(), number, universe or _universe_from_folders(path)


def _shelf_name(name: str) -> str:
    """A file or folder name without what only orders it on a shelf: a leading
    number and a year in brackets at the end ("13 The Tea Brewer (2024)")."""
    name = re.sub(r"\s*\((?:19|20)\d{2}[^)]*\)\s*$", "", name)
    return re.sub(r"^\d{1,3}[\s._-]+(?=\D)", "", name).strip()


def _universe_from_folders(path: str) -> Universe | None:
    """The universe a comic is filed under when its own name does not say: a folder
    above it named after one of the universe's shows - in full, without the article,
    or by its initials ("ATLA") - else the only universe there is."""
    universes = all_universes()
    for folder in reversed(Path(path).parts[:-1]):
        name = _plain(_shelf_name(folder))
        if not name:
            continue
        for candidate in universes:
            for show in candidate.shows:
                full = _plain(show)
                initials = "".join(word[0] for word in full.split())
                if name.startswith(full) or name == initials or (len(name.split()) > 1 and full.endswith(name)):
                    return candidate
    return universes[0] if len(universes) == 1 else None


def page_count(pdf_path: str) -> int:
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(pdf_path)
    try:
        return len(document)
    finally:
        document.close()


def render_pages(pdf_path: str, source_id: int, report=None) -> list[str]:
    """Writes every page of the PDF as a JPEG and returns the paths, in page order.
    Pages already rendered are kept."""
    import pypdfium2 as pdfium

    out = PAGES_DIR / str(source_id)
    out.mkdir(parents=True, exist_ok=True)
    document = pdfium.PdfDocument(pdf_path)
    paths: list[str] = []
    try:
        total = len(document)
        for index in range(total):
            target = out / f"page_{index + 1:03d}.jpg"
            if not target.exists():
                page = document[index]
                width, _ = page.get_size()
                image = page.render(scale=PAGE_WIDTH / width).to_pil().convert("RGB")
                partial = target.with_suffix(".part.jpg")
                image.save(partial, "JPEG", quality=92)
                partial.replace(target)
            paths.append(str(target))
            if report:
                report(index + 1, total)
    finally:
        document.close()
    return paths
