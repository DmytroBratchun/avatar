"""Renders a project to video with ffmpeg.

Every slot becomes its own short video-only file in <project>/segments/, named by
a hash of everything that affects its pixels - so changing one clip, or a frame
setting, only re-encodes what actually changed. The segments are then joined
without re-encoding and the narration is muxed in.
"""

import hashlib
import json
import re
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

from app.config import DATA_DIR
from app.editor import bgtrack, store
from app.editor.store import Candidate, Project, Quote, RenderSettings

WIDTH, HEIGHT = 1920, 1080
FPS_NUM, FPS_DEN = 24000, 1001
FPS = FPS_NUM / FPS_DEN
WIDE_ASPECT = 1.6  # picture aspect ratio from which a source counts as widescreen
ENCODE_VERSION = 3  # bump to invalidate every cached segment after changing the filter graph
PARALLEL_ENCODES = 3

FRAMES_DIR_NAME = "_frames"
BORDER_FRAME = "border"
NO_FRAME = "none"

CROP_CACHE_PATH = DATA_DIR / "editor_crop_cache.json"
_crop_lock = threading.Lock()


def _run(args: list[str]) -> subprocess.CompletedProcess:
    result = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {result.stderr.strip()[-600:]}")
    return result


# --- baked-in black bars ----------------------------------------------------


def _video_size(path: str) -> tuple[int, int]:
    result = _run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "csv=p=0", path]
    )
    width, height = result.stdout.strip().split(",")[:2]
    return int(width), int(height)


def source_crop(path: str) -> tuple[int, int, int, int]:
    """(w, h, x, y) of the actual picture inside the file. Some episodes are 4:3
    stored in a 16:9 file with black bars at the sides; without this the bars would
    be scaled into the frame as part of the clip. Measured once per file."""
    stat = Path(path).stat()
    key = f"{path}|{stat.st_size}|{int(stat.st_mtime)}"
    with _crop_lock:
        cache = json.loads(CROP_CACHE_PATH.read_text(encoding="utf-8")) if CROP_CACHE_PATH.exists() else {}
    if key in cache:
        return tuple(cache[key])

    width, height = _video_size(path)
    best = None
    # a dark scene reads as "all bars", so sample a few places and keep the largest picture found
    for timestamp in (120, 420, 780):
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-ss", str(timestamp), "-i", path, "-vf", "cropdetect=24:2:0",
             "-frames:v", "24", "-an", "-f", "null", "-"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        found = re.findall(r"crop=(\d+):(\d+):(\d+):(\d+)", result.stderr)
        if found:
            w, h, x, y = (int(v) for v in found[-1])
            if best is None or w * h > best[0] * best[1]:
                best = (w, h, x, y)
    if best is None or best[0] < width * 0.5 or best[1] < height * 0.5:
        best = (width, height, 0, 0)

    with _crop_lock:
        cache = json.loads(CROP_CACHE_PATH.read_text(encoding="utf-8")) if CROP_CACHE_PATH.exists() else {}
        cache[key] = best
        CROP_CACHE_PATH.write_text(json.dumps(cache, indent=2), encoding="utf-8")
    return best


# --- frame overlay ----------------------------------------------------------


def frames_dir() -> Path:
    path = store.root_dir() / FRAMES_DIR_NAME
    path.mkdir(exist_ok=True)
    return path


def list_frames() -> list[str]:
    return sorted(p.stem for p in frames_dir().glob("*.png"))


def _detect_window(alpha: Image.Image) -> tuple[int, int, int, int]:
    """Finds the opening of a frame image: walks outwards from the centre along many
    rows and columns until it hits the artwork. Corner art and emblems reach into the
    opening in places, so each edge takes the reading that most lines agree is far
    out, not the nearest obstruction."""
    width, height = alpha.size
    pixels = alpha.load()
    cx, cy = width // 2, height // 2

    def walk(x: int, y: int, dx: int, dy: int) -> int:
        while 0 < x < width - 1 and 0 < y < height - 1 and pixels[x, y] < 64:
            x, y = x + dx, y + dy
        return x if dx else y

    rows = [int(height * (0.25 + 0.5 * i / 40)) for i in range(41)]
    cols = [int(width * (0.25 + 0.5 * i / 40)) for i in range(41)]
    left = sorted(walk(cx, y, -1, 0) for y in rows)[8]
    right = sorted(walk(cx, y, 1, 0) for y in rows)[-9]
    top = sorted(walk(x, cy, 0, -1) for x in cols)[8]
    bottom = sorted(walk(x, cy, 0, 1) for x in cols)[-9]
    return left, top, right, bottom


def _even(value: float) -> int:
    return int(round(value / 2)) * 2


def prepare_frame(settings: RenderSettings, cache_dir: Path) -> tuple[Path | None, tuple[int, int, int, int]]:
    """Returns (overlay PNG at output size or None, window (x, y, w, h) the clip
    must fill). The clip goes under the overlay, so artwork may overlap its edges."""
    cache_dir.mkdir(exist_ok=True)
    frame_scale = min(1.5, max(0.4, settings.frame_scale))

    if settings.frame not in (BORDER_FRAME, NO_FRAME):
        source = frames_dir() / f"{settings.frame}.png"
        if not source.is_file():
            raise RuntimeError(f"Frame image not found: {source}")
        stat = source.stat()
        tag = hashlib.sha1(
            f"{source}|{stat.st_size}|{int(stat.st_mtime)}|{ENCODE_VERSION}|{frame_scale:.3f}".encode()
        ).hexdigest()[:12]
        out, meta = cache_dir / f"frame_{tag}.png", cache_dir / f"frame_{tag}.json"
        if out.exists() and meta.exists():
            return out, tuple(json.loads(meta.read_text(encoding="utf-8")))

        image = Image.open(source).convert("RGBA")
        left, top, right, bottom = _detect_window(image.getchannel("A"))
        # fitted to the screen, then resized by the user's frame size; larger than the
        # screen is allowed - the artwork that falls outside is simply cut off
        scale = min(HEIGHT / image.height, WIDTH / image.width) * frame_scale
        scaled = image.resize((round(image.width * scale), round(image.height * scale)), Image.LANCZOS)
        # near-invisible haze left over from cutting the artwork out of its background
        alpha = scaled.getchannel("A").point(lambda a: 0 if a < 8 else a)
        scaled.putalpha(alpha)
        offset_x, offset_y = (WIDTH - scaled.width) // 2, (HEIGHT - scaled.height) // 2
        canvas = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
        canvas.paste(scaled, (offset_x, offset_y))
        canvas.save(out)

        window = (
            offset_x + int(left * scale),
            offset_y + int(top * scale),
            _even((right - left) * scale),
            _even((bottom - top) * scale),
        )
        meta.write_text(json.dumps(window), encoding="utf-8")
        return out, window

    # plain 4:3 window, centred
    height = min(HEIGHT, _even(HEIGHT * settings.clip_height * frame_scale))
    width = _even(height * 4 / 3)
    window = ((WIDTH - width) // 2, (HEIGHT - height) // 2, width, height)
    if settings.frame == NO_FRAME:
        return None, window

    out = cache_dir / f"frame_border_{width}x{height}_{ENCODE_VERSION}.png"
    if not out.exists():
        x, y = window[0], window[1]
        shadow = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
        ImageDraw.Draw(shadow).rectangle([x - 6, y - 6, x + width + 6, y + height + 6], outline=(0, 0, 0, 200), width=18)
        canvas = shadow.filter(ImageFilter.GaussianBlur(12))
        ImageDraw.Draw(canvas).rectangle([x - 4, y - 4, x + width + 3, y + height + 3], outline=(235, 230, 215, 255), width=4)
        canvas.save(out)
    return out, window


def scaled_window(settings: RenderSettings, window: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    """The clip's rectangle after the user's size adjustment: grown or shrunk about
    its centre, kept on screen. Frame openings are detected automatically and are
    rarely pixel-perfect, so this lets the clip be tucked under the artwork by eye."""
    x, y, w, h = window
    scale = min(settings.clip_scale, WIDTH / w, HEIGHT / h)
    new_w, new_h = _even(w * scale), _even(h * scale)
    new_x = min(max(0, x + (w - new_w) // 2), WIDTH - new_w)
    new_y = min(max(0, y + (h - new_h) // 2), HEIGHT - new_h)
    return new_x, new_y, new_w, new_h


def corner_radii(settings: RenderSettings) -> tuple[int, int, int, int]:
    """(top-left, top-right, bottom-right, bottom-left) rounding of the clip."""
    if settings.corner_radii and len(settings.corner_radii) == 4:
        return tuple(max(0, int(r)) for r in settings.corner_radii)
    return (settings.corner_radius,) * 4


def prepare_mask(width: int, height: int, radii: tuple[int, int, int, int], cache_dir: Path) -> Path | None:
    """Greyscale mask the size of the clip window (white = keep) with each corner
    rounded by its own radius."""
    limit = min(width, height) // 2
    radii = tuple(min(r, limit) for r in radii)
    if not any(radii):
        return None
    cache_dir.mkdir(exist_ok=True)
    out = cache_dir / f"mask_{width}x{height}_r{'-'.join(map(str, radii))}.png"
    if not out.exists():
        # drawn at 4x and scaled down, for smooth corner edges
        scale = 4
        w, h = width * scale, height * scale
        big = Image.new("L", (w, h), 255)
        draw = ImageDraw.Draw(big)
        # for each corner: blank the corner square, then put back the quarter disc
        corners = [(0, 0), (w, 0), (w, h), (0, h)]
        for (cx, cy), radius in zip(corners, radii):
            r = radius * scale
            if r <= 0:
                continue
            x0, x1 = (0, r) if cx == 0 else (w - r, w)
            y0, y1 = (0, r) if cy == 0 else (h - r, h)
            draw.rectangle([x0, y0, x1 - 1, y1 - 1], fill=0)
            centre_x = r if cx == 0 else w - r
            centre_y = r if cy == 0 else h - r
            draw.ellipse([centre_x - r, centre_y - r, centre_x + r - 1, centre_y + r - 1], fill=255)
        big.resize((width, height), Image.LANCZOS).save(out)
    return out


# --- segments ---------------------------------------------------------------


def _frame_count(start: float, end: float) -> int:
    # from absolute positions, not the duration, so rounding never accumulates into audio drift
    return max(1, round(end * FPS) - round(start * FPS))


def _segment_args(
    candidate: Candidate | None,
    frames: int,
    settings: RenderSettings,
    overlay: Path | None,
    window: tuple[int, int, int, int],
    track: Path | None = None,
    track_frame: int = 0,
    track_identity: list | None = None,
    mask: Path | None = None,
) -> tuple[list[str], dict]:
    """ffmpeg arguments (without the output path) and the cache identity for one slot."""
    encode = [
        "-frames:v", str(frames), "-an",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
        "-r", f"{FPS_NUM}/{FPS_DEN}", "-video_track_timescale", str(FPS_NUM),
    ]
    if candidate is None:
        args = ["-f", "lavfi", "-i", f"color=black:s={WIDTH}x{HEIGHT}:r={FPS_NUM}/{FPS_DEN}", *encode]
        return args, {"blank": True, "frames": frames, "v": ENCODE_VERSION}

    cw, ch, cx, cy = source_crop(candidate.file_path)
    # Timestamps are reset to zero on every stream: after a seek the first frame of an
    # input carries a small offset, and overlay would show the first output frame
    # without whichever layer starts a few milliseconds late.
    base = f"fps={FPS_NUM}/{FPS_DEN},setpts=PTS-STARTPTS,crop={cw}:{ch}:{cx}:{cy},setsar=1"
    seek = ["-ss", f"{candidate.clip_start:.3f}", "-i", candidate.file_path]
    identity = {
        "file": candidate.file_path, "start": candidate.clip_start, "frames": frames,
        "crop": [cw, ch, cx, cy], "layout": settings.layout, "v": ENCODE_VERSION,
    }

    # A widescreen source (Korra) already fills a 16:9 screen and is sharp enough to,
    # so in a framed video it is shown full screen rather than cropped into the 4:3
    # window - the frame is for the old 4:3 footage.
    full_screen = settings.layout == "simple" or (settings.wide_full_screen and cw / ch >= WIDE_ASPECT)
    if full_screen:
        identity["layout"] = "simple"
        graph = (
            f"[0:v]{base},scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=decrease,"
            f"pad={WIDTH}:{HEIGHT}:(ow-iw)/2:(oh-ih)/2:black[out]"
        )
        return [*seek, "-filter_complex", graph, "-map", "[out]", *encode], identity

    x, y, w, h = window
    keep = 1.0 - settings.dim
    # blur at quarter resolution: far faster than blurring 1080p and looks the same once scaled back up
    sigma = max(0.1, settings.blur / 4)
    blur_chain = (
        f"scale={WIDTH // 4}:{HEIGHT // 4}:force_original_aspect_ratio=increase,crop={WIDTH // 4}:{HEIGHT // 4},"
        f"gblur=sigma={sigma:.2f},scale={WIDTH}:{HEIGHT},"
        f"colorchannelmixer=rr={keep:.3f}:gg={keep:.3f}:bb={keep:.3f}[bg]"
    )
    fg_chain = f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}[fg]"
    inputs = [*seek]

    if track is None:
        # no scenery chosen: fall back to a blurred copy of the clip itself
        graph = f"[0:v]{base},split[a][b];[a]{blur_chain};[b]{fg_chain}"
    else:
        # The background track is one continuous video on the same clock as the output;
        # this clip shows the stretch of it that starts at its own position. Seeking a
        # little before the frame's timestamp lands on exactly that frame.
        seek_to = max(0.0, (track_frame - 0.4) / FPS)
        inputs += ["-ss", f"{seek_to:.5f}", "-i", str(track)]
        graph = (
            f"[0:v]{base},{fg_chain};"
            f"[1:v]fps={FPS_NUM}/{FPS_DEN},setpts=PTS-STARTPTS,tpad=stop_mode=clone:stop=-1,gblur=sigma={sigma:.2f},"
            f"scale={WIDTH}:{HEIGHT},colorchannelmixer=rr={keep:.3f}:gg={keep:.3f}:bb={keep:.3f}[bg]"
        )
        identity["background"] = track_identity

    next_input = 1 if track is None else 2
    if mask is not None:
        # round the clip's corners so they don't poke out from under the frame artwork
        inputs += ["-loop", "1", "-i", str(mask)]
        graph += f";[fg]format=rgba[fgc];[{next_input}:v]format=gray[m];[fgc][m]alphamerge[fgr];[bg][fgr]overlay={x}:{y}[c]"
        next_input += 1
    else:
        graph += f";[bg][fg]overlay={x}:{y}[c]"
    if overlay is not None:
        inputs += ["-loop", "1", "-i", str(overlay)]
        graph += f";[c][{next_input}:v]overlay=0:0[out]"
    else:
        graph += ";[c]null[out]"
    identity.update(
        {
            "window": window, "blur": settings.blur, "dim": settings.dim,
            "frame": overlay.name if overlay else None, "mask": mask.name if mask else None,
        }
    )
    return [*inputs, "-filter_complex", graph, "-map", "[out]", *encode], identity


def render_preview(project: Project, slot_index: int) -> Path:
    """One still of the finished look - clip, background, frame, rounding - for a
    single clip, using the project's current settings. Takes a second or two, so the
    look can be adjusted without rendering the whole video."""
    slots = [(beat, slot) for beat in project.beats for slot in beat.slots if slot.selected is not None]
    if not slots:
        raise RuntimeError("Find footage first - there is nothing to preview yet")
    beat, slot = slots[slot_index % len(slots)]
    chosen = slot.candidates[slot.selected]
    # the middle of the clip is more representative than its first frame
    middle = (slot.end - slot.start) / 2
    candidate = chosen.model_copy(update={"clip_start": chosen.clip_start + middle})

    folder = store.project_dir(project.id)
    cache_dir = folder / "cache"
    overlay, window, mask, track = None, (0, 0, WIDTH, HEIGHT), None, None
    if project.settings.layout == "framed":
        overlay, window = prepare_frame(project.settings, cache_dir)
        window = scaled_window(project.settings, window)
        mask = prepare_mask(window[2], window[3], corner_radii(project.settings), cache_dir)
        # the background track from the last render, if there is one; otherwise the
        # preview falls back to a blurred copy of the clip, as an un-themed render would
        tracks = sorted(cache_dir.glob("bgtrack_*.mp4"), key=lambda p: p.stat().st_mtime)
        track = tracks[-1] if tracks and beat.backgrounds else None

    args, _ = _segment_args(
        candidate, 1, project.settings, overlay, window,
        track=track, track_frame=round((slot.start + middle) * FPS), track_identity=[], mask=mask,
    )
    cache_dir.mkdir(exist_ok=True)
    out = cache_dir / "preview.jpg"
    # keep the inputs and filter graph, swap the video encode settings for a single JPEG
    _run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args[: args.index("-frames:v")],
          "-frames:v", "1", "-s", "1280x720", "-q:v", "3", "-update", "1", str(out)])
    return out


def _audio_with_quotes(narration: str, interruptions: list[tuple[float, Quote, int]]) -> tuple[list[str], str]:
    """ffmpeg inputs and filter graph for the soundtrack: the narration, cut at each
    interruption, with the quote's own sound spliced in. Input 0 is the video, 1 the
    narration, 2.. the quote sources."""
    common = "aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo"
    inputs = ["-i", narration]
    pieces = len(interruptions) + 1
    graph = [f"[1:a]{common},asplit={pieces}" + "".join(f"[s{i}]" for i in range(pieces))]
    order: list[str] = []
    previous = 0.0
    for i, (cut, quote, frames) in enumerate(interruptions):
        if cut > previous:
            graph.append(f"[s{i}]atrim=start={previous:.6f}:end={cut:.6f},asetpts=PTS-STARTPTS[n{i}]")
            order.append(f"[n{i}]")
        else:
            graph.append(f"[s{i}]anullsink")  # two quotes at the same spot: no narration between them
        length = frames / FPS
        inputs += ["-ss", f"{quote.clip_start:.3f}", "-t", f"{length + 0.5:.3f}", "-i", quote.file_path]
        # padded/trimmed to exactly the quote's video length; short fades avoid clicks at the cuts
        graph.append(
            f"[{i + 2}:a:0]{common},apad,atrim=end={length:.6f},asetpts=PTS-STARTPTS,"
            f"afade=t=in:d=0.05,afade=t=out:st={max(0.0, length - 0.08):.6f}:d=0.08[q{i}]"
        )
        order.append(f"[q{i}]")
        previous = cut
    last = len(interruptions)
    graph.append(f"[s{last}]atrim=start={previous:.6f},asetpts=PTS-STARTPTS[n{last}]")
    order.append(f"[n{last}]")
    graph.append("".join(order) + f"concat=n={len(order)}:v=0:a=1[a]")
    return inputs, ";".join(graph)


def render_project(project_id: str, report) -> None:
    project = store.load_project(project_id)
    slots = [slot for beat in project.beats for slot in beat.slots]
    if not slots:
        raise RuntimeError("Nothing to render - find footage first")
    if not project.audio_file:
        raise RuntimeError("Project has no audio")

    folder = store.project_dir(project_id)
    segments_dir = folder / "segments"
    output_dir = folder / "output"
    segments_dir.mkdir(exist_ok=True)
    output_dir.mkdir(exist_ok=True)

    overlay, window = (None, (0, 0, WIDTH, HEIGHT))
    mask: Path | None = None
    if project.settings.layout == "framed":
        overlay, window = prepare_frame(project.settings, folder / "cache")
        window = scaled_window(project.settings, window)
        mask = prepare_mask(window[2], window[3], corner_radii(project.settings), folder / "cache")

    # Approved quotes play between beats: each goes after the beat whose end is
    # nearest its position (-1 = before the first beat).
    quotes_after: dict[int, list[Quote]] = {}
    for quote in sorted((q for q in project.quotes if q.status == "approved"), key=lambda q: q.at):
        if quote.at <= project.beats[0].start + 1e-3:
            index = -1
        else:
            index = min(range(len(project.beats)), key=lambda i: abs(project.beats[i].end - quote.at))
        quotes_after.setdefault(index, []).append(quote)

    # Everything that appears on screen, in order: (clip or None, frames, quote or
    # None, beat index). Beat ranges on the video clock include any quotes that play
    # after the beat.
    items: list[tuple[Candidate | None, int, Quote | None, int]] = []
    ranges: list[tuple[int, int]] = []
    position = 0

    def add_quotes(index: int) -> None:
        nonlocal position
        for quote in quotes_after.get(index, []):
            frames = max(1, round((quote.clip_end - quote.clip_start) * FPS))
            clip = Candidate(
                scene_id=0, source_id=quote.source_id, file_path=quote.file_path,
                scene_start=quote.clip_start, scene_end=quote.clip_end,
                clip_start=quote.clip_start, clip_end=quote.clip_end, score=0.0,
            )
            items.append((clip, frames, quote, index))
            position += frames

    add_quotes(-1)
    for index, beat in enumerate(project.beats):
        beat_start = 0 if index == 0 else position
        for slot in beat.slots:
            candidate = slot.candidates[slot.selected] if slot.selected is not None else None
            frames = _frame_count(slot.start, slot.end)
            items.append((candidate, frames, None, index))
            position += frames
        add_quotes(index)
        ranges.append((beat_start, position))
    total_frames = position

    jobs: list[tuple[Path, list[str]]] = []
    done = 0
    stage_label = ""
    lock = threading.Lock()

    def encode(job: tuple[Path, list[str]]) -> None:
        nonlocal done
        path, args = job
        # encode to a temp name so an interrupted run can't leave a half-written file that looks cached
        partial = path.with_suffix(".part.mp4")
        _run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args, str(partial)])
        partial.replace(path)
        with lock:
            done += 1
            report(f"{stage_label} {done}/{total_jobs}")

    def run_jobs(label: str) -> None:
        nonlocal done, stage_label, total_jobs
        done, stage_label, total_jobs = 0, label, len(jobs)
        report(f"{label} 0/{total_jobs}")
        with ThreadPoolExecutor(max_workers=PARALLEL_ENCODES) as pool:
            list(pool.map(encode, jobs))
        jobs.clear()

    total_jobs = 0
    cache_dir = folder / "cache"
    cache_dir.mkdir(exist_ok=True)

    # Background track (framed layout, once scenery has been chosen)
    track: Path | None = None
    placed: list[bgtrack.Placed] = []
    speed = min(1.0, max(0.25, project.settings.background_speed))
    if project.settings.layout == "framed":
        placed = bgtrack.plan(ranges, [beat.backgrounds for beat in project.beats], total_frames, speed)
    if placed:
        parts_dir = folder / "bgparts"
        parts_dir.mkdir(exist_ok=True)
        track_parts: list[Path] = []
        for path, args in bgtrack.pieces(placed, source_crop, parts_dir, speed):
            track_parts.append(path)
            if not path.exists() and all(path != queued for queued, _ in jobs):
                jobs.append((path, args))
        run_jobs("building background")

        tag = hashlib.sha1("".join(p.name for p in track_parts).encode()).hexdigest()[:16]
        track = cache_dir / f"bgtrack_{tag}.mp4"
        if not track.exists():
            listing = cache_dir / "bgtrack.txt"
            listing.write_text("".join(f"file '{p.as_posix()}'\n" for p in track_parts), encoding="utf-8")
            partial = track.with_suffix(".part.mp4")
            _run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0",
                  "-i", str(listing), "-c", "copy", str(partial)])
            partial.replace(track)
        for stale in set(cache_dir.glob("bgtrack_*.mp4")) - {track}:
            stale.unlink(missing_ok=True)
        for stale in set(parts_dir.glob("*.mp4")) - set(track_parts):
            stale.unlink(missing_ok=True)

    ordered: list[Path] = []
    # (narration time where the narration is interrupted, quote, its length in frames)
    interruptions: list[tuple[float, Quote, int]] = []
    position = 0
    for candidate, frames, quote, index in items:
        args, identity = _segment_args(
            candidate, frames, project.settings, overlay, window,
            track=track, track_frame=position, track_identity=bgtrack.slice_identity(placed, position, frames, speed),
            mask=mask,
        )
        name = hashlib.sha1(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:16]
        path = segments_dir / f"{name}.mp4"
        ordered.append(path)
        if not path.exists() and all(path != queued for queued, _ in jobs):
            jobs.append((path, args))
        if quote is not None:
            # cut the narration on the same frame the video cuts on, so nothing drifts
            cut = 0.0 if index < 0 else round(project.beats[index].end * FPS) / FPS
            interruptions.append((cut, quote, frames))
        position += frames

    run_jobs("rendering clips")

    report("joining clips with the narration")
    concat_list = cache_dir / "concat.txt"
    concat_list.write_text("".join(f"file '{p.as_posix()}'\n" for p in ordered), encoding="utf-8")
    output = output_dir / "video.mp4"
    partial = output_dir / "video.part.mp4"
    narration = str(folder / project.audio_file)
    if not interruptions:
        audio_inputs, audio_map = ["-i", narration], ["-map", "1:a"]
    else:
        audio_inputs, graph = _audio_with_quotes(narration, interruptions)
        audio_map = ["-filter_complex", graph, "-map", "[a]"]
    _run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "concat", "-safe", "0", "-i", str(concat_list),
        *audio_inputs,
        "-map", "0:v", *audio_map, "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
        # quote sources carry episode chapters/metadata that would otherwise be copied in
        "-map_chapters", "-1", "-map_metadata", "-1",
        "-movflags", "+faststart", str(partial),
    ])
    partial.replace(output)

    # segments no current slot uses are leftovers from earlier choices
    for stale in set(segments_dir.glob("*.mp4")) - set(ordered):
        stale.unlink(missing_ok=True)

    def apply(p: Project) -> None:
        p.output_file = "output/video.mp4"
        p.rendered_at = store.now()

    store.update_project(project_id, apply)
