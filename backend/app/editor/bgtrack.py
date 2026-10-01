"""Builds the background of the framed layout as one continuous low-resolution
video: scenery shots at normal speed, cross-fading into each other.

The track is assembled from small independent files - the part of each shot that
plays alone, and each cross-fade between two shots - so every piece is a simple
ffmpeg call that can be cached and run in parallel, however long the video is.
Blur and darkening are applied later, when the track is composited under a clip,
so changing them doesn't rebuild the track.
"""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from app.editor.store import Background

FPS_NUM, FPS_DEN = 24000, 1001
FPS = FPS_NUM / FPS_DEN
TRACK_WIDTH, TRACK_HEIGHT = 480, 270  # quarter of the output; it is only ever shown blurred
FADE_FRAMES = 19  # ~0.8s cross-fade
MIN_FRAMES = 2 * FADE_FRAMES + 12  # a shot must have something left between its two fades
MAX_FRAMES = round(9 * FPS)
TRACK_VERSION = 3


@dataclass
class Placed:
    clip: Background
    start: int  # frame on the video timeline where this shot starts fading in
    frames: int  # frames of the shot used, including both fades
    hold: int = 0  # extra frames holding the last picture (only ever the final shot)


def clip_frames(clip: Background, speed: float = 1.0) -> int:
    """Frames of screen time a shot gives when played at `speed` (1 = normal)."""
    return min(MAX_FRAMES, int((clip.clip_end - clip.clip_start) / speed * FPS))


def plan(ranges: list[tuple[int, int]], scenery: list[list[Background]], total: int, speed: float = 1.0) -> list[Placed]:
    """Lays scenery shots end to end over the video timeline. `ranges` are the frame
    ranges of the beats and `scenery` the shots chosen for each; a beat's shots play
    one after another until its range is covered, the last one cut so the fade into
    the next beat's scenery lands on the beat boundary."""
    placed: list[Placed] = []
    position = 0
    for (_, end), clips in zip(ranges, scenery):
        usable = [c for c in clips if clip_frames(c, speed) >= MIN_FRAMES]
        if not usable:
            continue  # no scenery for this beat: the neighbours' shots run on over it
        n = 0
        while end - position >= MIN_FRAMES:
            clip = usable[n % len(usable)]  # more time than shots (e.g. a long quote): go round again
            n += 1
            frames = min(clip_frames(clip, speed), end - position)
            placed.append(Placed(clip, position, frames))
            position += frames - FADE_FRAMES

    if placed:
        last = placed[-1]
        covered = last.start + last.frames
        if covered < total:
            last.hold = total - covered
        elif covered > total:
            last.frames -= covered - total
    return placed


def slice_identity(placed: list[Placed], start: int, frames: int, speed: float = 1.0) -> list:
    """What the track shows during [start, start+frames), in terms that don't change
    when an unrelated part of the video does - used in a clip's cache key."""
    return [
        [p.clip.file_path, p.clip.clip_start, p.start - start, p.frames, p.hold, speed]
        for p in placed
        if p.start < start + frames and p.start + p.frames + p.hold > start
    ]


def _prep(crop: tuple[int, int, int, int], speed: float) -> str:
    cw, ch, cx, cy = crop
    chain = (
        f"crop={cw}:{ch}:{cx}:{cy},"
        f"scale={TRACK_WIDTH}:{TRACK_HEIGHT}:force_original_aspect_ratio=increase,"
        f"crop={TRACK_WIDTH}:{TRACK_HEIGHT},setsar=1,"
    )
    if speed < 1.0:
        # Slow motion: stretch the timestamps, then blend neighbouring frames into the
        # missing ones. Plain frame repetition stutters on pans; blending is soft, which
        # is fine for something that is shown blurred anyway.
        return chain + f"setpts=(PTS-STARTPTS)/{speed:.4f},minterpolate=fps={FPS_NUM}/{FPS_DEN}:mi_mode=blend"
    return chain + f"fps={FPS_NUM}/{FPS_DEN},setpts=PTS-STARTPTS"


def _encode(frames: int) -> list[str]:
    # short GOP: clips seek into this track, and a seek decodes from the previous keyframe
    return [
        "-frames:v", str(frames), "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-g", "12",
        "-pix_fmt", "yuv420p", "-r", f"{FPS_NUM}/{FPS_DEN}", "-video_track_timescale", str(FPS_NUM),
    ]


def pieces(placed: list[Placed], source_crop, parts_dir: Path, speed: float = 1.0) -> list[tuple[Path, list[str]]]:
    """(file, ffmpeg args) for every piece of the track, in playing order."""
    result: list[tuple[Path, list[str]]] = []

    def piece(identity: dict, args: list[str]) -> None:
        name = hashlib.sha1(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:16]
        result.append((parts_dir / f"{name}.mp4", args))

    for i, p in enumerate(placed):
        crop = source_crop(p.clip.file_path)
        head = FADE_FRAMES if i > 0 else 0
        tail = FADE_FRAMES if i < len(placed) - 1 else 0
        alone = p.frames - head - tail + p.hold
        graph = _prep(crop, speed) + (f",tpad=stop_mode=clone:stop={p.hold}" if p.hold else "")
        piece(
            {"v": TRACK_VERSION, "alone": [p.clip.file_path, p.clip.clip_start, head, alone, p.hold], "crop": crop, "speed": speed},
            ["-ss", f"{p.clip.clip_start + head / FPS * speed:.3f}", "-i", p.clip.file_path, "-vf", graph, *_encode(alone)],
        )
        if tail:
            following = placed[i + 1]
            out_start = p.clip.clip_start + (p.frames - FADE_FRAMES) / FPS * speed
            next_crop = source_crop(following.clip.file_path)
            graph = (
                f"[0:v]{_prep(crop, speed)}[a];[1:v]{_prep(next_crop, speed)}[b];"
                f"[a][b]xfade=transition=fade:duration={FADE_FRAMES / FPS:.5f}:offset=0[out]"
            )
            piece(
                {
                    "v": TRACK_VERSION,
                    "fade": [p.clip.file_path, round(out_start, 3), following.clip.file_path, following.clip.clip_start],
                    "crop": [crop, next_crop],
                    "speed": speed,
                },
                [
                    "-ss", f"{out_start:.3f}", "-i", p.clip.file_path,
                    "-ss", f"{following.clip.clip_start:.3f}", "-i", following.clip.file_path,
                    "-filter_complex", graph, "-map", "[out]", *_encode(FADE_FRAMES),
                ],
            )
    return result
