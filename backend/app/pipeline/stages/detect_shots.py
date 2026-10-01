from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Shot, Source


def run_shot_detection(db: Session, source: Source) -> None:
    existing = db.execute(select(Shot).where(Shot.source_id == source.id)).first()
    if existing:
        return

    source.status = "detecting_shots"
    db.commit()

    from scenedetect import ContentDetector, SceneManager, open_video

    video = open_video(source.file_path)
    manager = SceneManager()
    manager.add_detector(ContentDetector(threshold=22.0))
    manager.detect_scenes(video, show_progress=False)
    shot_list = manager.get_scene_list()

    if not shot_list:
        duration = source.duration_seconds or video.duration.get_seconds()
        db.add(Shot(source_id=source.id, start_time=0.0, end_time=duration, shot_index=0))
        db.flush()
        return

    for i, (start, end) in enumerate(shot_list):
        db.add(
            Shot(
                source_id=source.id,
                start_time=start.get_seconds(),
                end_time=end.get_seconds(),
                shot_index=i,
            )
        )
    db.flush()
