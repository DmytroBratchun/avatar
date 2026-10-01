from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import Scene, SceneShot, Shot, Source, TranscriptLine


def _transcript_gap_at(lines: list[TranscriptLine], boundary_time: float) -> float | None:
    """Gap (seconds) between the transcript line ending before boundary_time and
    the one starting after it. None if there's no dialogue on one side (can't tell)."""
    before = [line for line in lines if line.end_time <= boundary_time]
    after = [line for line in lines if line.start_time >= boundary_time]
    if not before or not after:
        return None
    return after[0].start_time - before[-1].end_time


def run_scene_grouping(db: Session, source: Source) -> None:
    existing = db.execute(select(Scene).where(Scene.source_id == source.id)).first()
    if existing:
        return

    source.status = "grouping_scenes"
    db.commit()

    shots = list(
        db.execute(select(Shot).where(Shot.source_id == source.id).order_by(Shot.shot_index)).scalars()
    )
    if not shots:
        return

    lines = list(
        db.execute(
            select(TranscriptLine).where(TranscriptLine.source_id == source.id).order_by(TranscriptLine.start_time)
        ).scalars()
    )

    groups: list[list[Shot]] = [[shots[0]]]
    for shot in shots[1:]:
        current_group = groups[-1]
        group_start = current_group[0].start_time
        group_duration = shot.end_time - group_start
        shot_duration = shot.end_time - shot.start_time

        force_split = group_duration > settings.scene_max_duration_seconds
        too_short_to_split = shot_duration < settings.shot_min_duration_seconds
        # a brief conversational pause shouldn't fragment the scene into tiny clips -
        # only allow a dialogue-gap split once the current scene has real substance
        group_long_enough = group_duration >= settings.scene_min_duration_seconds

        gap = _transcript_gap_at(lines, shot.start_time)
        dialogue_break = gap is not None and gap > settings.scene_gap_seconds

        if force_split or (dialogue_break and group_long_enough and not too_short_to_split):
            groups.append([shot])
        else:
            current_group.append(shot)

    for group in groups:
        start_time = group[0].start_time
        end_time = group[-1].end_time
        linked_lines = [line for line in lines if line.start_time < end_time and line.end_time > start_time]

        scene = Scene(
            source_id=source.id,
            show_title=source.show_title,
            book=source.book,
            episode_number=source.episode_number,
            start_time=start_time,
            end_time=end_time,
            linked_transcript_ids=[line.id for line in linked_lines],
            caption_status="pending",
        )
        db.add(scene)
        db.flush()

        for order, shot in enumerate(group):
            db.add(SceneShot(scene_id=scene.id, shot_id=shot.id, shot_order=order))

    db.flush()
