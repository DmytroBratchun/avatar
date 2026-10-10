"""Gives the quotes of existing projects the longer tail (see quotes.LEAD_OUT and
quotes.retail): each quote's last piece is heard again, and the clip ends a little
after the last word unless someone speaks right after it. Quotes set by hand to
other words are left alone. Runs Whisper on the GPU - stop the app first if the
card has little memory free.

Usage (from backend/):
    .venv/Scripts/python scripts/retail_quotes.py [project id ...]    (all projects when none is given)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.editor import store
from app.editor.quotes import retail


def main() -> None:
    ids = sys.argv[1:] or [p.id for p in store.list_projects()]
    for project_id in ids:
        project = store.load_project(project_id)
        if not project.quotes:
            continue
        if project.job and project.job.status == "running":
            print(f"{project_id}: a stage is running - skipped")
            continue
        cache = store.project_dir(project_id) / "cache"
        cache.mkdir(exist_ok=True)
        changed = {}
        for quote in project.quotes:
            before = quote.clip_end
            if retail(quote, cache):
                changed[quote.id] = (quote.clip_end, quote.speech_end, quote.parts)
                print(f"  {quote.text[:60]!r}: ends {before:.2f} -> {quote.clip_end:.2f} (words end {quote.speech_end:.2f})")

        def apply(p: store.Project) -> None:
            for quote in p.quotes:
                if quote.id in changed:
                    quote.clip_end, quote.speech_end, quote.parts = changed[quote.id]

        if changed:
            store.update_project(project_id, apply)
        print(f"{project_id}: {len(changed)} of {len(project.quotes)} quotes given the longer tail")


if __name__ == "__main__":
    main()
