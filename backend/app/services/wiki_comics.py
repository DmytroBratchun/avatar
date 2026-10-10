"""Fan-wiki transcripts of comics: which page each spoken line and each description
of a panel belongs to.

The wiki writes a comic out the way it does an episode (see wiki_transcripts.py) -
lines with their speakers, descriptions of what is drawn between them - with no
page numbers, and the PDFs are scans with no text. So every page is read with OCR,
and a wiki line goes to the page its letters are found on. OCR of comic lettering
loses the spaces between words ("AVATAR'SPERSONAL"), so text is compared by runs of
letters, not by words. Lines come in reading order, so along the transcript the
page never goes back; that settles the short lines ("Yes!") found on many pages.

One PDF can hold several wiki pages (a collection of short stories), so the wiki
pages of a comic are found by their wording, not by title: every page most of whose
lines are in the PDF.
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path

from app.services.comics import PAGES_DIR
from app.services.wiki_transcripts import Entry, page_wikitext, parse_page, transcript_pages

GRAM = 4  # letters in a run
# a line is found on a page when this share of its runs of letters is there
LINE_FOUND_SHARE = 0.6
# lines shorter than this many runs are on too many pages to tell a comic by
TELLING_LINE_GRAMS = 12
# a wiki page belongs to the comic when this share of its telling lines is found
PAGE_FOUND_SHARE = 0.5
# what moving on a page costs, in runs of letters: a story's lines stay together, so
# a short line ("Yes!") is not placed on a far page that happens to have the word
PAGE_SKIP_COST = 0.5
MIN_LINES_PER_PAGE = 5  # short stories are a page or two long
# a page of the wiki whose wording is this little in the comic is not looked at closer
PAGE_PREFILTER_SHARE = 0.5


@dataclass
class Placed:
    """A wiki entry and the page of the PDF (from 0) it was put on, or None."""

    entry: Entry
    page: int | None


@dataclass
class ComicAlignment:
    wiki_pages: list[str]
    placed: list[Placed]
    lines: int = 0
    lines_found: int = 0


def _letters(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _grams(text: str) -> set[str]:
    letters = _letters(text)
    return {letters[i : i + GRAM] for i in range(len(letters) - GRAM + 1)}


def page_texts(source_id: int, report=None) -> list[str]:
    """What is written on each rendered page of the comic, read with OCR (local,
    free; about a second a page). Kept next to the page images."""
    folder = PAGES_DIR / str(source_id)
    images = sorted(folder.glob("page_*.jpg"))
    cache = folder / "ocr.json"
    if cache.exists():
        texts = json.loads(cache.read_text(encoding="utf-8"))
        if len(texts) == len(images):
            return texts
    from rapidocr_onnxruntime import RapidOCR

    engine = RapidOCR()
    texts = []
    for number, image in enumerate(images):
        found, _ = engine(str(image))
        texts.append(" ".join(item[1] for item in found or []))
        if report:
            report(number + 1, len(images))
    cache.write_text(json.dumps(texts, ensure_ascii=False), encoding="utf-8")
    # the scan site's "read more free comics" strip at the foot of some pages
    from app.services.comic_watermarks import strip_comic

    strip_comic(source_id, texts, engine)
    return texts


class WikiComics:
    """All of a wiki's transcripts, to find and place those of a comic."""

    def __init__(self, base: str):
        self.pages: dict[str, list[Entry]] = {}
        for title in transcript_pages(base):
            entries = parse_page(page_wikitext(base, title))
            if sum(1 for e in entries if e.kind == "line") >= MIN_LINES_PER_PAGE:
                self.pages[title] = entries
        self._grams = {
            title: set().union(*(_grams(e.text) for e in entries if e.kind == "line"))
            for title, entries in self.pages.items()
        }

    def align(self, texts: list[str]) -> ComicAlignment | None:
        """Places the wiki's transcripts of this comic on its pages (texts: what OCR
        read on each). None when the wiki has no transcript of it."""
        on_page = [_grams(text) for text in texts]
        anywhere = set().union(*on_page) if on_page else set()
        found: list[tuple[int, str, list[Placed], int, int]] = []
        for title, grams in self._grams.items():
            if not grams or len(grams & anywhere) / len(grams) < PAGE_PREFILTER_SHARE:
                continue
            placed, telling, telling_found, lines_found = _place(self.pages[title], on_page)
            if telling and telling_found / telling >= PAGE_FOUND_SHARE:
                pages = [p.page for p in placed if p.page is not None and p.entry.kind == "line"]
                found.append((min(pages), title, placed, sum(1 for e in self.pages[title] if e.kind == "line"), lines_found))
        if not found:
            return None
        found.sort(key=lambda item: item[0])  # stories in the order the PDF has them
        return ComicAlignment(
            wiki_pages=[title for _, title, _, _, _ in found],
            placed=[p for _, _, placed, _, _ in found for p in placed],
            lines=sum(item[3] for item in found),
            lines_found=sum(item[4] for item in found),
        )


def _place(entries: list[Entry], on_page: list[set[str]]) -> tuple[list[Placed], int, int, int]:
    """(entries with their pages, telling lines, telling lines found, lines found)."""
    lines = [(index, _grams(entry.text)) for index, entry in enumerate(entries) if entry.kind == "line"]
    count = len(on_page)
    # weight[k][p]: how many of line k's runs of letters are on page p
    weight = [[len(grams & page) for page in on_page] for _, grams in lines]

    # the pages that keep the lines in order and hold the most of their letters
    best = [0.0] * count
    came_from: list[list[int]] = []
    for row in weight:
        running, at, new, choice = float("-inf"), 0, [], []
        for page in range(count):
            running -= PAGE_SKIP_COST
            if best[page] > running:
                running, at = best[page], page
            new.append(running + row[page])
            choice.append(at)
        best = new
        came_from.append(choice)
    page = max(range(count), key=lambda p: best[p]) if lines else 0
    line_page: dict[int, int] = {}
    telling = telling_found = lines_found = 0
    for k in range(len(lines) - 1, -1, -1):
        index, grams = lines[k]
        is_found = bool(grams) and weight[k][page] / len(grams) >= LINE_FOUND_SHARE
        if is_found:
            line_page[index] = page
            lines_found += 1
        if len(grams) >= TELLING_LINE_GRAMS:
            telling += 1
            telling_found += is_found
        page = came_from[k][page]

    placed = [Placed(entry, line_page.get(index)) for index, entry in enumerate(entries)]
    _place_between(placed)
    return placed, telling, telling_found, lines_found


def _place_between(placed: list[Placed]) -> None:
    """Pages for what was not read off a page: a description of a panel, and a line
    OCR missed. Each lies between the found lines around it. A run of them between
    two pages is spread over those pages in order, by length - a description comes
    before the lines of its panel, so the run leans to the later page."""
    index = 0
    while index < len(placed):
        if placed[index].page is not None:
            index += 1
            continue
        end = index
        while end < len(placed) and placed[end].page is None:
            end += 1
        before = next((placed[k].page for k in range(index - 1, -1, -1) if placed[k].page is not None), None)
        after = placed[end].page if end < len(placed) else None
        run = placed[index:end]
        if before is None and after is None:
            return
        if before is None or after is None or before == after:
            for item in run:
                item.page = after if after is not None else before
        else:
            # the pages after `before`, up to `after`: what follows a page's last found
            # line mostly opens the next page
            pages = list(range(before + 1, after + 1))
            total = sum(len(item.entry.text) for item in run) or 1
            done = 0
            for item in run:
                middle = (done + len(item.entry.text) / 2) / total
                item.page = pages[min(len(pages) - 1, int(middle * len(pages)))]
                done += len(item.entry.text)
        index = end


def ocr_path(source_id: int) -> Path:
    return PAGES_DIR / str(source_id) / "ocr.json"
