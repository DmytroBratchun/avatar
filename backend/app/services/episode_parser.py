import re
from dataclasses import dataclass
from pathlib import Path

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".avi", ".mov", ".m4v", ".webm"}

_SXXEYY = re.compile(r"[sS](\d{1,2})[eE](\d{1,3})")
_XX_YY = re.compile(r"\b(\d{1,2})x(\d{1,3})\b")
_BOOK_WORD = re.compile(r"\b(?:book|season)\s*(\d)\b", re.IGNORECASE)


@dataclass
class ParsedEpisode:
    book: int | None
    episode_number: int | None
    episode_title: str | None


def _clean_title(stem: str, matched_span: tuple[int, int] | None) -> str | None:
    text = stem
    if matched_span:
        text = stem[: matched_span[0]] + " " + stem[matched_span[1] :]
    text = re.sub(r"[._]+", " ", text)
    text = re.sub(r"\s{2,}", " ", text).strip(" -_")
    return text or None


def parse_episode_info(file_path: Path) -> ParsedEpisode:
    stem = file_path.stem
    book = None
    for parent in file_path.parents:
        match = _BOOK_WORD.search(parent.name)
        if match:
            book = int(match.group(1))
            break

    match = _SXXEYY.search(stem)
    if match:
        book = book or int(match.group(1))
        return ParsedEpisode(book=book, episode_number=int(match.group(2)), episode_title=_clean_title(stem, match.span()))

    match = _XX_YY.search(stem)
    if match:
        book = book or int(match.group(1))
        return ParsedEpisode(book=book, episode_number=int(match.group(2)), episode_title=_clean_title(stem, match.span()))

    return ParsedEpisode(book=book, episode_number=None, episode_title=_clean_title(stem, None))


def find_video_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS)
