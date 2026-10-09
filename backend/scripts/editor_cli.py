"""The video editor from the command line - for making a video project out of a
finished script without opening the UI (and for Claude Code to do it when asked).
Talks to the running backend (./start.sh), so everything it does shows up in the
UI at once.

    python scripts/editor_cli.py universes
    python scripts/editor_cli.py list
    python scripts/editor_cli.py create --title "Why Zuko resonates" --script script.txt [--notes notes.md]
                                        [--universe avatar] [--show "Avatar: The Last Airbender"]... [--layout framed]
    python scripts/editor_cli.py status <project id>
    python scripts/editor_cli.py estimate <project id>     what voicing the script would cost (free)
    python scripts/editor_cli.py voice <project id>        orders the voice-over - PAID, from the Lumean subscription

`create` is free: it makes the project with its script, ready to be voiced. The
script is voiced as written, so it should already carry its directions to the
voice ([whispers], ...). The voice is the default one saved from a project's
Voice tab. Works with any Python 3 - it needs nothing but the standard library.
"""

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API = "http://localhost:8000/api"
UI = "http://localhost:5173"
REPO = Path(__file__).resolve().parents[2]


def call(method: str, path: str, body: dict | None = None, timeout: float = 120):
    request = urllib.request.Request(
        API + path,
        method=method,
        data=json.dumps(body).encode("utf-8") if body is not None else None,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        try:
            detail = json.load(exc).get("detail")
        except ValueError:
            detail = None
        sys.exit(f"error: {detail or exc}")
    except (urllib.error.URLError, OSError) as exc:
        sys.exit(f"error: the backend does not answer at {API} ({getattr(exc, 'reason', exc)}).\nStart it: cd \"{REPO}\" && ./start.sh")


def _read(path: str, what: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8-sig")
    except OSError as exc:
        sys.exit(f"error: cannot read the {what} file: {exc}")


def _clock(seconds: float | None) -> str:
    return "-" if seconds is None else f"{int(seconds // 60)}:{int(seconds % 60):02d}"


def _url(project_id: str, tab: str = "") -> str:
    return f"{UI}/editor/{project_id}" + (f"?tab={tab}" if tab else "")


def _real_universes() -> list[dict]:
    return [u for u in call("GET", "/universes") if u.get("id")]


def universes(args) -> None:
    for u in _real_universes():
        print(f"{u['id']}: {u['name']}")
        for show in u.get("available_shows") or []:
            print(f"    show: {show}")
        for comic in u.get("comics") or []:
            print(f"    comic: {comic}")


def list_projects(args) -> None:
    projects = call("GET", "/editor/projects")
    if not projects:
        print("no projects")
    for p in projects:
        print(f"{p['id']} | {p['title']} | {_clock(p.get('audio_duration'))} | {_stage(p)} | {p['created_at'][:10]}")


def _stage(p: dict) -> str:
    job = p.get("job") or {}
    if job.get("status") == "running":
        return f"running {job['stage']}" + (f" ({job['detail']})" if job.get("detail") else "")
    if job.get("status") == "error":
        return f"{job['stage']} failed: {job.get('error')}"
    if p.get("output_file"):
        return "rendered"
    if p.get("beats"):
        return f"{len(p['beats'])} beats"
    if p.get("word_count"):
        return "transcribed"
    if p.get("audio_file"):
        return "has narration"
    return "no narration yet"


def create(args) -> None:
    script = _read(args.script, "script").strip()
    if not script:
        sys.exit("error: the script file is empty")
    notes = _read(args.notes, "notes").strip() if args.notes else None

    known = _real_universes()
    if not known:
        sys.exit("error: the library has no universes")
    wanted = (args.universe or "").strip().lower()
    universe = next((u for u in known if wanted in (u["id"].lower(), u["name"].lower())), None) if wanted else known[0]
    if universe is None:
        sys.exit(f"error: no universe '{args.universe}'. There are: " + ", ".join(u["id"] for u in known))
    shows = args.show or []
    unknown = [s for s in shows if s not in (universe.get("available_shows") or [])]
    if unknown:
        sys.exit(
            f"error: not in the library for {universe['name']}: " + "; ".join(unknown)
            + ". Shows there: " + "; ".join(universe.get("available_shows") or [])
        )

    project = call(
        "POST",
        "/editor/projects",
        {
            "title": args.title,
            "script_text": script,
            "footage_notes": notes,
            "universe": universe["id"],
            "shows": shows,
            "layout": args.layout,
        },
    )
    voice = call("GET", f"/editor/projects/{project['id']}/voiceover")
    settings = voice["state"]["settings"]
    print(f"created project {project['id']}")
    print(f"  title:    {project['title']}")
    print(f"  universe: {universe['name']}; footage from {', '.join(shows) or 'all its shows'}")
    print(f"  script:   {len(script):,} characters" + (f"; footage notes: {len(notes):,} characters" if notes else ""))
    if settings.get("voice_id"):
        print(f"  voice:    {settings.get('voice_name') or settings['voice_id']} ({settings['model_id']}) - the default")
    else:
        print("  voice:    none yet - choose one on the Voice tab (and save it as the default there)")
    if not voice.get("has_key"):
        print("  the Lumean API key is not set (Settings in the UI) - the script can't be voiced until it is")
    print(f"  open:     {_url(project['id'], 'voice')}")
    print(f"next: `estimate {project['id']}` (free), then `voice {project['id']}` (paid) - or the Voice tab")


def status(args) -> None:
    p = call("GET", f"/editor/projects/{args.project}")
    print(f"{p['id']} | {p['title']}")
    print(f"  state:     {_stage(p)}")
    print(f"  narration: {p.get('audio_file') or 'none'} {_clock(p.get('audio_duration')) if p.get('audio_file') else ''}".rstrip())
    print(f"  script:    {len((p.get('script_text') or '').strip()):,} characters")
    voice = call("GET", f"/editor/projects/{args.project}/voiceover")
    state = voice["state"]
    if state.get("order_id"):
        fragments = state.get("fragments") or []
        done = sum(1 for f in fragments if f["status"] == "completed")
        bad = sum(1 for f in fragments if f["status"] in ("failed", "policy_flagged"))
        print(
            f"  voice-over: order {state.get('order_status')}, {done}/{len(fragments)} fragments voiced"
            + (f", {bad} failed or rejected" if bad else "")
            + (", joined audio ready" if state.get("final_file") else "")
            + (" (the script has changed since)" if voice.get("script_changed") else "")
        )
    else:
        name = state["settings"].get("voice_name") or state["settings"].get("voice_id") or "no voice chosen"
        print(f"  voice-over: not ordered ({name})")
    if p.get("queue"):
        print(f"  queued:    {', '.join(p['queue'])}")
    print(f"  open:      {_url(p['id'])}")


def estimate(args) -> None:
    e = call("GET", f"/editor/projects/{args.project}/voiceover/estimate", timeout=180)
    print(f"{e['characters']:,} characters" + (f" in {e['fragments']} fragments" if e.get("fragments") else ""))
    for key, value in (e.get("summary") or {}).items():
        if isinstance(value, (int, float, str)):
            print(f"  {key}: {value}")
    if e.get("blocked_fragments"):
        print(f"  fragments the content policy would reject: {e['blocked_fragments']}")
    if e.get("tokens_remaining") is not None:
        print(f"  tokens left in the subscription: {e['tokens_remaining']:,}")
    if not e.get("ready"):
        print("  the estimate was not ready in time - ask again")


def voice(args) -> None:
    p = call("POST", f"/editor/projects/{args.project}/voiceover/generate")
    print(f"voice-over ordered for {p['id']} - it is being voiced now (a few minutes)")
    print(f"  follow it: `status {p['id']}` or {_url(p['id'], 'voice')}")
    print("  when done it becomes the project's narration by itself if the project has none")


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Video editor projects from the command line (through the running backend).")
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("universes", help="universes with the shows and comics in the library").set_defaults(run=universes)
    commands.add_parser("list", help="the projects").set_defaults(run=list_projects)

    c = commands.add_parser("create", help="a new project from a script (free)")
    c.add_argument("--title", required=True)
    c.add_argument("--script", required=True, help="text file with the narration, as it is to be voiced")
    c.add_argument("--notes", help="text file with the author's notes on where the material comes from (read when shots are planned)")
    c.add_argument("--universe", help="id or name; the first universe when not given")
    c.add_argument("--show", action="append", help="take footage only from this show (repeatable); all the universe's shows when not given")
    c.add_argument("--layout", choices=["framed", "simple"], help="the saved default look's when not given")
    c.set_defaults(run=create)

    for name, run, text in (
        ("status", status, "where a project stands"),
        ("estimate", estimate, "what voicing its script would cost (free)"),
        ("voice", voice, "order the voice-over of its script (PAID)"),
    ):
        sub = commands.add_parser(name, help=text)
        sub.add_argument("project", help="project id")
        sub.set_defaults(run=run)

    args = parser.parse_args()
    args.run(args)


if __name__ == "__main__":
    main()
