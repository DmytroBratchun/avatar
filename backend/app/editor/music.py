"""Music under the narration.

The video is divided into blocks of one mood - a run of beats each - and every block
gets its own music, composed through Lumean (app/services/lumean.py). A generated
track is at most 30 seconds, so a block is filled with several different tracks made
from the same description, one after another; no track is played twice.

Generated tracks fade out towards their end. Each one is therefore measured: only
the part before the fade is used, and the next track comes in over its last seconds
(OVERLAP), so the level never sags between two tracks. All tracks are brought to
the same loudness, a fixed amount below the narration's.

Blocks and their tracks live in project.json (Project.music), the audio files in
<project>/music/. The mixed track is put together at render time (build_track) and
laid under the narration only - a quote plays with the show's own sound.

Every composed track is also filed in the music library (music_library.py), and
a block looks there before it orders anything.
"""

import hashlib
import json
import logging
import math
import re
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from app.editor import music_library, store
from app.editor.llm import run_claude_json
from app.editor.store import MusicBlock, MusicTrack, Project, project_shows, project_universe
from app.services.lumean import Lumean, LumeanError

logger = logging.getLogger(__name__)

FOLDER = "music"
TRACK_MS = 30_000  # the longest track Lumean makes
VARIANTS_PER_ORDER = 4  # the most one order can hold
OVERLAP = 3.0  # seconds two neighbouring tracks play together
MIN_BLOCK = 35.0  # a mood shorter than this is not worth its own music
# how much of a track is expected to be usable, for deciding how many to order
EXPECTED_USABLE = 22.0
PARALLEL_ORDERS = 3
ATTEMPTS_PER_BLOCK = 4
POLL_SECONDS = 5
MAX_WAIT_SECONDS = 60 * 60
# measuring where a track's fade-out begins
WINDOW = 0.25  # seconds per loudness reading
FADE_DROP_DB = 6.0  # the track counts as fading once it stays this far below its usual level
SILENCE_DROP_DB = 25.0  # ...and as not yet started while this far below
MIN_KEPT = 0.4  # never keep less than this share of a track

BLOCKS_SCHEMA = {
    "type": "object",
    "properties": {
        "blocks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "first_beat": {"type": "integer"},
                    "last_beat": {"type": "integer"},
                    "mood": {"type": "string", "description": "The mood of this stretch in a few words."},
                    "prompt": {"type": "string", "description": "What to ask the music generator for, in English."},
                },
                "required": ["first_beat", "last_beat", "mood", "prompt"],
            },
        }
    },
    "required": ["blocks"],
}


# --- planning ----------------------------------------------------------------


def refit_blocks(project: Project) -> bool:
    """Puts blocks planned for earlier beats onto the current ones, keeping their
    music: each block ends on the beat boundary nearest where it ended, the next
    starts right after. Building the beats again (often to fix a transcript)
    otherwise left the video silently without music. Returns whether the blocks fit
    now; a block that comes out longer may lack a little music until the stage runs."""
    if not project.music or not project.beats:
        return False
    if blocks_fit(project):
        return True
    beats = project.beats
    first = 0
    for n, block in enumerate(project.music):
        last_block = n == len(project.music) - 1
        if first >= len(beats):
            return False  # more blocks than beats left: plan again
        if last_block:
            last = len(beats) - 1
        else:
            # leave at least one beat for each block still to come
            room = range(first, len(beats) - (len(project.music) - n - 1))
            last = min(room, key=lambda i: abs(beats[i].end - block.end))
        block.beat_first, block.beat_last = beats[first].id, beats[last].id
        block.start, block.end = beats[first].start, beats[last].end
        first = last + 1
    return blocks_fit(project)


def blocks_fit(project: Project) -> bool:
    """Whether the stored blocks still describe the project's beats - beats built
    again, or a new narration, leave them pointing at times that no longer exist."""
    if not project.music or not project.beats:
        return False
    beats = {beat.id: beat for beat in project.beats}
    for block in project.music:
        first, last = beats.get(block.beat_first), beats.get(block.beat_last)
        if first is None or last is None or abs(first.start - block.start) > 0.01 or abs(last.end - block.end) > 0.01:
            return False
    return abs(project.music[0].start - project.beats[0].start) < 0.01 and abs(project.music[-1].end - project.beats[-1].end) < 0.01


def _clock(seconds: float) -> str:
    return f"{int(seconds // 60)}:{int(seconds % 60):02d}"


def plan_blocks(project: Project, cwd: Path) -> tuple[list[MusicBlock], float]:
    """One Claude call: where the mood of the narration changes, and what music each
    stretch should have."""
    universe = project_universe(project)
    shows = ", ".join(project_shows(project)) or universe.name
    lines = [
        f"[{i}] ({_clock(beat.start)}-{_clock(beat.end)}) {beat.summary or beat.text}"
        for i, beat in enumerate(project.beats)
    ]
    prompt = (
        f"Below is the narration of a video essay about {shows}, cut into numbered beats with their times. "
        "Quiet instrumental music will play under the narrator's voice for the whole video. Divide the beats "
        "into consecutive blocks, each with one mood, and say what music each block should have.\n\n"
        f"A block is a run of neighbouring beats and lasts at least {MIN_BLOCK:.0f} seconds, usually one to "
        "three minutes; start a new block only where the mood of what is being said really changes - a turn "
        "from wonder to loss, from calm explanation to conflict, from tension to resolution. Every beat "
        "belongs to exactly one block, in order, with none left out.\n\n"
        "For each block write the prompt for a music generator, in English, one or two sentences. The music "
        f"must sound as if it belongs to the world of {universe.name}"
        + (f" ({universe.description})" if universe.description else "")
        + ": name the instruments, the musical tradition and the character of that world's own music"
        + (f" - {universe.music_style}" if universe.music_style else "")
        + ". Do not use the names of the shows, their characters, places or composers - the generator does "
        "not know them and may refuse them; describe the sound instead. Then give the mood, the tempo and "
        "how full the arrangement is. It is background for a speaking voice: instrumental only, no vocals, "
        "no sudden hits, no dominant lead melody, an even level from the first second to the last. Blocks "
        "next to each other must be clearly different in mood but related in sound, since one fades into "
        "the next.\n\nBeats:\n" + "\n".join(lines)
    )
    result, cost = run_claude_json(prompt, BLOCKS_SCHEMA, cwd=cwd, timeout=600, task="music")

    # make the answer tile the beats: in order, no gaps, no overlaps, to the last beat
    entries = sorted(result["blocks"], key=lambda e: int(e["first_beat"]))
    runs: list[tuple[int, int, dict]] = []
    cursor = 0
    for entry in entries:
        last = min(int(entry["last_beat"]), len(project.beats) - 1)
        if last < cursor:
            continue
        runs.append((cursor, last, entry))
        cursor = last + 1
    if not runs:
        raise RuntimeError("Could not divide the narration into blocks of music")
    runs[-1] = (runs[-1][0], len(project.beats) - 1, runs[-1][2])

    # a block that came out too short joins the one before it (the first: the next)
    merged: list[tuple[int, int, dict]] = []
    for first, last, entry in runs:
        length = project.beats[last].end - project.beats[first].start
        if merged and length < MIN_BLOCK:
            merged[-1] = (merged[-1][0], last, merged[-1][2])
        else:
            merged.append((first, last, entry))
    if len(merged) > 1 and project.beats[merged[0][1]].end - project.beats[merged[0][0]].start < MIN_BLOCK:
        merged[1] = (merged[0][0], merged[1][1], merged[1][2])
        merged.pop(0)

    blocks = [
        MusicBlock(
            id=f"music_{n + 1:02d}",
            beat_first=project.beats[first].id,
            beat_last=project.beats[last].id,
            start=project.beats[first].start,
            end=project.beats[last].end,
            mood=str(entry["mood"]).strip(),
            prompt=str(entry["prompt"]).strip(),
        )
        for n, (first, last, entry) in enumerate(merged)
    ]
    return blocks, cost or 0.0


# --- measuring a track ---------------------------------------------------------


def _levels(path: Path) -> np.ndarray:
    """Loudness of the file in dB, one reading per WINDOW seconds."""
    rate = 8000
    raw = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", str(rate),
         "-f", "s16le", "-"],
        check=True, capture_output=True,
    ).stdout
    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    size = int(rate * WINDOW)
    count = len(samples) // size
    if count == 0:
        return np.array([])
    rms = np.sqrt(np.mean(samples[: count * size].reshape(count, size) ** 2, axis=1))
    return 20 * np.log10(np.maximum(rms, 1e-6))


def usable_range(path: Path) -> tuple[float, float, float]:
    """(start, end, duration) of the part of a generated track worth playing: from
    where it has got going to where its closing fade-out begins."""
    levels = _levels(path)
    duration = len(levels) * WINDOW
    if len(levels) < 8:
        return 0.0, duration, duration
    # the track's usual level: the middle of it, where neither the lead-in nor the fade is
    body = float(np.median(levels[len(levels) // 4 : len(levels) * 3 // 4]))
    started = np.nonzero(levels >= body - SILENCE_DROP_DB)[0]
    start = float(started[0]) * WINDOW if len(started) else 0.0
    # the last moment the music is still at full level; everything after it is the fade
    full = np.nonzero(levels >= body - FADE_DROP_DB)[0]
    end = (float(full[-1]) + 1) * WINDOW if len(full) else duration
    end = max(end, start + (duration - start) * MIN_KEPT)
    return round(start, 2), round(min(end, duration), 2), round(duration, 2)


def loudness(path: Path, start: float = 0.0, length: float | None = None) -> float | None:
    """Integrated loudness (LUFS) of the file, or of a part of it."""
    command = ["ffmpeg", "-hide_banner", "-nostats", "-ss", f"{start:.3f}"]
    if length is not None:
        command += ["-t", f"{length:.3f}"]
    command += ["-i", str(path), "-vn", "-af", "loudnorm=print_format=json", "-f", "null", "-"]
    output = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace").stderr
    found = re.search(r'"input_i"\s*:\s*"(-?[\d.]+|-inf)"', output)
    if not found or found.group(1) == "-inf":
        return None
    return float(found.group(1))


def narration_loudness(project: Project, folder: Path) -> float:
    """Loudness of the project's narration, measured once per audio file."""
    cache_path = folder / "cache" / "loudness.json"
    try:
        cache = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cache = {}
    audio = folder / (project.audio_file or "")
    key = f"{audio.name}:{audio.stat().st_size}"
    if key not in cache:
        measured = loudness(audio)
        cache = {key: measured if measured is not None else -20.0}
        cache_path.parent.mkdir(exist_ok=True)
        cache_path.write_text(json.dumps(cache), encoding="utf-8")
    return cache[key]


# --- placing tracks on the narration -----------------------------------------


@dataclass
class Placed:
    track: MusicTrack
    at: float  # where it starts on the narration clock
    length: float  # how long it plays, from track.start
    fade_in: float
    fade_out: float


def _playable(block: MusicBlock) -> list[MusicTrack]:
    return [t for t in block.tracks if t.enabled and t.end - t.start > OVERLAP * 2]


def covered(block: MusicBlock) -> float:
    """How many seconds the block's tracks fill when laid end to end with overlaps."""
    tracks = _playable(block)
    return sum(t.end - t.start for t in tracks) - OVERLAP * max(0, len(tracks) - 1)


def needed(block: MusicBlock) -> float:
    """How many seconds a block needs: its own length, plus the overlap into its
    neighbours."""
    return block.end - block.start + OVERLAP


def layout(project: Project) -> list[Placed]:
    """Every track's place under the narration. Within a block the tracks follow one
    another, each coming in over the previous one's last seconds; at a block's end
    its last track fades out as the next block's first one fades in. A block whose
    tracks run out early leaves the rest of it without music."""
    placed: list[Placed] = []
    for n, block in enumerate(project.music):
        first_block, last_block = n == 0, n == len(project.music) - 1
        cursor = 0.0 if first_block else block.start - OVERLAP / 2
        finish = block.end if last_block else block.end + OVERLAP / 2
        for track in _playable(block):
            if finish - cursor <= OVERLAP:
                break
            length = min(track.end - track.start, finish - cursor)
            placed.append(Placed(track=track, at=cursor, length=length, fade_in=OVERLAP, fade_out=OVERLAP))
            cursor += length - OVERLAP
    if placed:
        placed[0].fade_in = 1.5  # the video opens on music coming in, not on a slow swell
    return placed


def build_track(project: Project, folder: Path) -> Path | None:
    """The whole music track on the narration's clock, as one file: every track cut
    to its usable part, brought to the same loudness below the voice, cross-faded
    into the next. Rebuilt only when something about it changed."""
    if not blocks_fit(project) or not project.audio_file:
        return None
    out = folder / FOLDER
    placed = [p for p in layout(project) if (out / p.track.file).is_file()]
    if not placed:
        return None
    target = narration_loudness(project, folder) - project.settings.music_level
    recipe = [[p.track.file, p.track.start, round(p.at, 3), round(p.length, 3), p.fade_in, p.track.loudness] for p in placed]
    name = hashlib.sha1(json.dumps([recipe, round(target, 2)]).encode()).hexdigest()[:12]
    track = out / f"mix_{name}.flac"
    if track.exists():
        return track

    command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
    graph = []
    for i, p in enumerate(placed):
        command += ["-ss", f"{p.track.start:.3f}", "-t", f"{p.length:.3f}", "-i", str(out / p.track.file)]
        gain = target - (p.track.loudness if p.track.loudness is not None else target)
        delay = int(round(p.at * 1000))
        # equal-power fades: two tracks crossing keep the level steady
        graph.append(
            f"[{i}:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,volume={gain:.2f}dB,"
            f"afade=t=in:d={p.fade_in}:curve=qsin,afade=t=out:st={max(0.0, p.length - p.fade_out):.3f}:d={p.fade_out}:curve=qsin,"
            f"adelay={delay}:all=1[t{i}]"
        )
    graph.append("".join(f"[t{i}]" for i in range(len(placed))) + f"amix=inputs={len(placed)}:duration=longest:normalize=0[a]")
    script = folder / "cache" / "music_graph.txt"
    script.parent.mkdir(exist_ok=True)
    script.write_text(";".join(graph), encoding="utf-8")
    partial = track.with_suffix(".part.flac")
    done = subprocess.run(
        [*command, "-filter_complex_script", str(script), "-map", "[a]", "-c:a", "flac", str(partial)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if done.returncode != 0:
        raise RuntimeError(f"Could not mix the music: {done.stderr.strip()[-400:]}")
    partial.replace(track)
    for stale in set(out.glob("mix_*.flac")) - {track}:
        try:
            stale.unlink()
        except OSError:
            pass
    return track


# --- composing -----------------------------------------------------------------


def _save(project_id: str, blocks: list[MusicBlock], cost: float = 0.0) -> None:
    def apply(p: Project) -> None:
        p.music = blocks
        p.llm_cost_usd = round(p.llm_cost_usd + cost, 4)

    store.update_project(project_id, apply)


def _from_library(project: Project, blocks: list[MusicBlock], out: Path, only: bool) -> int:
    """Fills blocks from the music library: tracks made from a prompt almost the same
    as the block's, or - with the library as the only source - the closest there
    are. A track plays once in a video. Returns how many were taken."""
    if music_library.root() is None:
        return 0
    at_least = music_library.LIBRARY_ONLY_SIMILARITY if only else music_library.REUSE_SIMILARITY
    in_video = {music_library.library_id(t, out) for b in blocks for t in b.tracks}
    taken = 0
    for block in blocks:
        if covered(block) >= needed(block):
            continue
        for _, entry in music_library.similar(block.prompt, at_least):
            if covered(block) >= needed(block):
                break
            if entry.id in in_video or entry.id in block.avoid:
                continue
            block.tracks.append(music_library.take(project, block, entry, out))
            in_video.add(entry.id)
            taken += 1
    return taken


def _collect(client: Lumean, order: dict, block: MusicBlock, out: Path, project: Project) -> int:
    """Downloads an order's tracks into the block, measures them and files them in
    the music library. Returns how many."""
    files = (order.get("result") or {}).get("files") or []
    added = 0
    for path in files:
        # the start of an order's id is a time: orders placed together share it, so the end goes in too
        name = f"{block.id}_{order['id'][:8]}{order['id'][-6:]}_{added + 1}{Path(path).suffix or '.mp3'}"
        try:
            client.download(path, out / name)
        except LumeanError:
            continue
        start, end, duration = usable_range(out / name)
        track = MusicTrack(
            id=f"track_{uuid.uuid4().hex[:8]}", file=name, order_id=order["id"], duration=duration,
            start=start, end=end, loudness=loudness(out / name, start, end - start),
        )
        track.library_id = music_library.add(project, block, track, out / name) or ""
        block.tracks.append(track)
        added += 1
    return added


def compose(project_id: str, report) -> None:
    """The stage. Plans the blocks when there are none that fit the beats, then
    orders tracks for every block that is not filled yet, a few orders at a time,
    until each has enough to play without repeating one. Running it again only
    fills what is missing."""
    project = store.load_project(project_id)
    if not project.beats:
        raise RuntimeError("Build beats first")
    folder = store.project_dir(project_id)
    out = folder / FOLDER
    out.mkdir(exist_ok=True)

    if refit_blocks(project):
        _save(project_id, project.music)
    if not blocks_fit(project):
        report("dividing the narration into moods")
        blocks, cost = plan_blocks(project, folder)
        _save(project_id, blocks, cost)
    else:
        blocks = project.music

    library_only = project.settings.music_library_only
    report("looking in the music library")
    if _from_library(project, blocks, out, library_only):
        _save(project_id, blocks)
    if library_only:
        music_library.forget_project(project_id, {music_library.library_id(t, out) for b in blocks for t in b.tracks})
        short = [b for b in blocks if covered(b) < needed(b)]
        if short:
            names = ", ".join(f"{b.id[-2:]} ({b.mood})" for b in short)
            raise RuntimeError(
                f"The music library has not enough for block {names} - untick 'only from the library' to compose the rest"
            )
        return

    # If Lumean fails - an error, nothing back, out of time - the closest tracks of
    # the library fill what it could not, and the stage fails only when those run out.
    failure: str | None = None
    try:
        client = Lumean()
        in_flight: dict[str, str] = {}  # block id -> order id
        attempts: dict[str, int] = {}
        deadline = time.monotonic() + MAX_WAIT_SECONDS
        while True:
            lacking = [b for b in blocks if covered(b) < needed(b)]
            for block in lacking:
                if len(in_flight) >= PARALLEL_ORDERS:
                    break
                if block.id in in_flight or attempts.get(block.id, 0) >= ATTEMPTS_PER_BLOCK:
                    continue
                missing = needed(block) - covered(block)
                variants = min(VARIANTS_PER_ORDER, max(1, math.ceil(missing / (EXPECTED_USABLE - OVERLAP))))
                try:
                    order = client.create_music_order(block.prompt, variants, TRACK_MS)
                except LumeanError as exc:
                    if exc.reason == "concurrency_limit_exceeded" and in_flight:
                        break  # the account is busy with our own orders: wait for one
                    raise RuntimeError(f"Music for block {block.id[-2:]} ({block.mood}): {exc}") from exc
                in_flight[block.id] = order["id"]
                attempts[block.id] = attempts.get(block.id, 0) + 1

            filled = sum(1 for b in blocks if covered(b) >= needed(b))
            report(f"composing music {filled}/{len(blocks)}")
            if not in_flight:
                break
            if time.monotonic() > deadline:
                raise RuntimeError("Lumean is still composing - run the music stage again later")
            time.sleep(POLL_SECONDS)
            for block_id, order_id in list(in_flight.items()):
                order = client.order(order_id)
                if order.get("status") in ("created", "pending", "in_progress"):
                    continue
                del in_flight[block_id]
                block = next(b for b in blocks if b.id == block_id)
                if _collect(client, order, block, out, project):
                    _save(project_id, blocks)
    except Exception as exc:
        logger.warning("Composing music for project %s failed: %s", project_id, exc)
        failure = str(exc) or exc.__class__.__name__
    if any(covered(b) < needed(b) for b in blocks):
        report("taking the closest tracks from the music library")
        if _from_library(project, blocks, out, only=True):
            _save(project_id, blocks)

    unfilled = [b for b in blocks if covered(b) < needed(b)]
    music_library.forget_project(project_id, {music_library.library_id(t, out) for b in blocks for t in b.tracks})
    # files no block uses any more: tracks of earlier plans
    kept = {t.file for b in blocks for t in b.tracks}
    for stale in out.iterdir():
        if stale.name not in kept and not stale.name.startswith("mix_"):
            try:
                stale.unlink()
            except OSError:
                pass
    if unfilled:
        names = ", ".join(f"{b.id[-2:]} ({b.mood})" for b in unfilled)
        reason = f"{failure}. " if failure else ""
        raise RuntimeError(
            f"{reason}Could not get enough music for block {names}, and the music library has nothing close enough "
            "- reword its prompt or run the stage again"
        )
