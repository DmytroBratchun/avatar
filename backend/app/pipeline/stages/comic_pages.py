from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import stored_path
from app.db.models import Scene, Source
from app.services.comics import render_pages


def run_comic_pages(db: Session, source: Source) -> None:
    """The comic's counterpart of transcription, shot detection, scene grouping and
    keyframes in one: every page of the PDF becomes a scene with the page's image as
    its keyframe. Free and local. Skips if the pages are already there."""
    if db.execute(select(Scene.id).where(Scene.source_id == source.id)).first() is not None:
        return

    source.status = "extracting_keyframes"
    db.commit()

    def report(done: int, total: int) -> None:
        source.current_stage_detail = f"rendering pages {done}/{total}"
        db.commit()

    paths = render_pages(source.file_path, source.id, report)
    for index, path in enumerate(paths):
        db.add(
            Scene(
                source_id=source.id,
                show_title=source.show_title,
                book=source.book,
                episode_number=source.episode_number,
                # a page has no time: its place is its number, counted from 0
                start_time=float(index),
                end_time=float(index + 1),
                keyframe_paths=[stored_path(path)],
            )
        )
    source.duration_seconds = None
    db.commit()
