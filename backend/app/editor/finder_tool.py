"""Command-line tools for the Claude session that looks for specific moments (see
editor/moments.py). It runs them through its Bash tool, as

    python finder_tool.py search "Iroh redirects lightning" --show "Avatar: The Last Airbender" --season 1
    python finder_tool.py episode 53 [--from 12:00 --to 16:30]
    python finder_tool.py scene 1555

Search goes through the running backend (/api/search), which has the embedding
models loaded; the rest reads the database directly. Output is plain text, short
enough to read.
"""

import argparse
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # the backend folder, for "app"

API = "http://localhost:8000/api"


def _clock(seconds: float) -> str:
    return f"{int(seconds // 60)}:{int(seconds % 60):02d}"


def _seconds(text: str) -> float:
    minutes, _, seconds = text.partition(":")
    return int(minutes) * 60 + float(seconds or 0) if seconds else float(minutes)


def _where(show, book, episode) -> str:
    """"Show season 1 episode 2", or just the show for a film."""
    return " ".join([str(show)] + ([f"season {book}"] if book is not None else []) + ([f"episode {episode}"] if episode is not None else []))


def search(args) -> None:
    params = [("q", args.query), ("mode", args.mode), ("limit", str(args.limit))]
    if args.show:
        params.append(("shows", args.show))
    if args.season is not None:
        params.append(("book", str(args.season)))
    if args.episode is not None:
        params.append(("episode_number", str(args.episode)))
    with urllib.request.urlopen(f"{API}/search?{urllib.parse.urlencode(params)}", timeout=120) as response:
        results = json.load(response)
    if not results:
        print("nothing found")
    for result in results:
        scene = result["scene"]
        about = (scene.get("context_description") or scene.get("wiki_description") or "(no description)").replace("\n", " ")
        print(
            f"S{scene['id']} | {_where(scene.get('show_title'), scene.get('book'), scene.get('episode_number'))} "
            f"(episode id {scene['source_id']}) {_clock(scene['start_time'])}-{_clock(scene['end_time'])} | {about[:260]}"
        )


def episode(args) -> None:
    from app.db.models import Source
    from app.db.session import SessionLocal
    from app.editor.moments import episode_text

    db = SessionLocal()
    source = db.get(Source, args.id)
    if source is None:
        print("no such episode")
        return
    start = _seconds(args.start) if args.start else 0.0
    end = _seconds(args.end) if args.end else float("inf")
    print(episode_text(db, source, _where(source.show_title, source.book, source.episode_number), start, end)[0])


def scene(args) -> None:
    from sqlalchemy import select

    from app.db.models import Scene, Source, TranscriptLine
    from app.db.session import SessionLocal

    db = SessionLocal()
    found = db.get(Scene, args.id)
    if found is None:
        print("no such scene")
        return
    source = db.get(Source, found.source_id)
    print(
        f"S{found.id} | {_where(source.show_title, source.book, source.episode_number)} (episode id {source.id}) "
        f"{_clock(found.start_time)}-{_clock(found.end_time)}"
    )
    if found.context_description:
        print("What happens:", found.context_description)
    if found.wiki_description:
        print("Fan wiki:", found.wiki_description)
    lines = db.execute(
        select(TranscriptLine).where(
            TranscriptLine.source_id == found.source_id,
            TranscriptLine.start_time >= found.start_time - 1,
            TranscriptLine.start_time < found.end_time,
        ).order_by(TranscriptLine.start_time)
    ).scalars()
    for line in lines:
        print(f"  {_clock(line.start_time)} " + (f"{line.speaker}: " if line.speaker else "") + line.text.strip())
    neighbours = db.execute(
        select(Scene.id, Scene.start_time).where(Scene.source_id == found.source_id).order_by(Scene.start_time)
    ).all()
    ids = [row[0] for row in neighbours]
    i = ids.index(found.id)
    print(f"Scene before: S{ids[i - 1]}" if i > 0 else "First scene", "|", f"Scene after: S{ids[i + 1]}" if i + 1 < len(ids) else "Last scene")


def main() -> None:
    parser = argparse.ArgumentParser(prog="finder_tool")
    sub = parser.add_subparsers(dest="command", required=True)
    s = sub.add_parser("search", help="search the scene index by meaning")
    s.add_argument("query")
    s.add_argument("--show")
    s.add_argument("--season", type=int)
    s.add_argument("--episode", type=int, help="episode number within the season")
    s.add_argument("--mode", default="hybrid", choices=["hybrid", "text", "visual"])
    s.add_argument("--limit", type=int, default=10)
    s.set_defaults(run=search)
    e = sub.add_parser("episode", help="read an episode scene by scene, with dialogue")
    e.add_argument("id", type=int, help="episode id")
    e.add_argument("--from", dest="start")
    e.add_argument("--to", dest="end")
    e.set_defaults(run=episode)
    c = sub.add_parser("scene", help="one scene in full")
    c.add_argument("id", type=int)
    c.set_defaults(run=scene)
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    args.run(args)


if __name__ == "__main__":
    main()
