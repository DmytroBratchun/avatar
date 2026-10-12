"""Standard sound effects - a page turning when a comic page comes on screen, a
camera shutter for a photo. Each is generated once through Lumean (a few variants,
so the same one is not heard every time) into a library shared by all projects,
<root>/library/sounds/, and reused from there.

At render time the effects are laid on the narration's clock, at the start of
every slot that shows a comic page or a photo, and mixed into the narration
before quotes and music are added (build_narration) - so a quote cut out of the
narration takes no effect with it, and the music's dips around quotes leave the
effects alone.
"""

import hashlib
import json
import subprocess
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from app.config import LIBRARY_DIR
from app.editor.store import Project
from app.services.lumean import Lumean, LumeanError

LIBRARY_FILE = "sounds.json"
VARIANTS = 3
# every variant is brought to this peak, then played sfx_volume dB below it
PEAK_DB = -1.0
POLL_SECONDS = 4
MAX_WAIT_SECONDS = 300


@dataclass(frozen=True)
class Sound:
    name: str
    label: str
    used_for: str  # the Candidate.kind whose slots start with this sound
    prompt: str
    seconds: float


STANDARD = [
    Sound(
        "page_turn", "Page turn", "comic",
        "A single crisp page turn of a paper comic book, close-up paper flip and rustle, quiet room, no music, no voice",
        1.5,
    ),
    Sound(
        "shutter", "Camera shutter", "photo",
        "A single soft vintage film camera shutter click, close-up, clean, quiet room, no music, no voice",
        1.0,
    ),
]
BY_NAME = {sound.name: sound for sound in STANDARD}


def library_dir() -> Path:
    path = LIBRARY_DIR / "sounds"
    path.mkdir(parents=True, exist_ok=True)
    return path


def load_library() -> dict[str, list[str]]:
    """Sound name -> its variants' file names in the library folder."""
    try:
        data = json.loads((library_dir() / LIBRARY_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    folder = library_dir()
    return {name: [f for f in files if (folder / f).is_file()] for name, files in data.items()}


def _save_library(library: dict[str, list[str]]) -> None:
    (library_dir() / LIBRARY_FILE).write_text(json.dumps(library, indent=1), encoding="utf-8")


def _peak(path: Path) -> float:
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(path), "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    for line in result.stderr.splitlines():
        if "max_volume:" in line:
            return float(line.split("max_volume:")[1].split()[0])
    return 0.0


def _prepare(raw: Path, target: Path) -> None:
    """Silence trimmed off the start (the sound should land on the cut), brought to
    PEAK_DB, written as WAV."""
    trimmed = target.with_name(target.stem + ".trim.wav")
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(raw),
         "-af", "silenceremove=start_periods=1:start_threshold=-45dB,aresample=48000,aformat=channel_layouts=stereo",
         str(trimmed)],
        check=True,
    )
    gain = PEAK_DB - _peak(trimmed)
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(trimmed), "-af", f"volume={gain:.2f}dB", str(target)],
        check=True,
    )
    trimmed.unlink(missing_ok=True)


def _one(client: Lumean, sound: Sound, folder: Path) -> str:
    order = client.create_sfx_order(sound.prompt, sound.seconds, prompt_influence=0.6)
    deadline = time.monotonic() + MAX_WAIT_SECONDS
    while order.get("status") in ("created", "pending", "in_progress"):
        if time.monotonic() > deadline:
            raise RuntimeError("Lumean is still generating the sound - try again later")
        time.sleep(POLL_SECONDS)
        order = client.order(order["id"])
    files = (order.get("result") or {}).get("files") or []
    if order.get("status") != "completed" or not files:
        raise RuntimeError(f"Lumean could not generate the sound ({order.get('status')})")
    raw = folder / f"{sound.name}_{uuid.uuid4().hex[:8]}.raw{Path(files[0]).suffix or '.mp3'}"
    client.download(files[0], raw)
    target = raw.with_name(raw.name.split(".raw")[0] + ".wav")
    try:
        _prepare(raw, target)
    finally:
        raw.unlink(missing_ok=True)
    return target.name


def generate(name: str) -> list[str]:
    """Generates the sound's variants (replacing any it had) and returns them."""
    sound = BY_NAME.get(name)
    if sound is None:
        raise KeyError(name)
    folder = library_dir()
    client = Lumean()
    with ThreadPoolExecutor(VARIANTS) as pool:
        futures = [pool.submit(_one, client, sound, folder) for _ in range(VARIANTS)]
        made, errors = [], []
        for future in futures:
            try:
                made.append(future.result())
            except (LumeanError, RuntimeError, subprocess.CalledProcessError) as exc:
                errors.append(str(exc))
    if not made:
        raise RuntimeError(errors[0] if errors else "No sound was generated")
    library = load_library()
    for old in library.get(name, []):
        (folder / old).unlink(missing_ok=True)
    library[name] = made
    _save_library(library)
    return made


def placements(project: Project) -> list[tuple[float, Path]]:
    """(time on the narration's clock, file) of every effect the video gets: a slot
    showing a comic page or a photo starts with that kind's sound. The variants take
    turns."""
    library = load_library()
    folder = library_dir()
    by_kind = {sound.used_for: [folder / f for f in library.get(sound.name, [])] for sound in STANDARD}
    turn: dict[str, int] = {}
    placed = []
    for beat in project.beats:
        for slot in beat.slots:
            if slot.selected is None:
                continue
            kind = slot.candidates[slot.selected].kind
            variants = by_kind.get(kind)
            if not variants:
                continue
            placed.append((slot.start, variants[turn.get(kind, 0) % len(variants)]))
            turn[kind] = turn.get(kind, 0) + 1
    return placed


def build_narration(project: Project, folder: Path) -> Path | None:
    """The narration with the effects mixed in, cached by what goes into it; None
    when there are no effects to add."""
    if not project.features.sound_effects or not project.audio_file:
        return None
    placed = placements(project)
    if not placed:
        return None
    narration = folder / project.audio_file
    recipe = json.dumps([str(narration), project.settings.sfx_volume, [(round(t, 3), p.name) for t, p in placed]])
    target = folder / "cache" / f"narration_sfx_{hashlib.sha1(recipe.encode()).hexdigest()[:12]}.wav"
    if target.exists():
        return target
    target.parent.mkdir(exist_ok=True)
    # Each effect file is opened once and split as many times as it plays, and the
    # graph goes in a file: a video of 140 comic pages would otherwise need a command
    # line past Windows' 32k-character limit.
    files = list(dict.fromkeys(path for _, path in placed))
    inputs = ["-i", str(narration)]
    for path in files:
        inputs += ["-i", str(path)]
    graph = ["[0:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo[n]"]
    uses: dict[Path, list[int]] = {path: [] for path in files}
    for i, (_, path) in enumerate(placed):
        uses[path].append(i)
    for f, path in enumerate(files, start=1):
        outs = "".join(f"[s{i}]" for i in uses[path])
        graph.append(f"[{f}:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,"
                     f"volume={-abs(project.settings.sfx_volume):.1f}dB,asplit={len(uses[path])}{outs}")
    for i, (at, _) in enumerate(placed):
        graph.append(f"[s{i}]adelay={int(round(at * 1000))}:all=1[e{i}]")
    graph.append("[n]" + "".join(f"[e{i}]" for i in range(len(placed)))
                 + f"amix=inputs={len(placed) + 1}:duration=first:normalize=0[a]")
    script = target.with_name("narration_sfx_graph.txt")
    script.write_text(";".join(graph), encoding="utf-8")
    partial = target.with_name(target.stem + ".part.wav")
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *inputs, "-filter_complex_script", str(script),
         "-map", "[a]", "-c:a", "pcm_s16le", str(partial)],
        check=True,
    )
    partial.replace(target)
    return target
