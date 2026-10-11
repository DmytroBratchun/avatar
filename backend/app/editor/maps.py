"""Map shots: a journey drawn across a map of the universe's world, for a stretch of
narration about where someone went.

A map lives in <root>/library/maps/<name>/: the picture (map.json names it, with
its font and credit), places.json - every named place with its pixel position, the
side it belongs to (the free peoples or the enemy) and, optionally, a picture of it
in places/ - and routes.json, journeys as lists of points. A universe names its map
(Universe.map).

A shot (store.MapShot) goes from one place to another, along a journey or straight
through the places given. The map lies on a table, seen at an angle; the camera
starts close on the first place, draws back to show the whole way as a dashed ink
line is drawn along it, and closes in on the last. The places are named in the
map's own hand, and a place with a picture shows it in a round medallion pinned to
the map with a leader - a frame for the free peoples and one for the enemy.
Everything drawn lies in the plane of the map, so it is foreshortened with it,
except the medallions, which stand up. Lines and letters are laid on as ink,
multiplied into the paper, so its grain shows through.

Frames are drawn here with OpenCV and piped to ffmpeg, encoded like every other
segment of the video (see render.py). Pictures of places are made with GPT Image in
the style of the map (generate_picture).
"""

import base64
import json
import math
import subprocess
import threading
import urllib.error
import urllib.request
import uuid
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from app.config import LIBRARY_DIR
from app.editor.store import MapShot

MAPS_DIR = LIBRARY_DIR / "maps"
FONTS_DIR = LIBRARY_DIR / "fonts"
MAP_VERSION = 1  # bump after changing how a shot is drawn, to draw cached shots again

W, H = 1920, 1080
FPS_NUM, FPS_DEN = 24000, 1001
Z_MAX = 1.7  # beyond this the map picture is visibly upscaled
FOV = 35.0  # vertical field of view of the camera, degrees
INK = (32, 30, 168)  # BGR, the red ink of the route, its marks and the names
HALO = (222, 205, 178)  # RGB, the parchment under a name
TABLE = (24, 30, 38)  # BGR, beyond the map's edge
SEPIA_DARK, SEPIA_LIGHT = (16, 30, 44), (176, 206, 222)  # BGR, medallion frames and leaders
LABEL_SIZES = [24, 34, 48, 68, 96, 136, 192, 272]  # font px, steps of sqrt(2)
MEDALLION_SIZES = [64, 90, 128, 181, 256, 362, 512, 724]  # frame widths, steps of sqrt(2)
MEDALLION_PX = 380  # a medallion's width on screen when it appears; then it scales with the map
LEAD, TAIL = 0.8, 1.2  # seconds before the line starts and after it arrives

_lock = threading.Lock()


# --- the map's data -----------------------------------------------------------


def map_dir(name: str) -> Path:
    path = (MAPS_DIR / name).resolve()
    if path.parent != MAPS_DIR.resolve() or not (path / "map.json").exists():
        raise ValueError(f"No map named {name!r}")
    return path


def _read(name: str, file: str) -> dict:
    return json.loads((map_dir(name) / file).read_text(encoding="utf-8"))


def info(name: str) -> dict:
    """What the editor shows to build a shot: the places and journeys of a map."""
    meta = _read(name, "map.json")
    routes = _read(name, "routes.json")["routes"]
    return {
        "name": name,
        "title": meta.get("name", name),
        "credit": meta.get("credit", ""),
        "places": _read(name, "places.json")["places"],
        "routes": [{"id": key, "label": route.get("label", key)} for key, route in routes.items()],
    }


def places(name: str) -> dict[str, dict]:
    return {p["id"]: p for p in _read(name, "places.json")["places"]}


def _save_places(name: str, by_id: dict[str, dict]) -> None:
    path = map_dir(name) / "places.json"
    path.write_text(json.dumps({"places": sorted(by_id.values(), key=lambda p: p["name"])}, indent=1, ensure_ascii=False), encoding="utf-8")


def picture_path(name: str, place: dict) -> Path | None:
    if not place.get("picture"):
        return None
    path = map_dir(name) / "places" / place["picture"]
    return path if path.exists() else None


# --- building a shot ------------------------------------------------------------


def _span(points: list, first: dict, last: dict) -> tuple[int, int]:
    """Where on a journey to start and stop. A journey can pass a place more than
    once (there and back again), and a place need not lie on it: the points nearest
    the two places that are walked forwards, from one to the other, are taken; the
    journey is walked backwards only when no forward way comes near both."""
    d_start = [math.dist(p[:2], (first["x"], first["y"])) for p in points]
    d_stop = [math.dist(p[:2], (last["x"], last["y"])) for p in points]
    n = len(points)

    def best(forward: bool):
        pairs = [(a, b) for a in range(n) for b in range(n) if (a < b if forward else a > b)]
        return min(pairs, key=lambda ab: (round(d_start[ab[0]] + d_stop[ab[1]], -1), abs(ab[1] - ab[0])), default=None)

    ahead = best(True)
    if ahead is not None and d_start[ahead[0]] + d_stop[ahead[1]] <= 400:
        return ahead
    back = best(False)
    return back if back is not None else ahead


def waypoints(shot: MapShot) -> list:
    """The points the line goes through: the journey cut between the first and last
    places (it may be walked either way), or the places themselves."""
    by_id = places(shot.map)
    stops = [by_id[p] for p in shot.places if p in by_id]
    if len(stops) < 2:
        raise ValueError("A map shot needs at least two places on the map")
    first, last = stops[0], stops[-1]
    if shot.route:
        route = _read(shot.map, "routes.json")["routes"].get(shot.route)
        if route is None:
            raise ValueError(f"No journey {shot.route!r} on the map")
        points = route["points"]
        a, b = _span(points, first, last)
        piece = points[a : b + 1] if a <= b else points[b : a + 1][::-1]
        return [[first["x"], first["y"]]] + piece[1:-1] + [[last["x"], last["y"]]]
    return [[p["x"], p["y"]] for p in stops]


def label(shot: MapShot, place_id: str) -> str:
    return places(shot.map).get(place_id, {}).get("name", place_id)


def describe(shot: MapShot) -> str:
    names = [label(shot, p) for p in shot.places]
    return "Map: " + " → ".join(names)


def identity(shot: MapShot, frames: int, factor: int) -> dict:
    """What a shot's pixels depend on, for the segment cache: the shot, its length
    and rate, and the files it draws (a picture made again draws it again)."""
    folder = map_dir(shot.map)
    files = [folder / "map.json", folder / "places.json", folder / "routes.json",
             folder / "medallion-frame-good.png", folder / "medallion-frame-evil.png", folder / "medallion-leader.png"]
    by_id = places(shot.map)
    files += [p for p in (picture_path(shot.map, by_id.get(i, {})) for i in shot.pictures) if p]
    stamps = [f"{f.name}:{int(f.stat().st_mtime)}" for f in files if f.exists()]
    return {"map": shot.model_dump(), "frames": frames, "fps": factor, "files": stamps, "v": MAP_VERSION}


# --- drawing --------------------------------------------------------------------


class _Picture:
    """The map picture at several sizes, so a far view samples a smaller one
    instead of aliasing."""

    def __init__(self, path: Path):
        image = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError(f"Cannot read the map picture {path}")
        self.levels = [image]
        for _ in range(3):
            prev = self.levels[-1]
            self.levels.append(cv2.resize(prev, (prev.shape[1] // 2, prev.shape[0] // 2), interpolation=cv2.INTER_AREA))

    def view(self, cx: float, cy: float, zoom: float, tilt: float):
        """The map seen by a camera aimed at (cx, cy), tilted `tilt` degrees from
        straight down towards the north. Returns the frame and the homography from
        map pixels to the screen; `zoom` is screen px per map px at the centre."""
        phi = math.radians(tilt)
        f = (H / 2) / math.tan(math.radians(FOV / 2))
        forward = np.array([0.0, -math.sin(phi), math.cos(phi)])  # z points into the table
        down = np.array([0.0, math.cos(phi), math.sin(phi)])
        rot = np.array([[1.0, 0.0, 0.0], down, forward])
        eye = np.array([cx, cy, 0.0]) - (f / zoom) * forward
        k = np.array([[f, 0, W / 2], [0, f, H / 2], [0, 0, 1]])
        to_screen = k @ np.column_stack([rot[:, 0], rot[:, 1], -rot @ eye])
        # the far half of a tilted view is smaller on screen: sample a smaller level
        reach = zoom * (0.6 if tilt else 1.0)
        level = int(np.clip(math.floor(math.log2(1 / reach)) if reach < 1 else 0, 0, len(self.levels) - 1))
        m = to_screen @ np.diag([2.0**level, 2.0**level, 1.0])
        frame = cv2.warpPerspective(self.levels[level], m, (W, H), flags=cv2.INTER_LINEAR,
                                    borderMode=cv2.BORDER_CONSTANT, borderValue=TABLE)
        return frame, to_screen


@lru_cache(maxsize=2)
def _picture(name: str) -> _Picture:
    return _Picture(map_dir(name) / _read(name, "map.json")["image"])


def _font(name: str) -> str:
    font = _read(name, "map.json").get("font", "UncialAntiqua-Regular.ttf")
    return str(FONTS_DIR / font)


def _smooth(points: list) -> np.ndarray:
    """Catmull-Rom through the waypoints; a point flagged [x, y, 1] (traced along a
    river) is joined straight."""
    pts = [(float(p[0]), float(p[1])) for p in points]
    flags = [len(p) > 2 and p[2] == 1 for p in points]
    out = []
    for i in range(len(pts) - 1):
        p0, p1, p2 = pts[max(i - 1, 0)], pts[i], pts[i + 1]
        p3 = pts[min(i + 2, len(pts) - 1)]
        if flags[i] or flags[i + 1] or math.dist(p1, p2) < 15:
            out.append(p1)
            continue
        for s in range(8):
            t = s / 8
            t2, t3 = t * t, t * t * t
            out.append(tuple(
                0.5 * (2 * p1[d] + (-p0[d] + p2[d]) * t + (2 * p0[d] - 5 * p1[d] + 4 * p2[d] - p3[d]) * t2
                       + (-p0[d] + 3 * p1[d] - 3 * p2[d] + p3[d]) * t3)
                for d in (0, 1)
            ))
    out.append(pts[-1])
    return np.array(out)


def _ease(t: float) -> float:
    t = min(max(t, 0.0), 1.0)
    return t * t * (3 - 2 * t)


def _ease_out_back(t: float) -> float:
    """Overshoots a little and settles - a pop."""
    t = min(max(t, 0.0), 1.0)
    return 1 + 2.4 * (t - 1) ** 3 + 1.4 * (t - 1) ** 2


def _gaussian(values: np.ndarray, sigma: float) -> np.ndarray:
    radius = max(1, int(3 * sigma))
    kernel = np.exp(-0.5 * (np.arange(-radius, radius + 1) / sigma) ** 2)
    kernel /= kernel.sum()
    padded = np.pad(values, ((radius, radius), (0, 0)), mode="edge")
    return np.stack([np.convolve(padded[:, d], kernel, mode="valid") for d in range(values.shape[1])], axis=1)


def _noise(shape, seed: int, scale: float) -> np.ndarray:
    """0..1 noise at a few scales."""
    rng = np.random.default_rng(seed)
    h, w = shape
    total = np.zeros(shape, np.float32)
    for cell, amp in ((scale * 0.9, 1.0), (scale * 0.35, 0.6), (scale * 0.12, 0.35)):
        gh, gw = max(2, int(h / cell) + 2), max(2, int(w / cell) + 2)
        total += amp * cv2.resize(rng.standard_normal((gh, gw)).astype(np.float32), (w, h), interpolation=cv2.INTER_CUBIC)
    return (total - total.min()) / (total.max() - total.min() + 1e-6)


@lru_cache(maxsize=64)
def _label_sprites(text: str, font: str) -> dict[int, tuple]:
    """A name drawn once large - a parchment halo, and letters in dense ink a little
    uneven and darker at their edges - then shrunk to a ladder of sizes."""
    size = LABEL_SIZES[-1]
    face = ImageFont.truetype(font, size)
    outline = max(1, round(size / 22))
    box = face.getbbox(text, stroke_width=outline)
    img = Image.new("RGBA", (box[2] - box[0] + 8, box[3] - box[1] + 8), (0, 0, 0, 0))
    at = (4 - box[0], 4 - box[1])
    ImageDraw.Draw(img).text(at, text, font=face, fill=(*HALO, 235), stroke_width=outline, stroke_fill=(*HALO, 235))
    halo = np.array(img)[:, :, [2, 1, 0, 3]]
    letters = Image.new("L", img.size, 0)
    ImageDraw.Draw(letters).text(at, text, font=face, fill=255)
    letters = np.array(letters).astype(np.float32) / 255
    edge = np.clip(letters - cv2.erode(letters, np.ones((5, 5), np.uint8), iterations=2), 0, 1)
    # dense everywhere - thin ink over the light halo turns pink
    density = np.clip(0.86 + 0.14 * _noise(letters.shape, sum(map(ord, text)), size / 4) + 0.2 * edge, 0, 1)
    ink = np.dstack([np.zeros(halo.shape[:2] + (3,), np.uint8), (letters * density * 255).astype(np.uint8)])
    sprites = {}
    for target in LABEL_SIZES:
        k = target / size
        dims = (max(1, round(halo.shape[1] * k)), max(1, round(halo.shape[0] * k)))
        sprites[target] = (cv2.resize(halo, dims, interpolation=cv2.INTER_AREA), cv2.resize(ink, dims, interpolation=cv2.INTER_AREA))
    return sprites


def _grey(art: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(art[:, :, :3].astype(np.uint8), cv2.COLOR_BGR2GRAY).astype(np.float32)


def _sepia(tone: np.ndarray) -> np.ndarray:
    """A generated drawing's tones in the map's own sepia, whatever colours it came in."""
    tone = np.clip((tone - 40) / 170, 0, 1)[:, :, None] ** 1.5
    return np.array(SEPIA_DARK, np.float32) + (np.array(SEPIA_LIGHT, np.float32) - SEPIA_DARK) * tone


@lru_cache(maxsize=16)
def _medallion(map_name: str, picture: str, side: str) -> tuple[dict, float]:
    """A picture of a place in its round frame. Returns sprites keyed by the frame's
    width, and the radius of the ring as a share of that width (where the leader ends)."""
    folder = map_dir(map_name)
    frame = cv2.imread(str(folder / f"medallion-frame-{side}.png"), cv2.IMREAD_UNCHANGED).astype(np.float32)
    tone = _grey(frame)
    if side == "evil":
        # the enemy's frame is drawn finer: a little heavier, so its thorns read when small
        tone = cv2.erode(tone, np.ones((3, 3), np.uint8), iterations=2)
        frame[:, :, 3] = np.clip(cv2.dilate(frame[:, :, 3], np.ones((3, 3), np.uint8), iterations=2) * 1.4, 0, 255)
    frame[:, :, :3] = _sepia(tone)
    # a soft light halo around the drawing lifts it off the map
    glow = cv2.GaussianBlur(cv2.dilate(frame[:, :, 3], np.ones((5, 5), np.uint8), iterations=3), (0, 0), 9) / 255 * 0.25
    under = np.clip(glow - frame[:, :, 3] / 255, 0, 1)[:, :, None]
    frame[:, :, :3] = frame[:, :, :3] * (1 - under) + np.array([196, 222, 236], np.float32) * under
    frame[:, :, 3] = np.maximum(frame[:, :, 3], under[:, :, 0] * 255)
    size = frame.shape[0]
    # the opening; a hairline gap in a generated ring would let it run out to the edge
    solid = cv2.morphologyEx((frame[:, :, 3] >= 128).astype(np.uint8), cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    _, labels, stats, centroids = cv2.connectedComponentsWithStats(1 - solid)
    hole = labels[size // 2, size // 2]
    cx, cy = centroids[hole]
    radius = math.sqrt(stats[hole, cv2.CC_STAT_AREA] / math.pi)
    ring = np.hypot(*np.nonzero(frame[:, :, 3] > 128)[::-1] - np.array([[cx], [cy]])).astype(np.float32)
    ring_outer = float(np.percentile(ring[ring < radius * 1.6], 60))

    img = cv2.imread(str(folder / "places" / picture))
    h, w = img.shape[:2]
    cut = int(min(h, w) * 0.72)  # the middle of the picture fills the opening
    x0, y0 = (w - cut) // 2, (h - cut) // 2
    r_pic = radius + 6  # tucked under the frame's inner edge
    face = cv2.resize(img[y0 : y0 + cut, x0 : x0 + cut], (int(2 * r_pic), int(2 * r_pic)), interpolation=cv2.INTER_AREA)
    canvas = np.zeros((size, size, 4), np.float32)
    px, py = int(round(cx - r_pic)), int(round(cy - r_pic))
    canvas[py : py + face.shape[0], px : px + face.shape[1], :3] = face
    yy, xx = np.mgrid[0:size, 0:size]
    canvas[:, :, 3] = np.clip(r_pic + 0.5 - np.hypot(xx - cx, yy - cy), 0, 1)
    a = frame[:, :, 3:4] / 255
    canvas[:, :, :3] = canvas[:, :, :3] * (1 - a) + frame[:, :, :3] * a
    canvas[:, :, 3] = np.maximum(canvas[:, :, 3], a[:, :, 0]) * 255
    full = np.clip(canvas, 0, 255).astype(np.uint8)
    return {d: cv2.resize(full, (d, d), interpolation=cv2.INTER_AREA) for d in MEDALLION_SIZES}, ring_outer / size


@lru_cache(maxsize=2)
def _leader(map_name: str):
    """The leader in the map's sepia, cropped to what is drawn, at a ladder of sizes,
    with where its foot (the ring on the place) and its top are."""
    art = cv2.imread(str(map_dir(map_name) / "medallion-leader.png"), cv2.IMREAD_UNCHANGED).astype(np.float32)
    ys, xs = np.nonzero(art[:, :, 3] > 40)
    art = art[ys.min() : ys.max() + 1, max(0, xs.min() - 4) : xs.max() + 5]
    tone = cv2.erode(_grey(art), np.ones((3, 3), np.uint8), iterations=2)
    art[:, :, 3] = cv2.dilate(art[:, :, 3], np.ones((3, 3), np.uint8), iterations=2)
    art[:, :, :3] = _sepia(tone)
    art = np.clip(art, 0, 255).astype(np.uint8)
    h, w = art.shape[:2]
    foot_rows = art[int(h * 0.97) :, :, 3] > 40
    fx = float(np.nonzero(foot_rows.any(axis=0))[0].mean())
    foot = (fx, h - 1 - min(h * 0.012, 18))
    ladder, k = {1.0: art}, 1.0
    while k > 0.06:
        k /= math.sqrt(2)
        ladder[k] = cv2.resize(art, (max(1, round(w * k)), max(1, round(h * k))), interpolation=cv2.INTER_AREA)
    return ladder, foot, (fx, 0.0)


def _blend(frame: np.ndarray, piece: np.ndarray, x0: int, y0: int, alpha: float, ink=None) -> None:
    a = piece[:, :, 3:4].astype(np.float32) / 255 * alpha
    y1, x1 = y0 + piece.shape[0], x0 + piece.shape[1]
    region = frame[y0:y1, x0:x1].astype(np.float32)
    if ink is not None:  # multiplied into the paper, so its grain shows through
        frame[y0:y1, x0:x1] = (region * (1 - a * (1 - np.array(ink, np.float32) / 255))).astype(np.uint8)
    else:
        frame[y0:y1, x0:x1] = (region * (1 - a) + piece[:, :, :3] * a).astype(np.uint8)


def _warp_onto(frame: np.ndarray, sprite: np.ndarray, to_frame: np.ndarray, alpha: float, ink=None) -> None:
    """A BGRA sprite through a homography (sprite px -> screen px)."""
    h, w = sprite.shape[:2]
    corners = np.array([[0, 0, 1], [w, 0, 1], [0, h, 1], [w, h, 1]], np.float64) @ to_frame.T
    if alpha <= 0 or np.any(corners[:, 2] <= 0):
        return
    xy = corners[:, :2] / corners[:, 2:3]
    x0, y0 = np.floor(xy.min(axis=0)).astype(int) - 1
    x1, y1 = np.ceil(xy.max(axis=0)).astype(int) + 1
    x0, y0, x1, y1 = max(x0, 0), max(y0, 0), min(x1, W), min(y1, H)
    if x0 >= x1 or y0 >= y1:
        return
    shift = np.array([[1, 0, -x0], [0, 1, -y0], [0, 0, 1]], np.float64)
    piece = cv2.warpPerspective(sprite, shift @ to_frame, (x1 - x0, y1 - y0), flags=cv2.INTER_LINEAR, borderValue=(0, 0, 0, 0))
    _blend(frame, piece, x0, y0, alpha, ink)


def _place_sprite(frame: np.ndarray, sprites: dict, size: float, x: float, y: float, alpha: float) -> None:
    """A sprite standing up (not in the map's plane), `size` wide, centred on (x, y)."""
    sizes = sorted(sprites)
    base = next((s for s in sizes if s >= size), sizes[-1])
    sprite = sprites[base]
    k = size / base
    h, w = sprite.shape[:2]
    x0, y0 = x - w * k / 2, y - h * k / 2
    ix0, iy0 = max(int(math.floor(x0)), 0), max(int(math.floor(y0)), 0)
    ix1, iy1 = min(int(math.ceil(x0 + w * k)) + 1, W), min(int(math.ceil(y0 + h * k)) + 1, H)
    if ix0 >= ix1 or iy0 >= iy1 or alpha <= 0:
        return
    m = np.array([[k, 0, x0 - ix0], [0, k, y0 - iy0]], dtype=np.float32)
    piece = cv2.warpAffine(sprite, m, (ix1 - ix0, iy1 - iy0), flags=cv2.INTER_LINEAR, borderValue=(0, 0, 0, 0))
    _blend(frame, piece, ix0, iy0, alpha)


def _ink_onto(frame: np.ndarray, mask: np.ndarray, strength: float) -> None:
    ys, xs = np.nonzero(mask)
    if len(ys) == 0 or strength <= 0:
        return
    y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
    piece = np.dstack([np.zeros((y1 - y0, x1 - x0, 3), np.uint8), mask[y0:y1, x0:x1]])
    _blend(frame, piece, x0, y0, strength, ink=INK)


def _stroke(img: np.ndarray, lines: list, widths: list) -> None:
    """Anti-aliased lines of any (fractional) width with round ends."""
    quads = []
    for pts, w in zip(lines, widths):
        d = np.diff(pts, axis=0)
        length = np.linalg.norm(d, axis=1)
        keep = length > 1e-6
        if not keep.any():
            continue
        a, b, d, length = pts[:-1][keep], pts[1:][keep], d[keep], length[keep]
        normal = np.column_stack([-d[:, 1], d[:, 0]]) / length[:, None] * (w / 2)
        quads.extend(np.stack([a + normal, b + normal, b - normal, a - normal], axis=1))
    if quads:
        cv2.fillPoly(img, list((np.array(quads) * 16).astype(np.int32)), 255, cv2.LINE_AA, shift=4)
    for pts, w in zip(lines, widths):
        for x, y in pts:
            cv2.circle(img, (int(x * 16), int(y * 16)), max(1, int(w / 2 * 16)), 255, -1, cv2.LINE_AA, shift=4)


def _circle(x: float, y: float, radius: float) -> np.ndarray:
    t = np.linspace(0, 2 * np.pi, 49)
    return np.column_stack([x + radius * np.cos(t), y + radius * np.sin(t)])


@lru_cache(maxsize=2)
def _shade(tilt: float) -> np.ndarray:
    """Darker towards the screen's edges, and the far edge of the table in shadow."""
    yy, xx = np.mgrid[0:H, 0:W]
    r = np.sqrt(((xx - W / 2) / (W / 2)) ** 2 + ((yy - H / 2) / (H / 2)) ** 2)
    shade = (1 - 0.45 * np.clip(r - 0.55, 0, 1) ** 1.5)[:, :, None].astype(np.float32)
    if tilt:
        fall = np.clip(np.linspace(0, 1, H) / 0.45, 0, 1)[:, None, None] ** 0.8
        shade = shade * (0.45 + 0.55 * fall).astype(np.float32)
    return shade


class _Shot:
    """Everything about a shot that does not change from frame to frame."""

    def __init__(self, shot: MapShot, frames: int, factor: int):
        self.shot = shot
        self.picture = _picture(shot.map)
        self.rate = FPS_NUM * factor / FPS_DEN
        self.n = max(1, frames * factor)
        seconds = frames * FPS_DEN / FPS_NUM
        path = _smooth(waypoints(shot))
        along = np.concatenate([[0], np.cumsum(np.linalg.norm(np.diff(path, axis=0), axis=1))])
        self.path, self.along, total = path, along, along[-1]
        self.total = total

        lo, hi = path.min(axis=0), path.max(axis=0)
        span = np.maximum(hi - lo, 1)
        z_fit = min(Z_MAX, W * 0.7 / span[0], H * 0.7 / span[1])
        z_close = min(Z_MAX, max(z_fit * 2.5, 1.0))
        # a short shot cannot afford the pauses at its ends
        lead, tail = (LEAD, TAIL) if seconds >= 5 else (seconds * 0.1, seconds * 0.15)
        moving = max(seconds - lead - tail, 0.5)
        times = np.arange(self.n) / self.rate
        self.progress = np.array([_ease((t - lead) / moving) for t in times])
        self.head = np.column_stack([np.interp(self.progress * total, along, path[:, 0]),
                                     np.interp(self.progress * total, along, path[:, 1])])
        # close at both ends, the whole way in the middle
        out = np.sin(np.pi * self.progress) ** 0.8
        zoom = np.exp(np.log(z_close) + out * (np.log(z_fit) - np.log(z_close)))
        centre = self.head * (1 - out[:, None]) + (lo + hi) / 2 * out[:, None]
        self.cam = _gaussian(np.column_stack([centre, np.log(zoom)]), sigma=0.35 * self.rate)

        # Ink is sized in map units, so it grows and shrinks with the map; from the
        # zoom the shot spends its time around, the line at least 2 px at the farthest
        z_ref = math.exp(np.median(self.cam[:, 2]))
        z_min = math.exp(self.cam[:, 2].min())
        self.line_w = max(4.5 / z_ref, 2.0 / z_min)
        self.dash_on = max(18.0 / z_ref, 3.5 * self.line_w)
        self.dash_starts = np.arange(0.0, total, self.dash_on + max(10.0 / z_ref, 2.2 * self.line_w))

        by_id = places(shot.map)
        arrive = lead + moving
        font = _font(shot.map)
        first, last = shot.places[0], shot.places[-1]
        pictured = [p for p in shot.pictures if p in by_id and picture_path(shot.map, by_id[p])]
        self.labels = []
        for place_id, size, appear in ((first, 64, min(0.1, seconds * 0.05)), (last, 70, max(0.0, arrive - 0.3))):
            at = np.array([by_id[place_id]["x"], by_id[place_id]["y"]], float)
            self.labels.append((_label_sprites(by_id[place_id]["name"], font), at, size, self._zoom_at(appear), appear, place_id in pictured))

        self.pinned = []
        for place_id in pictured:
            place = by_id[place_id]
            at = np.array([place["x"], place["y"]], float)
            # a picture of the first place shows at once and goes before the last one
            # comes; any other when the line reaches its place
            if place_id == first:
                appear = min(0.4, seconds * 0.1)
            else:
                reach = along[np.argmin(np.linalg.norm(path - at, axis=1))]
                when = np.nonzero(self.progress * total >= reach - 1e-6)[0]
                appear = (when[0] / self.rate) if len(when) else arrive
            # to the side the line is not on: away from where it goes, or beyond where it came from
            i = np.argmin(np.linalg.norm(path - at, axis=1))
            j = min(i + 8, len(path) - 1) if place_id == first else max(i - 8, 0)
            dx = path[j][0] - at[0]
            side = (-1 if dx > 0 else 1) if place_id == first else (1 if dx < 0 else -1) if dx else 1
            z_then = self._zoom_at(appear)
            offset = np.array([side * 1.15 * MEDALLION_PX, -0.8 * MEDALLION_PX]) / z_then
            sprites, ring = _medallion(shot.map, place["picture"], place.get("side") or "good")
            self.pinned.append([sprites, ring, at, offset, z_then, appear, None])
        # each picture makes way for the next
        self.pinned.sort(key=lambda p: p[5])
        for earlier, later in zip(self.pinned, self.pinned[1:]):
            earlier[6] = max(earlier[5] + 0.8, later[5] - 0.6)
        self.leader = _leader(shot.map)
        self.shade = _shade(shot.tilt)

    def _zoom_at(self, t: float) -> float:
        return math.exp(self.cam[min(int(t * self.rate), self.n - 1), 2])

    def frame(self, i: int) -> np.ndarray:
        cx, cy, lz = self.cam[i]
        z = math.exp(lz)
        t = i / self.rate
        frame, to_screen = self.picture.view(cx, cy, z, self.shot.tilt)
        depth_c = np.array([cx, cy, 1.0]) @ to_screen[2]

        def screen(p):
            p = np.asarray(p, float)
            q = np.concatenate([p.reshape(-1, 2), np.ones((p.size // 2, 1))], axis=1) @ to_screen.T
            return (q[:, :2] / q[:, 2:3]).reshape(p.shape)

        def local(p):
            """Screen px per map px at that point, nearer or farther."""
            return z * depth_c / (np.array([p[0], p[1], 1.0]) @ to_screen[2])

        path, along = self.path, self.along
        drawn = self.progress[i] * self.total
        if drawn > 0:
            # the dashes are fixed on the map; the last one grows as the line is drawn
            dashes, widths = [], []
            for a in self.dash_starts[self.dash_starts < drawn]:
                ts = np.linspace(a, min(a + self.dash_on, drawn), 6)
                piece = np.column_stack([np.interp(ts, along, path[:, 0]), np.interp(ts, along, path[:, 1])])
                dashes.append(screen(piece))
                widths.append(self.line_w * local(piece[len(piece) // 2]))
            mask = np.zeros((H, W), np.uint8)
            _stroke(mask, dashes, widths)
            hx, hy = screen(self.head[i])
            cv2.circle(mask, (int(hx * 16), int(hy * 16)), int(self.line_w * 1.35 * local(self.head[i]) * 16), 255, -1, cv2.LINE_AA, shift=4)
            _ink_onto(frame, mask, 0.95)

        # the first and last places: a ring and the name, lying on the map
        for sprites, at, px, z_then, appear, below in self.labels:
            alpha = _ease((t - appear) / 0.6)
            if alpha <= 0:
                continue
            scale = local(at) / z_then
            pulse = 1 + 0.3 * math.sin((t - appear) * 4) * (appear > 1)  # the destination breathes
            mask = np.zeros((H, W), np.uint8)
            _stroke(mask, [screen(_circle(at[0], at[1], 14 * pulse / z_then))], [3 * scale])
            _ink_onto(frame, mask, 0.95 * alpha)
            font_map = px / z_then
            # north of the point, or south of it when a picture is pinned above
            spot, anchor = (at + [0, 18 / z_then], (0.5, 0.0)) if below else (at - [0, 22 / z_then], (0.5, 1.0))
            need = font_map * local(at)
            key = next((k for k in sorted(sprites) if k >= need), max(sprites))
            halo, ink = sprites[key]
            m = font_map / key
            h, w = halo.shape[:2]
            to_frame = to_screen @ np.array([[m, 0, spot[0] - anchor[0] * w * m], [0, m, spot[1] - anchor[1] * h * m], [0, 0, 1]])
            _warp_onto(frame, halo, to_frame, alpha * 0.8)
            _warp_onto(frame, ink, to_frame, alpha, ink=INK)

        # pictures of places, standing up, pinned with a leader lying on the map
        for sprites, ring, at, offset, z_then, appear, leave in self.pinned:
            grow = _ease_out_back((t - appear) / 0.55)
            alpha = _ease((t - appear) / 0.25)
            if leave is not None:
                fade = _ease((t - leave) / 0.5)
                alpha *= 1 - fade
                grow *= 1 - 0.15 * fade
            if alpha <= 0:
                continue
            scale = local(at) / z_then
            px, py = screen(at)
            mx, my = screen(at + offset)
            d = MEDALLION_PX * scale * grow
            direction = np.array([mx - px, my - py])
            edge = np.array([mx, my]) - direction / max(np.linalg.norm(direction), 1e-6) * d * ring
            back = np.linalg.inv(to_screen) @ np.array([edge[0], edge[1], 1.0])
            self._leader_on_map(frame, to_screen, local(at), at, back[:2] / back[2], alpha)
            _place_sprite(frame, sprites, d, mx, my, alpha)

        return (frame.astype(np.float32) * self.shade).astype(np.uint8)

    def _leader_on_map(self, frame, to_screen, local_zoom, foot_map, top_map, alpha):
        ladder, foot, top = self.leader
        length = float(np.linalg.norm(top_map - foot_map))
        if length < 1e-6:
            return
        k = length / (foot[1] - top[1])  # map px per sprite px
        u = (top_map - foot_map) / length
        ax, ay = np.array([-u[1], u[0]]) * k, -u * k
        base = min((b for b in ladder if b >= k * local_zoom), default=1.0)
        t = foot_map - foot[0] * ax - foot[1] * ay
        to_map = np.array([[ax[0], ay[0], t[0]], [ax[1], ay[1], t[1]], [0, 0, 1]]) @ np.diag([1 / base, 1 / base, 1])
        _warp_onto(frame, ladder[base], to_screen @ to_map, alpha)


def render_clip(shot: MapShot, frames: int, factor: int, out: Path) -> None:
    """The shot as a video segment of `frames` frames on the 24 fps clock, at
    `factor` times that rate - encoded like every other segment, to be joined
    with them without re-encoding."""
    plan = _Shot(shot, frames, factor)
    rate = f"{FPS_NUM * factor}/{FPS_DEN}"
    process = subprocess.Popen(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24",
         "-s", f"{W}x{H}", "-r", rate, "-i", "-", "-frames:v", str(plan.n), "-an",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
         "-r", rate, "-video_track_timescale", str(FPS_NUM * factor), str(out)],
        stdin=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        for i in range(plan.n):
            process.stdin.write(plan.frame(i).tobytes())
    finally:
        process.stdin.close()
        errors = process.stderr.read().decode(errors="replace")
        process.wait()
    if process.returncode != 0:
        raise RuntimeError(f"ffmpeg failed on a map shot: {errors.strip()[-600:]}")


def still(shot: MapShot, frames: int, share: float = 0.5) -> np.ndarray:
    """One frame of the shot, `share` of the way through (0..1), as a BGR image."""
    plan = _Shot(shot, frames, 1)
    return plan.frame(min(plan.n - 1, int(round(share * (plan.n - 1)))))


# --- pictures of places -----------------------------------------------------------

PICTURE_MODEL = "gpt-image-2"
PICTURE_PROMPT = (
    "The attached image is a piece of a hand-drawn map; use it only as the style reference. "
    "Draw a small illustration of {what}, in exactly the same style as the map: dark sepia-brown ink linework and "
    "hatching on the same aged cream parchment, the same restrained palette, drawn by the same hand - like a vignette "
    "the cartographer added in the margin of this map. A three-quarter view, the subject centred and filling the "
    "middle of the picture so that it reads inside a circle. No text, no lettering, no labels, no border, no frame."
)


def generate_picture(map_name: str, place_id: str, what: str = "") -> dict:
    """Draws a picture of a place with GPT Image, in the map's style - a piece of the
    map around the place is the style reference - and files it as the place's
    picture. `what` describes the place; empty = its name. Paid (one image)."""
    from app.services.runtime_settings import current_openai_api_key

    key = current_openai_api_key()
    if not key:
        raise RuntimeError("Set the OpenAI API key in Settings - it draws the pictures of places")
    by_id = places(map_name)
    place = by_id.get(place_id)
    if place is None:
        raise ValueError(f"No place {place_id!r} on the map")
    paper = _picture(map_name).levels[0]
    x, y = int(place["x"]), int(place["y"])
    ok, png = cv2.imencode(".png", paper[max(0, y - 450) : y + 450, max(0, x - 450) : x + 450])
    subject = what.strip() or f"{place['name']} in Middle-earth"
    boundary = uuid.uuid4().hex
    fields = {"model": PICTURE_MODEL, "prompt": PICTURE_PROMPT.format(what=subject), "size": "1024x1024", "quality": "high", "n": "1"}
    body = b"".join(
        f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode() for k, v in fields.items()
    )
    body += f'--{boundary}\r\nContent-Disposition: form-data; name="image[]"; filename="map.png"\r\nContent-Type: image/png\r\n\r\n'.encode()
    body += png.tobytes() + f"\r\n--{boundary}--\r\n".encode()
    request = urllib.request.Request(
        "https://api.openai.com/v1/images/edits", data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=400) as response:
            data = json.load(response)
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"GPT Image: {error.code} {error.read().decode(errors='replace')[:400]}")
    folder = map_dir(map_name) / "places"
    folder.mkdir(exist_ok=True)
    name = f"{place_id}.png"
    (folder / name).write_bytes(base64.b64decode(data["data"][0]["b64_json"]))
    with _lock:
        by_id = places(map_name)
        by_id[place_id]["picture"] = name
        _save_places(map_name, by_id)
    _medallion.cache_clear()
    return by_id[place_id]
