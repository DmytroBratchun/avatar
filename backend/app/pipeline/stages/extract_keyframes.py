import subprocess

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import KEYFRAMES_DIR, settings
from app.db.models import Scene, Source


MAX_KEYFRAME_DIMENSION = 768


def _extract_frame(video_path: str, timestamp: float, out_path: str) -> None:
    # downscale to MAX_KEYFRAME_DIMENSION on the long edge (never upscale) - both
    # Claude and local VLMs pay per vision-token roughly proportional to pixel
    # count. 768px long edge (~0.44MP) stays well above Qwen3-VL's documented
    # ~262K px quality floor and far under Anthropic's 1.15MP/1568px cap, while
    # roughly halving vision-token cost vs sending source resolution.
    scale_filter = (
        f"scale='if(gt(iw,ih),min(iw,{MAX_KEYFRAME_DIMENSION}),-2)':"
        f"'if(gt(iw,ih),-2,min(ih,{MAX_KEYFRAME_DIMENSION}))'"
    )
    subprocess.run(
        [
            "ffmpeg", "-y", "-ss", f"{timestamp:.3f}", "-i", video_path,
            "-frames:v", "1", "-vf", scale_filter, "-q:v", "2", out_path,
        ],
        check=True,
        capture_output=True,
    )


_OFFSETS_BY_COUNT = {1: [0.5], 2: [0.25, 0.75], 3: [0.2, 0.5, 0.8]}


def _keyframe_count_for_duration(duration: float) -> int:
    if duration < settings.keyframe_short_scene_seconds:
        return 1
    if duration < settings.keyframe_medium_scene_seconds:
        return min(2, settings.keyframes_per_scene)
    return settings.keyframes_per_scene


def run_keyframe_extraction(db: Session, source: Source) -> None:
    scenes = list(
        db.execute(
            select(Scene).where(Scene.source_id == source.id, Scene.keyframe_paths == [])
        ).scalars()
    )
    if not scenes:
        return

    source.status = "extracting_keyframes"
    db.commit()

    total = len(scenes)
    for i, scene in enumerate(scenes):
        duration = scene.end_time - scene.start_time
        count = _keyframe_count_for_duration(duration)
        offsets = _OFFSETS_BY_COUNT[count]
        paths = []
        for j, offset in enumerate(offsets):
            timestamp = scene.start_time + duration * offset
            out_path = str(KEYFRAMES_DIR / f"scene_{scene.id}_{j}.jpg")
            _extract_frame(source.file_path, timestamp, out_path)
            paths.append(out_path)
        scene.keyframe_paths = paths
        source.current_stage_detail = f"extracting keyframes {i + 1}/{total}"
        db.commit()
