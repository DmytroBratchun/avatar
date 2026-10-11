"""Renders a project to video with ffmpeg.

Every slot becomes its own short video-only file in <project>/segments/, named by
a hash of everything that affects its pixels - so changing one clip, or a frame
setting, only re-encodes what actually changed. The segments are then joined
without re-encoding and the narration is muxed in.
"""

import datetime as dt
import hashlib
import json
import re
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

from app.config import CACHE_DIR, LIBRARY_DIR, data_path, media_stored, stored_path
from app.editor import bgtrack, store
from app.editor.store import Candidate, Project, Quote, RenderSettings

WIDTH, HEIGHT = 1920, 1080
# The timeline - slot lengths, cuts against the audio, the background track - is
# counted in frames of FPS. A video rendered at 48 fps (RenderSettings.frame_rate)
# keeps that plan and only encodes every frame as two: twice the frames at twice the
# rate last exactly as long, so nothing drifts. Footage (all of it 23.976) shows each
# of its frames twice; only what the renderer moves itself - a scrolling comic page,
# a panning photo - gets the in-between positions.
FPS_NUM, FPS_DEN = 24000, 1001
FPS = FPS_NUM / FPS_DEN
WIDE_ASPECT = 1.6  # picture aspect ratio from which a source counts as widescreen
ENCODE_VERSION = 3  # bump to invalidate every cached segment after changing the filter graph
PARALLEL_ENCODES = 3

# A still picture as a layer: read, scaled and converted once, then that one frame is
# repeated. (An input opened with -loop 1 is decoded again for every output frame, and
# every filter after it runs again on the same picture - most of a clip's time.)
STILL = "loop=loop=-1:size=1:start=0"

# A show that may only be shown as stills (Universe.still_shows): a slot of its
# footage shows one frame of the clip - the scene's keyframe when one falls inside it -
# with a slow pan across it, the picture enlarged a little so there is room to move.
# Each move goes from one point to another, as fractions of that room (0 = the left or
# top edge, 1 = the right or bottom edge). Neighbouring stills never get the same move.
STILL_MOVES = {
    "left_to_right": ((0.0, 0.5), (1.0, 0.5)),
    "right_to_left": ((1.0, 0.5), (0.0, 0.5)),
    "bottom_left_to_top_right": ((0.0, 1.0), (1.0, 0.0)),
    "top_right_to_bottom_left": ((1.0, 0.0), (0.0, 1.0)),
    "top_left_to_bottom_right": ((0.0, 0.0), (1.0, 1.0)),
    "bottom_right_to_top_left": ((1.0, 1.0), (0.0, 0.0)),
}
# the frame is enlarged this many times before the moving crop is taken from it, so
# the crop's whole-pixel steps are a fraction of a pixel on screen (no judder)
STILL_UPSCALE = 3

BORDER_FRAME = "border"
NO_FRAME = "none"

CROP_CACHE_PATH = CACHE_DIR / "crop.json"
_crop_lock = threading.Lock()


def _rate_factor(settings: RenderSettings) -> int:
    """How many output frames each frame of the FPS clock is encoded as."""
    return 2 if settings.frame_rate == 48 else 1


def _rate(factor: int) -> str:
    return f"{FPS_NUM * factor}/{FPS_DEN}"


def _run(args: list[str]) -> subprocess.CompletedProcess:
    result = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {result.stderr.strip()[-600:]}")
    return result


def _run_reporting(args: list[str], seconds: float, label: str, report) -> None:
    """Runs an ffmpeg command, reporting how far into the output it is ("label 312s /
    1520s", which the UI shows as a filling ring)."""
    import tempfile

    with tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(
            [args[0], "-progress", "pipe:1", "-nostats", *args[1:]],
            stdout=subprocess.PIPE, stderr=errors, text=True, encoding="utf-8", errors="replace",
        )
        last = 0.0
        for line in process.stdout:
            key, _, value = line.strip().partition("=")
            if key == "out_time_us" and value.isdigit():
                done = min(seconds, int(value) / 1e6)
                if done - last >= 5 or done >= seconds:
                    report(f"{label} {done:.0f}s / {seconds:.0f}s")
                    last = done
        if process.wait() != 0:
            errors.seek(0)
            message = errors.read().decode("utf-8", "replace").strip()
            raise RuntimeError(f"ffmpeg failed: {message[-600:]}")


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
    key = f"{media_stored(path)}|{stat.st_size}|{int(stat.st_mtime)}"
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
    path = LIBRARY_DIR / "frames"
    path.mkdir(parents=True, exist_ok=True)
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

    # A frame may be open on a side - artwork only at the left and right, say, with
    # nothing along the top and bottom. A walk that reached the edge of the image
    # found no artwork there, so that pair of edges is unknown; the opening is then
    # given the 4:3 shape of the footage it is for, centred, from the pair that is known.
    open_vertically = top <= 1 and bottom >= height - 2
    open_horizontally = left <= 1 and right >= width - 2
    if open_vertically and not open_horizontally:
        half = (right - left) * 3 / 4 / 2
        top, bottom = round(cy - half), round(cy + half)
    elif open_horizontally and not open_vertically:
        half = (bottom - top) * 4 / 3 / 2
        left, right = round(cx - half), round(cx + half)
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
            f"{source.name}|{stat.st_size}|{int(stat.st_mtime)}|{ENCODE_VERSION}|{frame_scale:.3f}|window-v2".encode()
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


def on_disk(candidate: Candidate | None, folder: Path) -> Candidate | None:
    """The candidate with its pictures as full paths: a comic page is stored relative
    to DATA_DIR, a photo relative to its project's folder."""
    if candidate is None or not candidate.image_path:
        return candidate
    if candidate.kind == "comic":
        return candidate.model_copy(update={"image_path": str(data_path(candidate.image_path))})
    if candidate.kind == "photo" and not Path(candidate.image_path).is_absolute():
        path = str(folder / candidate.image_path)
        return candidate.model_copy(update={"image_path": path, "file_path": path})
    return candidate


def _frame_count(start: float, end: float) -> int:
    # from absolute positions, not the duration, so rounding never accumulates into audio drift
    return max(1, round(end * FPS) - round(start * FPS))


def _comic_size(candidate: Candidate, settings: RenderSettings) -> tuple[int, int]:
    """How large a comic page is drawn: comic_width of the screen's width."""
    with Image.open(candidate.image_path) as image:
        page_w, page_h = image.size
    width = _even(WIDTH * min(1.0, max(0.2, settings.comic_width)))
    return width, _even(width * page_h / page_w)


def needs_background(candidate: Candidate | None, settings: RenderSettings) -> bool:
    """Whether anything shows behind this clip - the scenery track, in a framed
    video. Not when the clip covers the screen: a comic page at full width, footage
    shown full screen; and a photo brings its own background."""
    if candidate is None or settings.layout != "framed" or candidate.kind == "photo":
        return False
    if candidate.kind == "comic" and candidate.image_path:
        width, height = _comic_size(candidate, settings)
        return width < WIDTH or height < HEIGHT
    cw, ch, _, _ = source_crop(candidate.file_path)
    if not (settings.wide_full_screen and cw / ch >= WIDE_ASPECT):
        return True
    # as in _rate_segment_args: wider than the screen and not zoomed all the way
    # is a band with the background above and below it
    wider_than_screen = cw / ch > WIDTH / HEIGHT + 0.01
    fit_height = _even(WIDTH * ch / cw) if wider_than_screen else HEIGHT
    fill = min(1.0, max(0.0, settings.wide_fill))
    return wider_than_screen and _even(fit_height + (HEIGHT - fit_height) * fill) < HEIGHT


def _comic_args(
    candidate: Candidate, frames: int, settings: RenderSettings, encode: list[str],
    track: Path | None, track_frame: int, track_identity: list | None, upward: bool = False, factor: int = 1,
) -> tuple[list[str], dict]:
    """A page of a comic in place of a clip: the page as a column in the middle of
    the screen, scrolling from its top to its bottom over the clip's length (from its
    bottom to its top when `upward`), with no frame around it. Behind it, blurred and
    dimmed, is the background scenery when the project has it, otherwise the page itself
    - and nothing when the page is as wide as the screen and covers it."""
    width, height = _comic_size(candidate, settings)
    travel = max(0, height - HEIGHT)  # how far the page has to move to show its bottom
    seconds = frames / FPS
    # With a speed limit a page shown briefly scrolls only as far as it has time for:
    # down from its top, or up from its bottom when `upward`. The average speed is
    # held to the limit (eased motion peaks at 1.5 times it, in the middle).
    limit = settings.comic_max_speed
    moved = min(travel, round(limit * seconds)) if limit > 0 else travel
    # eased: the page starts and stops gently rather than at full speed
    progress = f"min(1,t/{seconds:.4f})"
    eased = f"({progress}*{progress}*(3-2*{progress}))"
    if not travel:
        y = f"{(HEIGHT - height) // 2}"
    elif upward:
        y = f"-({travel}-{moved}*{eased})"
    else:
        y = f"-{moved}*{eased}"
    keep = 1.0 - settings.dim
    sigma = max(0.1, settings.blur / 4)
    inputs = ["-framerate", _rate(factor), "-i", candidate.image_path]
    page = f"scale={width}:{height}:flags=lanczos,format=yuv420p,setsar=1,{STILL}"
    # the page image itself may change (a watermark cut off it): its size tells
    stamp = Path(candidate.image_path).stat().st_size
    identity = {
        "comic": stored_path(candidate.image_path), "stamp": stamp, "frames": frames, "width": width, "blur": settings.blur,
        "dim": settings.dim, "v": ENCODE_VERSION, "upward": upward,
    }
    if moved < travel:
        identity["moved"] = moved
    if width >= WIDTH and height >= HEIGHT:
        # The page hides everything behind it: no background is read or drawn, and the
        # screen is a window moving down the page (exact: a pixel at a time, not two).
        graph = f"[0:v]{page},crop={WIDTH}:{HEIGHT}:0:'-({y})':exact=1[out]"
        identity = {
            "comic": stored_path(candidate.image_path), "stamp": stamp, "frames": frames, "width": width, "v": ENCODE_VERSION,
            "upward": upward, **({"moved": moved} if moved < travel else {}),
        }
        return [*inputs, "-filter_complex", graph, "-map", "[out]", *encode], identity
    if track is None:
        graph = (
            f"[0:v]split[a][b];[a]scale={WIDTH // 4}:{HEIGHT // 4}:force_original_aspect_ratio=increase,"
            f"crop={WIDTH // 4}:{HEIGHT // 4},gblur=sigma={max(sigma, 6):.2f},"
            f"colorchannelmixer=rr={keep * 0.6:.3f}:gg={keep * 0.6:.3f}:bb={keep * 0.6:.3f},"
            f"scale={WIDTH}:{HEIGHT},format=yuv420p,setsar=1,{STILL}[bg];[b]{page}[fg]"
        )
    else:
        seek_to = max(0.0, (track_frame - 0.4) / FPS)
        inputs += ["-ss", f"{seek_to:.5f}", "-i", str(track)]
        # the track is small and at the footage's rate: blur and dim it as it is, and
        # only then enlarge it and repeat its frames up to the output rate
        graph = (
            f"[0:v]{page}[fg];"
            f"[1:v]setpts=PTS-STARTPTS,gblur=sigma={sigma:.2f},"
            f"colorchannelmixer=rr={keep:.3f}:gg={keep:.3f}:bb={keep:.3f},scale={WIDTH}:{HEIGHT},setsar=1,"
            f"fps={_rate(factor)},tpad=stop_mode=clone:stop=-1[bg]"
        )
        identity["background"] = track_identity
    graph += f";[bg][fg]overlay=x={(WIDTH - width) // 2}:y='{y}'[out]"
    return [*inputs, "-filter_complex", graph, "-map", "[out]", *encode], identity


def _photo_args(
    candidate: Candidate, frames: int, settings: RenderSettings, encode: list[str], factor: int = 1,
) -> tuple[list[str], dict]:
    """A photo in place of a clip, with no frame around it. A wide photo fills the
    screen and pans slowly across; any other is shown whole in the middle, over a
    blurred and darkened copy of itself."""
    with Image.open(candidate.image_path) as image:
        photo_w, photo_h = image.size
    seconds = frames / FPS
    progress = f"min(1,t/{seconds:.4f})"
    eased = f"({progress}*{progress}*(3-2*{progress}))"
    inputs = ["-framerate", _rate(factor), "-i", candidate.image_path]
    identity = {"photo": Path(candidate.image_path).name, "frames": frames, "v": ENCODE_VERSION}
    if photo_w / photo_h >= 1.5:
        # cover the screen with a little to spare, and pan across that spare width
        scale = max(WIDTH / photo_w, HEIGHT / photo_h) * 1.08
        width, height = _even(photo_w * scale), _even(photo_h * scale)
        travel = width - WIDTH
        graph = (
            f"color=black:s={WIDTH}x{HEIGHT}:r={_rate(factor)}[bg];"
            f"[0:v]scale={width}:{height}:flags=lanczos,format=yuv420p,setsar=1,{STILL}[fg];"
            f"[bg][fg]overlay=x='-{travel}*{eased}':y={(HEIGHT - height) // 2}:shortest=1[out]"
        )
        identity["pan"] = True
    else:
        scale = min(WIDTH * 0.9 / photo_w, HEIGHT * 0.9 / photo_h)
        width, height = _even(photo_w * scale), _even(photo_h * scale)
        keep = 1.0 - settings.dim
        sigma = max(6.0, settings.blur / 4)
        graph = (
            f"[0:v]split[a][b];[a]scale={WIDTH // 4}:{HEIGHT // 4}:force_original_aspect_ratio=increase,"
            f"crop={WIDTH // 4}:{HEIGHT // 4},gblur=sigma={sigma:.2f},"
            f"colorchannelmixer=rr={keep * 0.6:.3f}:gg={keep * 0.6:.3f}:bb={keep * 0.6:.3f},"
            f"scale={WIDTH}:{HEIGHT},format=yuv420p,setsar=1,{STILL}[bg];"
            f"[b]scale={width}:{height}:flags=lanczos,format=yuv420p,setsar=1,{STILL}[fg];"
            f"[bg][fg]overlay=x={(WIDTH - width) // 2}:y={(HEIGHT - height) // 2}[out]"
        )
        identity.update(blur=settings.blur, dim=settings.dim)
    return [*inputs, "-filter_complex", graph, "-map", "[out]", *encode], identity


def still_source_ids(db) -> set[int]:
    """Sources whose footage may only be shown as stills."""
    from sqlalchemy import select as sa_select

    from app.db.models import Source
    from app.services.universes import still_shows

    shows = still_shows()
    if not shows:
        return set()
    return set(db.execute(sa_select(Source.id).where(Source.show_title.in_(shows))).scalars())


def still_time(candidate: Candidate) -> float:
    """Which frame of a clip a still shows: the one the user picked, else the
    keyframe its scene was indexed by, when one falls inside the clip (nearest its
    middle), else the clip's middle."""
    from app.pipeline.stages.extract_keyframes import _OFFSETS_BY_COUNT, _keyframe_count_for_duration

    if candidate.still_at is not None:
        return round(candidate.still_at, 3)
    duration = candidate.scene_end - candidate.scene_start
    middle = (candidate.clip_start + candidate.clip_end) / 2
    times = [candidate.scene_start + duration * offset for offset in _OFFSETS_BY_COUNT[_keyframe_count_for_duration(duration)]]
    # a couple of frames clear of the clip's ends, which may sit on a cut
    inside = [t for t in times if candidate.clip_start + 2 / FPS <= t <= candidate.clip_end - 2 / FPS]
    return round(min(inside, key=lambda t: abs(t - middle)) if inside else middle, 3)


def still_move(candidate: Candidate, previous: str | None) -> str:
    """The camera move over a still: fixed by the frame itself, so a re-render keeps
    it, and never the same as the still just before it."""
    seed = int(hashlib.sha1(f"{media_stored(candidate.file_path)}|{still_time(candidate)}".encode()).hexdigest(), 16)
    names = list(STILL_MOVES)
    move = names[seed % len(names)]
    if move == previous:
        move = names[(seed + 1) % len(names)]
    return move


def _still_chain(cw: int, ch: int, cx: int, cy: int, frames: int, move: str, factor: int) -> str:
    """Filters turning the first frame of the input into a moving picture of the
    clip's length, at the output rate, cw:ch cropped and fitted to the screen."""
    scale = min(WIDTH / cw, HEIGHT / ch)
    width, height = _even(cw * scale), _even(ch * scale)
    count = frames * factor
    seconds = frames / FPS
    progress = f"min(1,on/{max(1, count - 1)})"
    eased = f"({progress}*{progress}*(3-2*{progress}))"
    # how far the pan goes: the picture is enlarged by this much and moves across the
    # spare - gentle on a short slot, at most 15% of the picture
    amount = min(0.15, 0.035 * seconds)
    (x0, y0), (x1, y1) = STILL_MOVES[move]
    z = f"{1 + amount:.4f}"
    x = f"(iw-iw/zoom)*({x0}+{x1 - x0}*{eased})"
    y = f"(ih-ih/zoom)*({y0}+{y1 - y0}*{eased})"
    return (
        f"trim=end_frame=1,setpts=PTS-STARTPTS,crop={cw}:{ch}:{cx}:{cy},setsar=1,"
        f"scale={width * STILL_UPSCALE}:{height * STILL_UPSCALE}:flags=lanczos,"
        f"zoompan=z='{z}':x='{x}':y='{y}':d={count}:s={width}x{height}:fps={_rate(factor)},setsar=1"
    )


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
    upward: bool = False,
    still: str | None = None,
) -> tuple[list[str], dict]:
    """ffmpeg arguments (without the output path) and the cache identity for one slot.
    `frames` and `track_frame` are on the FPS clock whatever the output rate.
    `upward` scrolls a comic page from its bottom to its top. `still` names the
    pan (STILL_MOVES) when the clip may only be shown as a still frame."""
    factor = _rate_factor(settings)
    args, identity = _rate_segment_args(
        candidate, frames, settings, overlay, window, track, track_frame, track_identity, mask, upward, factor, still
    )
    if factor > 1:
        identity["fps"] = settings.frame_rate
        if "-filter_complex" in args and not {"comic", "photo", "still"} & identity.keys():
            # Footage is composed on the FPS clock and only then repeated: its frames sit
            # exactly on every other output frame, so each shows twice, in clean pairs.
            # (Raising the rate before a held start or end shifts the pairs by one.)
            at = args.index("-filter_complex") + 1
            args = [*args]
            args[at] += f";[out]fps={_rate(factor)}[outr]"
            args[args.index("[out]")] = "[outr]"
    return args, identity


def _rate_segment_args(
    candidate: Candidate | None,
    frames: int,
    settings: RenderSettings,
    overlay: Path | None,
    window: tuple[int, int, int, int],
    track: Path | None,
    track_frame: int,
    track_identity: list | None,
    mask: Path | None,
    upward: bool,
    factor: int,
    still: str | None = None,
) -> tuple[list[str], dict]:
    encode = [
        "-frames:v", str(frames * factor), "-an",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
        "-r", _rate(factor), "-video_track_timescale", str(FPS_NUM * factor),
    ]
    if candidate is None:
        args = ["-f", "lavfi", "-i", f"color=black:s={WIDTH}x{HEIGHT}:r={_rate(factor)}", *encode]
        return args, {"blank": True, "frames": frames, "v": ENCODE_VERSION}

    if candidate.kind == "comic" and candidate.image_path:
        return _comic_args(candidate, frames, settings, encode, track, track_frame, track_identity, upward, factor)
    if candidate.kind == "photo" and candidate.image_path:
        return _photo_args(candidate, frames, settings, encode, factor)

    cw, ch, cx, cy = source_crop(candidate.file_path)
    # Timestamps are reset to zero on every stream: after a seek the first frame of an
    # input carries a small offset, and overlay would show the first output frame
    # without whichever layer starts a few milliseconds late.
    # A still picture at either end: the file is read from after hold_start to before
    # hold_end, and its first and last frames are repeated to fill the clip.
    held_start = round(candidate.hold_start * FPS)
    held_end = round(candidate.hold_end * FPS)
    if held_start + held_end >= frames:
        held_start = held_end = 0
    hold = ""
    if held_start or held_end:
        hold = (
            f"trim=end_frame={frames - held_start - held_end},"
            f"tpad=start_mode=clone:start={held_start}:stop_mode=clone:stop={held_end},"
        )
    base = f"fps={FPS_NUM}/{FPS_DEN},{hold}setpts=PTS-STARTPTS,crop={cw}:{ch}:{cx}:{cy},setsar=1"
    seek = ["-ss", f"{candidate.clip_start + held_start / FPS:.3f}", "-i", candidate.file_path]
    identity = {
        "file": media_stored(candidate.file_path), "start": candidate.clip_start, "frames": frames,
        "crop": [cw, ch, cx, cy], "layout": settings.layout, "v": ENCODE_VERSION,
    }
    if hold:
        identity["hold"] = [held_start, held_end]
    # the rate the picture is composed at: footage at its own, a still at the output's
    # (its move is drawn by the renderer, so it gets the in-between positions)
    compose_rate = f"{FPS_NUM}/{FPS_DEN}"
    if still and candidate.kind == "video":
        at = still_time(candidate)
        base = _still_chain(cw, ch, cx, cy, frames, still, factor)
        seek = ["-ss", f"{at:.3f}", "-i", candidate.file_path]
        identity.pop("start")
        identity.pop("hold", None)
        identity["still"] = [at, still]
        compose_rate = _rate(factor)

    # A widescreen source (Korra) already fills a 16:9 screen and is sharp enough to,
    # so in a framed video it is shown full screen rather than cropped into the 4:3
    # window - the frame is for the old 4:3 footage.
    full_screen = settings.layout == "simple" or (settings.wide_full_screen and cw / ch >= WIDE_ASPECT)
    if full_screen:
        identity["layout"] = "simple"
        wider_than_screen = cw / ch > WIDTH / HEIGHT + 0.01
        fit_height = _even(WIDTH * ch / cw) if wider_than_screen else HEIGHT
        fill = min(1.0, max(0.0, settings.wide_fill))
        shown_height = _even(fit_height + (HEIGHT - fit_height) * fill)
        if not wider_than_screen:
            # as wide as the screen or narrower: fit it, black at the sides if any
            graph = (
                f"[0:v]{base},scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=decrease,"
                f"pad={WIDTH}:{HEIGHT}:(ow-iw)/2:(oh-ih)/2:black[out]"
            )
            return [*seek, "-filter_complex", graph, "-map", "[out]", *encode], identity
        if shown_height >= HEIGHT:
            # zoomed all the way: fills the screen, sides cropped
            graph = f"[0:v]{base},scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,crop={WIDTH}:{HEIGHT}[out]"
            identity["wide_fill"] = 1.0
            return [*seek, "-filter_complex", graph, "-map", "[out]", *encode], identity
        # Wider than the screen and not fully zoomed: a full-width band with the
        # blurred background above and below it - the framed composition without the
        # frame or the rounded corners.
        identity["wide_fill"] = fill
        window = (0, (HEIGHT - shown_height) // 2, WIDTH, shown_height)
        overlay, mask = None, None

    x, y, w, h = window
    keep = 1.0 - settings.dim
    # blur at quarter resolution: far faster than blurring 1080p and looks the same once scaled back up
    sigma = max(0.1, settings.blur / 4)
    blur_chain = (
        f"scale={WIDTH // 4}:{HEIGHT // 4}:force_original_aspect_ratio=increase,crop={WIDTH // 4}:{HEIGHT // 4},"
        # dimmed while still small: at full size it is the costliest step of the background
        f"gblur=sigma={sigma:.2f},colorchannelmixer=rr={keep:.3f}:gg={keep:.3f}:bb={keep:.3f},"
        f"scale={WIDTH}:{HEIGHT}[bg]"
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
            f"[1:v]fps={compose_rate},setpts=PTS-STARTPTS,tpad=stop_mode=clone:stop=-1,gblur=sigma={sigma:.2f},"
            f"colorchannelmixer=rr={keep:.3f}:gg={keep:.3f}:bb={keep:.3f},scale={WIDTH}:{HEIGHT}[bg]"
        )
        identity["background"] = track_identity

    next_input = 1 if track is None else 2
    if mask is not None:
        # round the clip's corners so they don't poke out from under the frame artwork
        inputs += ["-i", str(mask)]
        graph += (
            f";[fg]format=yuva420p[fgc];[{next_input}:v]format=gray,{STILL}[m];"
            f"[fgc][m]alphamerge[fgr];[bg][fgr]overlay={x}:{y}[c]"
        )
        next_input += 1
    else:
        graph += f";[bg][fg]overlay={x}:{y}[c]"
    if overlay is not None:
        inputs += ["-i", str(overlay)]
        graph += f";[{next_input}:v]format=yuva420p,{STILL}[frame];[c][frame]overlay=0:0[out]"
    else:
        graph += ";[c]null[out]"
    identity.update(
        {
            "window": window, "blur": settings.blur, "dim": settings.dim,
            "frame": overlay.name if overlay else None, "mask": mask.name if mask else None,
        }
    )
    return [*inputs, "-filter_complex", graph, "-map", "[out]", *encode], identity


FORMATS = ("4:3", "16:9", "wide")
COMIC = "comic"  # the preview's look of a comic page, next to the three footage formats
# picture size of a stand-in clip for each format, when no real footage of it exists
PLACEHOLDER_SIZES = {"4:3": (1440, 1080), "16:9": (1920, 1080), "wide": (1920, 804)}


def footage_format(path: str) -> str:
    """Which of the three looks a source file gets: "4:3" (shown in the frame),
    "16:9" (fills the screen) or "wide" (cinema format, wider than the screen)."""
    cw, ch, _, _ = source_crop(path)
    ratio = cw / ch
    if ratio > 1.9:
        return "wide"
    return "16:9" if ratio >= WIDE_ASPECT else "4:3"


def _placeholder(fmt: str) -> str:
    """A stand-in clip of the given format: a soft gradient with a grid, so the
    edges and proportions of the picture area are easy to see."""
    width, height = PLACEHOLDER_SIZES[fmt]
    path = CACHE_DIR / f"editor_placeholder_{width}x{height}.mp4"
    if not path.exists():
        _run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
            "-i", f"gradients=s={width}x{height}:c0=0x27456b:c1=0xd08a4e:x0=0:y0=0:x1={width}:y1={height}:d=6:r={FPS_NUM}/{FPS_DEN}",
            "-vf", f"drawgrid=w={width // 8}:h={height // 6}:t=2:c=white@0.35,drawbox=x=0:y=0:w={width}:h={height}:t=6:c=white@0.8",
            "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", str(path),
        ])
    return str(path)


def preview_clips(project: Project, fmt: str | None) -> tuple[str, list[tuple[Candidate, float, float | None]]]:
    """What the Look preview can show for a format: (where the clips come from,
    [(clip, its length, its position on the narration clock or None)]).

    The project's own clips of that format come first. A format the project has no
    clip of is shown with footage of that format from the library, and if there is
    none of that either, with a placeholder - so each format's settings can be
    adjusted before any such clip is in the video."""
    from sqlalchemy import select as sa_select

    from app.db.models import Scene, Source
    from app.db.session import SessionLocal
    from app.editor.footage import _clip_window

    own = [
        (slot.candidates[slot.selected], slot.end - slot.start, slot.start)
        for beat in project.beats for slot in beat.slots if slot.selected is not None
    ]
    if fmt is None:
        return "project", own
    if fmt == COMIC:
        return _comic_preview_clips(own)
    own = [item for item in own if item[0].kind == "video"]  # a comic page or a photo has no footage format
    matching = [item for item in own if footage_format(item[0].file_path) == fmt]
    if matching:
        return "project", matching

    db = SessionLocal()
    try:
        # one episode per show is enough to tell a show's format
        shows = list(store.project_shows(project))
        shows += [t for t in db.execute(sa_select(Source.show_title).distinct()).scalars() if t and t not in shows]
        for show in shows:
            source = db.execute(sa_select(Source).where(Source.show_title == show).order_by(Source.id)).scalars().first()
            if source is None or not Path(source.file_path).exists() or footage_format(source.file_path) != fmt:
                continue
            scenes = list(
                db.execute(
                    sa_select(Scene).where(Scene.source_id == source.id, Scene.end_time - Scene.start_time >= 4).order_by(Scene.start_time)
                ).scalars()
            )
            if not scenes:
                continue
            clips = []
            for scene in scenes[len(scenes) // 5 :: max(1, len(scenes) // 12)][:10]:
                start, end = _clip_window(db, scene, source, 4.0, True)
                clips.append((Candidate(
                    scene_id=scene.id, source_id=source.id, file_path=source.file_path, show_title=source.show_title,
                    scene_start=scene.start_time, scene_end=scene.end_time, clip_start=start, clip_end=end, score=0.0,
                ), 4.0, None))
            return "library", clips
    finally:
        db.close()

    path = _placeholder(fmt)
    clip = Candidate(scene_id=0, source_id=0, file_path=path, scene_start=0, scene_end=4, clip_start=1.0, clip_end=5.0, score=0.0)
    return "placeholder", [(clip, 4.0, None)]


def _comic_preview_clips(own: list[tuple[Candidate, float, float | None]]) -> tuple[str, list[tuple[Candidate, float, float | None]]]:
    """The project's comic pages, or else a few pages of the first comic in the
    library, or nothing when there is no comic with rendered pages."""
    from sqlalchemy import select as sa_select

    from app.db.models import Scene, Source
    from app.db.session import SessionLocal
    from app.editor.comic_pages import page_candidate

    pages = [item for item in own if item[0].kind == "comic" and item[0].image_path]
    if pages:
        return "project", pages
    db = SessionLocal()
    try:
        for source in db.execute(sa_select(Source).where(Source.kind == "comic").order_by(Source.id)).scalars():
            scenes = [
                scene for scene in db.execute(
                    sa_select(Scene).where(Scene.source_id == source.id).order_by(Scene.start_time)
                ).scalars()
                if scene.keyframe_paths and data_path(scene.keyframe_paths[0]).exists()
            ]
            if scenes:
                return "library", [(page_candidate(scene, source, 5.0, 0.0), 5.0, None) for scene in scenes[:10]]
    finally:
        db.close()
    return "none", []


def render_preview(project: Project, slot_index: int, fmt: str | None = None) -> Path:
    """One still of the finished look - clip, background, frame, rounding - for a
    single clip, using the project's current settings. Takes a second or two, so the
    look can be adjusted without rendering the whole video."""
    _, clips = preview_clips(project, fmt)
    if not clips:
        raise RuntimeError("Find footage first - there is nothing to preview yet")
    chosen, duration, at = clips[slot_index % len(clips)]
    chosen = on_disk(chosen, store.project_dir(project.id))
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        still = still_move(chosen, None) if chosen.kind == "video" and chosen.source_id in still_source_ids(db) else None
    finally:
        db.close()
    # the middle of the clip is more representative than its first frame (a still
    # is shown from its first frame: the frame is the same, the move only begins)
    middle = duration / 2
    candidate = chosen if still else chosen.model_copy(update={"clip_start": chosen.clip_start + middle})

    folder = store.project_dir(project.id)
    cache_dir = folder / "cache"
    overlay, window, mask, track = None, (0, 0, WIDTH, HEIGHT), None, None
    if project.settings.layout == "framed":
        overlay, window = prepare_frame(project.settings, cache_dir)
        window = scaled_window(project.settings, window)
        mask = prepare_mask(window[2], window[3], corner_radii(project.settings), cache_dir)
    # the background track from the last render, if there is one; otherwise the
    # preview falls back to a blurred copy of the clip, as an un-themed render would
    tracks = sorted(cache_dir.glob("bgtrack_*.mp4"), key=lambda p: p.stat().st_mtime) if cache_dir.exists() else []
    track = tracks[-1] if tracks else None
    # a clip that isn't on the narration clock (library footage, placeholder) is shown
    # over a different stretch of the background each time
    track_time = at + middle if at is not None else 20.0 + 11.0 * (slot_index % 12)

    # a comic page scrolls over the slot's whole length; the still is its first frame
    frames = max(1, round(duration * FPS)) if candidate.kind == "comic" else 1
    args, _ = _segment_args(
        candidate, frames, project.settings, overlay, window,
        track=track, track_frame=round(track_time * FPS), track_identity=[], mask=mask, still=still,
    )
    cache_dir.mkdir(exist_ok=True)
    out = cache_dir / "preview.jpg"
    # keep the inputs and filter graph, swap the video encode settings for a single JPEG
    _run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args[: args.index("-frames:v")],
          "-frames:v", "1", "-s", "1280x720", "-q:v", "3", "-update", "1", str(out)])
    return out


def _quote_holds(db, source_id: int, start: float, end: float) -> tuple[float, float]:
    """How long the picture of a quote should stand still at its start and at its
    end. A quote is cut by its words, and a line often begins a moment before the
    shot changes or ends a moment after: the clip then opens or closes on a few
    frames of another shot, which reads as a flash. Those frames are replaced by
    holding the first frame after the cut (the last one before it, at the end) -
    the sound is left alone, so no word is lost."""
    from app.editor.footage import MIN_FRAGMENT_SECONDS, _cuts

    frame = 1 / FPS
    cuts = _cuts(db, source_id, start, end)
    opening = [cut for cut in cuts if frame < cut - start < MIN_FRAGMENT_SECONDS]
    closing = [cut for cut in cuts if frame < end - cut < MIN_FRAGMENT_SECONDS]
    # one frame past the cut: shot detection can be a frame out
    hold_start = max(opening) + frame - start if opening else 0.0
    hold_end = end - (min(closing) - frame) if closing else 0.0
    if hold_start + hold_end > (end - start) / 2:
        return 0.0, 0.0  # a clip this short is mostly its neighbours; leave it as it is
    return round(hold_start, 3), round(hold_end, 3)


# how long the music takes to fade away before a quote and to come back after it
MUSIC_DIP = 0.6
# how long a quote's own sound takes to die away after its last word - at most; and
# for a quote cut before its last word's end was kept
QUOTE_TAIL_FADE = 0.45
QUOTE_TAIL_FADE_UNKNOWN = 0.15


def _soundtrack(
    narration: str, interruptions: list[tuple[float, Candidate, int, float | None]], music: str | None = None,
    narration_seconds: float = 0.0,
) -> tuple[list[str], str]:
    """ffmpeg inputs and filter graph for the soundtrack: the narration, cut at each
    interruption, with the quote clip's own sound spliced in. Input 0 is the video, 1
    the narration, then the music track if there is one, then the quote sources. A
    quote cut from several pieces is several interruptions at the same spot.

    The music runs on the narration's clock and is mixed into the narration only: it
    fades away before a quote, which has the show's own music, and comes back after."""
    common = "aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo"
    inputs = ["-i", narration] + (["-i", music] if music else [])
    first_quote = 3 if music else 2
    pieces = len(interruptions) + 1
    graph = [f"[1:a]{common},asplit={pieces}" + "".join(f"[s{i}]" for i in range(pieces))]
    if music:
        # padded with silence so it can never end before the narration does - to a fixed
        # length: padding without end leaves ffmpeg unable to finish the pieces it drops
        graph.append(
            f"[2:a]{common},apad=whole_dur={narration_seconds + 5:.3f},asplit={pieces}"
            + "".join(f"[m{i}]" for i in range(pieces))
        )

    def piece(i: int, start: float, end: float | None) -> str:
        """The narration from start to end (None = to its end), with its music."""
        trim = f"atrim=start={start:.6f}" + (f":end={end:.6f}" if end is not None else "") + ",asetpts=PTS-STARTPTS"
        graph.append(f"[s{i}]{trim}[n{i}]")
        if not music:
            return f"[n{i}]"
        fades = f",afade=t=in:d={MUSIC_DIP}" if i > 0 else ""
        if end is not None:
            fades += f",afade=t=out:st={max(0.0, end - start - MUSIC_DIP):.6f}:d={MUSIC_DIP}"
        graph.append(f"[m{i}]{trim}{fades}[mm{i}]")
        graph.append(f"[n{i}][mm{i}]amix=inputs=2:duration=first:normalize=0[p{i}]")
        return f"[p{i}]"

    order: list[str] = []
    previous = 0.0
    for i, (cut, clip, frames, tail) in enumerate(interruptions):
        if cut > previous:
            order.append(piece(i, previous, cut))
        else:
            graph.append(f"[s{i}]anullsink")  # two quotes at the same spot: no narration between them
            if music:
                graph.append(f"[m{i}]anullsink")
        length = frames / FPS
        inputs += ["-ss", f"{clip.clip_start:.3f}", "-t", f"{length + 0.5:.3f}", "-i", clip.file_path]
        # padded/trimmed to exactly the quote's video length. Short fades avoid clicks at
        # the cuts; at the end of a quote the scene's own sound - music, the room - dies
        # away over the time after the last word rather than stopping dead.
        fade_out = 0.08
        if tail is not None:
            fade_out = QUOTE_TAIL_FADE_UNKNOWN if tail < 0 else min(QUOTE_TAIL_FADE, max(0.08, tail - 0.05))
        fade_out = min(fade_out, length / 2)
        graph.append(
            f"[{i + first_quote}:a:0]{common},apad,atrim=end={length:.6f},asetpts=PTS-STARTPTS,"
            f"afade=t=in:d=0.05,afade=t=out:st={max(0.0, length - fade_out):.6f}:d={fade_out:.3f}[q{i}]"
        )
        order.append(f"[q{i}]")
        previous = cut
    order.append(piece(len(interruptions), previous, None))
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
            # a quote cut together from pieces is one clip per piece, back to back
            for start, end in quote.spans():
                frames = max(1, round((end - start) * FPS))
                hold_start, hold_end = _quote_holds(db, quote.source_id, start, end)
                clip = Candidate(
                    scene_id=0, source_id=quote.source_id, file_path=quote.file_path,
                    scene_start=start, scene_end=end, clip_start=start, clip_end=end, score=0.0,
                    hold_start=hold_start, hold_end=hold_end,
                )
                items.append((clip, frames, quote, index))
                position += frames

    from app.db.session import SessionLocal

    db = SessionLocal()  # read only: where the shots of a quote's episode change
    try:
        stills = still_source_ids(db)
        add_quotes(-1)
        for index, beat in enumerate(project.beats):
            beat_start = 0 if index == 0 else position
            for slot in beat.slots:
                candidate = on_disk(slot.candidates[slot.selected], folder) if slot.selected is not None else None
                frames = _frame_count(slot.start, slot.end)
                items.append((candidate, frames, None, index))
                position += frames
            add_quotes(index)
            ranges.append((beat_start, position))
    finally:
        db.close()
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
    # built only where some clip leaves room for it to show; elsewhere the track is black
    shown: list[tuple[int, int]] = []
    at = 0
    for candidate, frames, _, _ in items:
        if needs_background(candidate, project.settings):
            if shown and shown[-1][1] == at:
                shown[-1] = (shown[-1][0], at + frames)
            else:
                shown.append((at, at + frames))
        at += frames
    if shown:
        # scenery chosen before its show was made stills-only is left out
        backgrounds = [[b for b in beat.backgrounds if b.source_id not in stills] for beat in project.beats]
        placed = bgtrack.plan(ranges, backgrounds, total_frames, speed)
    if placed:
        parts_dir = folder / "bgparts"
        parts_dir.mkdir(exist_ok=True)
        track_parts: list[Path] = []
        for path, args in bgtrack.pieces(placed, source_crop, parts_dir, speed, shown):
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
    # (narration time where the narration is interrupted, quote clip, its length in frames)
    interruptions: list[tuple[float, Candidate, int, float | None]] = []
    position = 0
    comic_run = 0  # comic pages in a row so far: they scroll down and up in turn
    last_move: str | None = None  # the move of the still just before, if the slot before was one
    for candidate, frames, quote, index in items:
        is_comic = candidate is not None and candidate.kind == "comic"
        still = None
        if candidate is not None and candidate.kind == "video" and candidate.source_id in stills:
            still = still_move(candidate, last_move)
        last_move = still
        args, identity = _segment_args(
            candidate, frames, project.settings, overlay, window,
            track=track, track_frame=position, track_identity=bgtrack.slice_identity(placed, position, frames, speed),
            mask=mask, upward=is_comic and comic_run % 2 == 1, still=still,
        )
        comic_run = comic_run + 1 if is_comic else 0
        name = hashlib.sha1(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:16]
        path = segments_dir / f"{name}.mp4"
        ordered.append(path)
        if not path.exists() and all(path != queued for queued, _ in jobs):
            jobs.append((path, args))
        if quote is not None:
            # cut the narration on the same frame the video cuts on, so nothing drifts
            cut = 0.0 if index < 0 else round(project.beats[index].end * FPS) / FPS
            # the last piece of a quote lets the scene's sound die away after the words
            tail = None
            if candidate.clip_end == quote.spans()[-1][1]:
                tail = candidate.clip_end - quote.speech_end if quote.speech_end is not None else -1.0
            interruptions.append((cut, candidate, frames, tail))
        position += frames

    run_jobs("rendering clips")

    report("joining clips with the narration")
    concat_list = cache_dir / "concat.txt"
    concat_list.write_text("".join(f"file '{p.as_posix()}'\n" for p in ordered), encoding="utf-8")
    output = output_dir / "video.mp4"
    partial = output_dir / "video.part.mp4"
    narration = str(folder / project.audio_file)
    if project.settings.sound_effects:
        from app.editor.sounds import build_narration

        report("adding sound effects")
        narration = str(build_narration(project, folder) or narration)
        report("joining clips with the narration")
    music_track = None
    if project.settings.music_enabled and project.music:
        from app.editor.music import blocks_fit, build_track, refit_blocks

        report("mixing the music")
        if not blocks_fit(project) and refit_blocks(project):
            # the beats were built again since the music was planned: keep the music
            blocks = project.music
            store.update_project(project_id, lambda p: setattr(p, "music", blocks))
        music_track = build_track(project, folder)
        report("joining clips with the narration")
    # one gain for the whole soundtrack, so the narration ends at the target loudness;
    # a limiter keeps the odd loud quote or peak from clipping
    from app.editor.music import narration_loudness

    gain = project.settings.loudness_target - narration_loudness(project, folder)
    level = f"volume={gain:.2f}dB,alimiter=limit=0.891:level=disabled:latency=1"
    if not interruptions and music_track is None:
        audio_inputs, audio_map = ["-i", narration], ["-map", "1:a", "-af", level]
    else:
        audio_inputs, graph = _soundtrack(
            narration, interruptions, str(music_track) if music_track else None, project.audio_duration or 0.0
        )
        audio_map = ["-filter_complex", f"{graph};[a]{level}[loud]", "-map", "[loud]"]
    _run_reporting([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "concat", "-safe", "0", "-i", str(concat_list),
        *audio_inputs,
        "-map", "0:v", *audio_map, "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
        # quote sources carry episode chapters/metadata that would otherwise be copied in
        "-map_chapters", "-1", "-map_metadata", "-1",
        "-movflags", "+faststart", str(partial),
    ], total_frames / FPS, "joining clips with the narration", report)
    # The previous render may still be open - in a player, or streamed to the browser
    # tab that watched it - and Windows won't replace an open file. The new video then
    # gets a name of its own rather than the render failing after all the encoding.
    try:
        partial.replace(output)
    except PermissionError:
        output = output_dir / f"video_{dt.datetime.now():%Y%m%d_%H%M%S}.mp4"
        partial.replace(output)
    for stale in set(output_dir.glob("video*.mp4")) - {output}:
        try:
            stale.unlink()
        except OSError:
            pass  # still open; the next render tries again

    # segments no current slot uses are leftovers from earlier choices
    for stale in set(segments_dir.glob("*.mp4")) - set(ordered):
        stale.unlink(missing_ok=True)

    def apply(p: Project) -> None:
        p.output_file = f"output/{output.name}"
        p.rendered_at = store.now()

    store.update_project(project_id, apply)
