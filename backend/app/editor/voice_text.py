"""A script made ready for the synthetic voice: directions in square brackets
([whispers], [serious]) where the delivery changes, pauses through punctuation,
numbers and abbreviations spelled out. One Claude call rewrites the whole script,
so the directions follow its arc; the words themselves are not to change, and
what did change is reported back (see changed_words).

The rules are ElevenLabs' own for Eleven v4 / v3 - the models that act on tags
(elevenlabs.io/docs, "Best practices" for text to speech, and their audio-tag
guides).
"""

import difflib
import re
import tempfile
from pathlib import Path

from app.editor.llm import run_claude_json

TIMEOUT = 900
# directions to the voice ([whispers]) and Lumean's silences ({{pause=1.5}})
_UNSPOKEN = re.compile(r"\[[^\[\]\n]{1,80}\]|\{\{[^{}\n]{1,40}\}\}")

SCHEMA = {
    "type": "object",
    "properties": {
        "text": {"type": "string", "description": "The whole script, ready to be voiced. Nothing but the script."},
    },
    "required": ["text"],
}

RULES = """\
Audio tags
- A tag is a direction to the voice in square brackets, in English, placed right before the words it applies to: \
[curious], [excited], [serious], [calm], [sarcastic], [mischievously], [warm], [ominous], [whispers], [amused], \
[awed], [matter-of-fact], [confident]. Reactions: [sighs], [laughs], [chuckles], [exhales]. Pauses: [pause], \
[long pause].
- When no standard tag says it, describe the delivery: [low, steady voice, restrained awe], [quietly, almost a \
secret]. Nuances are combined inside one pair of brackets, separated by commas: [whispering, playful].
- A delivery carries on until the next tag, so tag only where the delivery changes. The model reads emotion from \
the text itself: a well-written passage needs no tag. On average one tag per one to three paragraphs; too many \
make the reading theatrical and unstable.
- A tag can come in the middle of a paragraph, before the sentence or clause it changes: "He looked up. [quietly] \
Nobody was there." A direction always has words after it - never leave one at the end of the script or alone in a \
paragraph (only a reaction such as [sighs] may follow the words it belongs to).
- One emotion per phrase; never contradicting tags next to each other.
- A tag must describe a voice, not a sound: [in a hushed voice], not [hush]. No sound effects or music ([explosion], \
[wind], [applause], [music]) and no stage business ([standing], [grinning]).
- Tags have to suit a narrator: no [giggles] or [crying] for a calm documentary voice.

Pauses and emphasis (SSML such as <break> is not supported)
- An ellipsis ... is a pause that gives weight to what follows. A dash is a sharp turn of thought.
- CAPITALS on one word are emphasis: at most one word per paragraph, only where the sentence needs it.
- Pause the same way through the whole script - do not pause by punctuation in one half and by [pause] tags in the \
other. Prefer punctuation; keep [pause] tags rare.
- Leave the script's own short fragments ("Weeks.") alone: they already are its rhythm.

Normalisation (so the voice does not guess)
- Numbers in words: 36 -> thirty-six, 1,000 -> one thousand, 3.5 -> three point five, 1990s -> nineteen nineties. \
Ordinals, years, times and money as they are said: $42.50 -> forty-two dollars and fifty cents.
- Abbreviations expanded or spelled as said: vs -> versus, Dr. -> Doctor, km -> kilometres.
- The symbols % $ & / + = in words.

What must not change
- The words. Add tags, change punctuation, normalise numbers, abbreviations and symbols - nothing else. Do not \
reword, cut, add or reorder anything, and keep the paragraphs as they are.
- Tags already in the script stay, unless the author's request below says otherwise; so do pauses written as \
{{pause=1.5}}.
- Brackets in the script that are not directions to the voice - notes for the edit such as [CLIP: ...], footnote \
marks such as [1] - are removed whole, with the line they stand alone on: the voice would try to act them.
- Never put the script's own words inside brackets.
- No headings, notes, comments or markdown: the result goes to the voice as it is.
"""


def spoken(text: str | None) -> str:
    """The script without what is not said aloud: voice directions and pauses."""
    if not text:
        return ""
    return re.sub(r"[ \t]{2,}", " ", _UNSPOKEN.sub("", text))


def _words(text: str) -> list[str]:
    return re.findall(r"[^\W_]+(?:['’.,][^\W_]+)*|[%$&/+=]", spoken(text))


def _normalised(piece: list[str]) -> bool:
    """Whether a changed stretch of the original is something normalisation is meant
    to rewrite: a number, a symbol, an abbreviation."""
    return any(
        re.search(r"\d", w) or w in "%$&/+=" or "." in w or (w.isupper() and len(w) <= 5) or w.lower() in ("vs", "etc")
        for w in piece
    )


def changed_words(before: str, after: str, limit: int = 12) -> list[dict]:
    """Places where the wording itself changed - beyond tags, punctuation, capitals
    and normalised numbers. [{was, now}], with a word of context on each side."""
    old, new = _words(before), _words(after)
    key = lambda w: w.lower().replace("’", "'")
    changes = []
    matcher = difflib.SequenceMatcher(None, [key(w) for w in old], [key(w) for w in new], autojunk=False)
    for op, a1, a2, b1, b2 in matcher.get_opcodes():
        if op == "equal" or (op == "replace" and _normalised(old[a1:a2])):
            continue
        changes.append({"was": " ".join(old[max(0, a1 - 2) : a2 + 2]), "now": " ".join(new[max(0, b1 - 2) : b2 + 2])})
        if len(changes) == limit:
            break
    return changes


def optimize(text: str, instructions: str = "") -> tuple[str, list[dict], float | None]:
    """The script rewritten for the voice. Returns it with the places where its
    wording changed (there should be none) and what the call cost."""
    text = text.strip()
    if not text:
        raise RuntimeError("There is no script to prepare")
    prompt = (
        "Below is the narration script of a video essay. It will be read by a synthetic voice (ElevenLabs Eleven "
        "v4), which acts on directions written into the text. Prepare the script for that voice: direct the "
        "delivery so it follows the script's own drama - the hook, the explanations, the dark and the light "
        "passages, the ending - and make it unambiguous to read. Follow these rules, which are the voice "
        "maker's own.\n\n"
        + RULES
        + (
            "\nThe author's request for this script. It comes before the guidance above on which tags to use "
            "and how many - do what it asks; only the rules on what must not change still hold:\n"
            + instructions.strip() + "\n"
            if instructions.strip()
            else ""
        )
        + "\nAnswer with the whole script.\n\nScript:\n"
        + text
    )
    # away from the repo, so the call doesn't load its CLAUDE.md
    result, cost = run_claude_json(prompt, SCHEMA, cwd=Path(tempfile.gettempdir()), timeout=TIMEOUT, task="voice_text")
    ready = (result.get("text") or "").strip()
    if not ready:
        raise RuntimeError("Claude returned no script")
    return ready, changed_words(text, ready), cost
