"""Files the music already composed for the existing projects in the music library
(editor/music_library.py; `music_library` in settings.json names its folder).
Tracks the user unticked are filed as rejected, so they are not offered again.
Safe to run again: a track already there only gets its project noted. It only
reads the projects - nothing in them is changed.

Usage (from backend/):
    .venv/Scripts/python scripts/import_music_library.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.editor import music_library, store
from app.editor.music import FOLDER


def main() -> None:
    if music_library.root() is None:
        sys.exit('Set "music_library" in data/settings.json to the library folder first')
    for project in store.list_projects():
        folder = store.project_dir(project.id) / FOLDER
        filed = missing = 0
        for block in project.music:
            for track in block.tracks:
                if music_library.add(project, block, track, folder / track.file):
                    filed += 1
                else:
                    missing += 1
        print(f"{project.id}: {filed} tracks filed" + (f", {missing} without a file" if missing else ""))
    tracks = music_library.tracks()
    print(f"library: {len(tracks)} tracks, {sum(1 for t in tracks if t.rejected)} rejected, in {music_library.root()}")


if __name__ == "__main__":
    main()
