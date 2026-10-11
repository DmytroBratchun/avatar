"""Screenplays: the same knowledge a fan-wiki transcript gives - who says each line
and what happens on screen between the lines - for a film or show whose wiki has no
transcripts. A screenplay is parsed into the wiki's entries (wiki_transcripts.Entry)
and aligned with our Whisper transcript the same way (scripts/import_wiki_transcripts.py).

A universe lists its screenplays as plain-text files under media/ (Universe.screenplays),
in the usual layout, told apart by indentation: action and scene headings at the left
margin, the speaker's name in capitals far in, the dialogue under it less far in, and
(directions) in brackets between. Page furniture - (CONTINUED), page numbers,
transitions - sits further right still and is dropped.

A screenplay is a draft, not the film: scenes were cut, added and reworded. So its
lines prove nothing about what Whisper heard (the importer removes no transcript lines
on its account), and a description is only placed in time next to a line of its own
scene that was found in the film (Wiki.align, strict).
"""

import re
from pathlib import Path

from app.config import media_path
from app.services.wiki_transcripts import Entry

# indentation, in spaces, from which a line is a speaker's name, and beyond which
# it is page furniture
CUE_INDENT = 18
FURNITURE_INDENT = 35
# up to this indentation a line is action; from here to CUE_INDENT, dialogue
DIALOGUE_INDENT = 5
MIN_DESCRIPTION_CHARS = 20

_HEADING = re.compile(r"^(INT|EXT|INT\./EXT|EXT\./INT)[.\s]")
# a line of its own in capitals ("FADE TO BLACK", "CUT TO:") - "FADE UP: An ancient map..." is action
_TRANSITION = re.compile(r"^((FADE|CUT|DISSOLVE|SMASH CUT|MATCH CUT)\b.*|.*\bTO:|THE END|\(?CONTINUED\b.*|\(MORE\))$")
# titles on the screen, not what it shows
_SUPER = re.compile(r"^SUPER:")
# a page's header or footer: "Final Revision - October, 2003 56."
_FOOTER = re.compile(r"^.*\b(Revision|Draft)\b.*\d+\.?$", re.I)


def _clean(text: str) -> str:
    text = text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", text).strip()


def _sentence_case(text: str) -> str:
    """Screenplays put names and key words in capitals ("SAURON forging the ONE RING");
    the index reads plain text better."""
    return re.sub(r"\b[A-Z][A-Z'\-]+\b", lambda m: "-".join(part.capitalize() for part in m.group(0).split("-")), text)


def _speaker(cue: str) -> str:
    """"GALADRIEL (V.O.) (CONT'D)" -> "Galadriel"."""
    name = re.sub(r"\s*\([^)]*\)", "", cue)
    name = re.sub(r"\s+(V\.?/?[O0]\.?|O\.?S\.?|O\.?C\.?)$", "", name.strip(" ."))
    # scanned scripts leave stray marks around a name
    name = re.sub(r"^[^A-Za-z]+|[^A-Za-z]+$", "", name)
    return re.sub(r"\s+", " ", name).title()


def _mend_speakers(entries: list[Entry]) -> None:
    """A scanned script can split a name with spaces ("GA ND ALF"): such a name takes
    the spelling of the most used name it equals without spaces."""
    counts: dict[str, int] = {}
    for entry in entries:
        if entry.kind == "line":
            counts[entry.speaker] = counts.get(entry.speaker, 0) + 1
    best: dict[str, str] = {}
    for name, _ in sorted(counts.items(), key=lambda item: -item[1]):
        best.setdefault(name.replace(" ", "").lower(), name)
    for entry in entries:
        if entry.kind == "line":
            entry.speaker = best[entry.speaker.replace(" ", "").lower()]


def _is_cue(line: str) -> bool:
    """A speaker's name in capitals - "SMEAGOL (cont'd)" too."""
    name = re.sub(r"\([^)]*\)", "", line)
    return not line.startswith("(") and bool(re.search(r"[A-Z]", name)) and name.upper() == name


def parse(text: str) -> list[Entry]:
    entries: list[Entry] = []
    speaker: str | None = None
    spoken: list[str] = []
    action: list[str] = []
    heading = ""
    in_direction = False  # inside a (direction) that runs over several lines

    def flush() -> None:
        nonlocal speaker, spoken, action, heading
        if speaker and spoken:
            # directions inside a line - "(softly)" - are not spoken
            line = _clean(re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", " ".join(spoken)))
            if line:
                entries.append(Entry(kind="line", speaker=speaker, text=line))
        if action:
            body = _clean(" ".join(action))
            if _HEADING.match(body) and len(action) == 1:
                heading = body  # told with the action that follows
            else:
                body = _sentence_case(f"{heading}. {body}" if heading else body)
                heading = ""
                if len(body) > MIN_DESCRIPTION_CHARS:
                    entries.append(Entry(kind="desc", text=body))
        speaker, spoken, action = None, [], []

    for raw in text.expandtabs(8).splitlines():
        stripped = raw.strip()
        if not stripped:
            flush()
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        if (
            indent >= FURNITURE_INDENT
            or _SUPER.match(stripped)
            or (_TRANSITION.match(stripped) and stripped.upper() == stripped)
            or re.fullmatch(r"\d+[A-Z]?\.", stripped)
            or _FOOTER.match(stripped)
        ):
            continue
        if indent >= CUE_INDENT and _is_cue(stripped):
            flush()
            speaker = _speaker(stripped)
            continue
        if speaker is not None and indent >= DIALOGUE_INDENT:
            if in_direction or stripped.startswith("("):
                in_direction = ")" not in stripped
                continue
            spoken.append(stripped)
            continue
        if speaker is not None:
            flush()
        action.append(stripped)
    flush()
    _mend_speakers(entries)
    return entries


def load(paths: list[str]) -> dict[str, list[Entry]]:
    """{title: entries} for screenplay files given relative to media/ - the title is the file name."""
    pages = {}
    for stored in paths:
        path = Path(media_path(stored))
        pages[path.stem] = parse(path.read_text(encoding="utf-8"))
    return pages
