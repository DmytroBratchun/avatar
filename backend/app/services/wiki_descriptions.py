"""Turns an episode's fan-wiki transcript entries (wiki_entries, see
wiki_transcripts.py) into a text per scene - what the wiki says happens on screen
during the scene, plus who says what - to be embedded and searched like a caption,
at no cost.

A description is only known to lie between two spoken lines. When several
descriptions share one such gap (a stretch with no dialogue), they are spread over
it in page order, each taking a share of the gap in proportion to its length -
longer descriptions cover more screen time.

A comic's entries are placed on pages instead (wiki_comics.py), and a page's text is
simply what the wiki has for that page, in its order."""

from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Scene, Source, WikiEntry

MAX_CHARS = 1500
# a description counts for a scene when they overlap by at least this much...
MIN_OVERLAP_SECONDS = 1.0
# ...or when the overlap is at least this share of the shorter of the two
MIN_OVERLAP_SHARE = 0.5
# a description wedged between two back-to-back lines still describes that moment
MIN_RANGE_SECONDS = 2.0
# a comic page with no description is still indexed when it has this much dialogue:
# a page of talk tells what the page is about, which one line of a scene does not
MIN_PAGE_DIALOGUE_CHARS = 100


def description_ranges(entries: list[WikiEntry]) -> list[tuple[float, float, str]]:
    """(start, end, text) for every description that could be placed in time."""
    ranges: list[tuple[float, float, str]] = []
    i = 0
    while i < len(entries):
        entry = entries[i]
        if entry.kind != "desc" or entry.start_time is None:
            i += 1
            continue
        # consecutive descriptions with the same gap share it
        group = [entry]
        j = i + 1
        while (
            j < len(entries) and entries[j].kind == "desc"
            and entries[j].start_time == entry.start_time and entries[j].end_time == entry.end_time
        ):
            group.append(entries[j])
            j += 1
        start, end = entry.start_time, entry.end_time
        if end - start < MIN_RANGE_SECONDS:
            middle = (start + end) / 2
            start, end = max(0.0, middle - MIN_RANGE_SECONDS / 2), middle + MIN_RANGE_SECONDS / 2
        total = sum(len(e.text) for e in group) or 1
        cursor = start
        for e in group:
            length = (end - start) * len(e.text) / total
            ranges.append((cursor, cursor + length, e.text))
            cursor += length
        i = j
    return ranges


def scene_texts(db: Session, source_id: int) -> dict[int, str]:
    """scene id -> the wiki's account of that scene. Scenes with no description of
    what happens are left out, even when the wiki has their dialogue: a bare line
    ("Zuko: I have changed.") says nothing about what is on screen, and in the index
    it only crowds out scenes that are described."""
    entries = list(
        db.execute(select(WikiEntry).where(WikiEntry.source_id == source_id).order_by(WikiEntry.position)).scalars()
    )
    if not entries:
        return {}
    source = db.get(Source, source_id)
    if source is not None and source.kind == "comic":
        return _page_texts(db, source_id, entries)
    scenes = list(db.execute(select(Scene).where(Scene.source_id == source_id).order_by(Scene.start_time)).scalars())
    descriptions = description_ranges(entries)
    lines = [e for e in entries if e.kind == "line" and e.start_time is not None]

    seen: dict[int, list[str]] = defaultdict(list)
    said: dict[int, list[str]] = defaultdict(list)
    for scene in scenes:
        length = scene.end_time - scene.start_time
        for start, end, text in descriptions:
            overlap = min(end, scene.end_time) - max(start, scene.start_time)
            if overlap <= 0:
                continue
            if overlap >= MIN_OVERLAP_SECONDS or overlap >= MIN_OVERLAP_SHARE * min(end - start, length):
                seen[scene.id].append(text)
        for line in lines:
            middle = (line.start_time + line.end_time) / 2
            if scene.start_time <= middle < scene.end_time:
                said[scene.id].append(f"{line.speaker}: {line.text}" if line.speaker else line.text)

    texts: dict[int, str] = {}
    for scene in scenes:
        if scene.id in seen:
            texts[scene.id] = "\n".join(seen[scene.id] + said.get(scene.id, []))[:MAX_CHARS]
    return texts


def _page_texts(db: Session, source_id: int, entries: list[WikiEntry]) -> dict[int, str]:
    """scene id -> the wiki's account of that page of a comic: the panels described
    and the lines spoken on it, in the wiki's order."""
    on_page: dict[int, list[WikiEntry]] = defaultdict(list)
    for entry in entries:
        if entry.start_time is not None:
            on_page[int(entry.start_time)].append(entry)
    texts: dict[int, str] = {}
    for scene in db.execute(select(Scene).where(Scene.source_id == source_id)).scalars():
        page = on_page.get(int(scene.start_time))
        if not page:
            continue
        described = any(e.kind == "desc" for e in page)
        spoken = sum(len(e.text) for e in page if e.kind == "line")
        if described or spoken >= MIN_PAGE_DIALOGUE_CHARS:
            texts[scene.id] = "\n".join(
                f"{e.speaker}: {e.text}" if e.kind == "line" and e.speaker else e.text for e in page
            )[:MAX_CHARS]
    return texts
