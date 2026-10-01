"""Finds footage for slots by searching the scene index (read-only).

Smart hybrid: every slot has a narrative query (for the caption text index) and a
frame-description query (for the visual index). Both are searched and the results
merged. Raw distances from the two indexes aren't comparable, and "the best text
hit" can still be a poor match, so each hit is scored by how much closer it is to
the query than a random scene from the same index (a z-score). That is comparable
across indexes and across queries.
"""

import random
import statistics

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db.models import Scene, Shot, Source
from app.editor.store import Candidate, Project, Slot, project_shows
from app.pipeline.stages.extract_keyframes import _OFFSETS_BY_COUNT
from app.services.embeddings import embed_text, embed_text_for_visual_search
from app.vectorstore.sqlite_vec_client import _serialize, knn_text, knn_visual

MAX_CLIP_SECONDS = 5.0
KNN_K = 30
CANDIDATES_PER_SLOT = 8
BACKGROUND_SAMPLE = 400
# a scene right next to one already used looks like the same footage again
NEIGHBOUR_SECONDS = 20.0
NEIGHBOUR_PENALTY = 1.0
# both indexes agreeing is worth a little more than either alone
AGREEMENT_BONUS = 0.25


class SearchContext:
    """Per-run state: which scenes are searchable for this project, and a fixed
    random sample of each index to measure 'a random scene' against."""

    def __init__(self, db: Session, project: Project):
        self.db = db
        self.mode = project.settings.search_mode
        self.candidate_ids: list[int] | None = None
        shows = project_shows(project)
        if shows:
            self.candidate_ids = list(
                db.execute(select(Scene.id).where(Scene.show_title.in_(shows))).scalars()
            )

        self.project_shows = shows
        self._by_shows: dict[tuple[str, ...], list[int] | None] = {}

        rng = random.Random(0)
        self.text_sample = self._sample(rng, Scene.text_embedding_model)
        self.visual_sample = self._sample(rng, Scene.visual_embedding_model)

    def candidates_for(self, shows: list[str]) -> list[int] | None:
        """Scene ids a clip may come from: the shows chosen for that clip, limited to
        the ones the project allows. No (valid) choice = the whole project."""
        wanted = tuple(sorted(s for s in shows if not self.project_shows or s in self.project_shows))
        if not wanted:
            return self.candidate_ids
        if wanted not in self._by_shows:
            self._by_shows[wanted] = list(
                self.db.execute(select(Scene.id).where(Scene.show_title.in_(wanted))).scalars()
            )
        return self._by_shows[wanted] or self.candidate_ids

    def _sample(self, rng: random.Random, model_column) -> list[int]:
        stmt = select(Scene.id).where(model_column.isnot(None))
        if self.candidate_ids is not None:
            stmt = stmt.where(Scene.id.in_(self.candidate_ids))
        ids = list(self.db.execute(stmt).scalars())
        return rng.sample(ids, min(BACKGROUND_SAMPLE, len(ids)))


def _background(db: Session, table: str, sample_ids: list[int], vector: list[float]) -> tuple[float, float] | None:
    if len(sample_ids) < 20:
        return None
    placeholders = ",".join(str(int(i)) for i in sample_ids)
    rows = db.execute(
        text(f"SELECT vec_distance_L2(embedding, :vec) FROM {table} WHERE scene_id IN ({placeholders})"),
        {"vec": _serialize(vector)},
    ).all()
    distances = [row[0] for row in rows]
    spread = statistics.pstdev(distances)
    if spread == 0:
        return None
    return statistics.fmean(distances), spread


def _scored(hits: list[tuple[int, float]], background: tuple[float, float] | None) -> dict[int, float]:
    if background is None:
        return {}
    mean, spread = background
    return {scene_id: (mean - distance) / spread for scene_id, distance in hits}


def _clip_window(db: Session, scene: Scene, source: Source, duration: float, anchor_on_keyframe: bool) -> tuple[float, float]:
    """Where in the source to cut `duration` seconds for this scene. A visual match
    is a match on one keyframe, so the clip is taken around that frame rather than
    the scene's centre; it is kept inside a single shot when the shot is long enough."""
    scene_length = scene.end_time - scene.start_time
    offsets = _OFFSETS_BY_COUNT.get(len(scene.keyframe_paths or []))
    if anchor_on_keyframe and offsets:
        anchor = scene.start_time + scene_length * offsets[0]
    else:
        anchor = scene.start_time + scene_length / 2

    shot = db.execute(
        select(Shot).where(Shot.source_id == scene.source_id, Shot.start_time <= anchor, Shot.end_time > anchor)
    ).scalars().first()
    low, high = scene.start_time, scene.end_time
    if shot is not None and shot.end_time - shot.start_time >= duration:
        low, high = shot.start_time, shot.end_time

    start = anchor - duration / 2
    start = max(low, min(start, high - duration))
    # a scene shorter than the slot runs on into what follows it in the episode
    source_end = source.duration_seconds or scene.end_time
    start = max(0.0, min(start, source_end - duration))
    return round(start, 3), round(start + duration, 3)


def search_slot(ctx: SearchContext, slot: Slot) -> list[Candidate]:
    """Ranked candidates for one slot, best first (no reuse handling here)."""
    db = ctx.db
    text_scores: dict[int, float] = {}
    visual_scores: dict[int, float] = {}
    candidate_ids = ctx.candidates_for(slot.shows)

    if ctx.mode in ("smart_hybrid", "text") and slot.text_query.strip():
        vector = embed_text(slot.text_query)
        hits = knn_text(db, vector, candidate_ids, k=KNN_K)
        text_scores = _scored(hits, _background(db, "scene_text_vec", ctx.text_sample, vector))
    if ctx.mode in ("smart_hybrid", "visual") and slot.visual_query.strip():
        vector = embed_text_for_visual_search(slot.visual_query)
        hits = knn_visual(db, vector, candidate_ids, k=KNN_K)
        visual_scores = _scored(hits, _background(db, "scene_visual_vec", ctx.visual_sample, vector))

    combined: dict[int, float] = {}
    for scene_id in text_scores.keys() | visual_scores.keys():
        scores = [s for s in (text_scores.get(scene_id), visual_scores.get(scene_id)) if s is not None]
        combined[scene_id] = max(scores) + (AGREEMENT_BONUS * min(scores) if len(scores) == 2 else 0.0)

    ranked = sorted(combined, key=combined.get, reverse=True)[: CANDIDATES_PER_SLOT * 3]
    if not ranked:
        return []

    rows = db.execute(select(Scene, Source).join(Source, Scene.source_id == Source.id).where(Scene.id.in_(ranked))).all()
    by_id = {scene.id: (scene, source) for scene, source in rows}
    duration = slot.end - slot.start

    candidates: list[Candidate] = []
    for scene_id in ranked:
        if scene_id not in by_id:
            continue
        scene, source = by_id[scene_id]
        text_score = text_scores.get(scene_id)
        visual_score = visual_scores.get(scene_id)
        # cut around the keyframe unless the caption was the stronger reason for the match
        on_keyframe = visual_score is not None and (text_score is None or visual_score >= text_score)
        clip_start, clip_end = _clip_window(db, scene, source, duration, on_keyframe)
        candidates.append(
            Candidate(
                scene_id=scene.id,
                source_id=source.id,
                file_path=source.file_path,
                show_title=source.show_title,
                book=source.book,
                episode_number=source.episode_number,
                scene_start=scene.start_time,
                scene_end=scene.end_time,
                clip_start=clip_start,
                clip_end=clip_end,
                description=scene.context_description,
                text_score=round(text_score, 2) if text_score is not None else None,
                visual_score=round(visual_score, 2) if visual_score is not None else None,
                score=round(combined[scene_id], 2),
            )
        )
    return candidates


class Usage:
    """Footage already on screen earlier in the video."""

    def __init__(self) -> None:
        self.scene_ids: set[int] = set()
        self.clips: list[tuple[int, float]] = []  # (source_id, clip_start)

    def add(self, candidate: Candidate) -> None:
        self.scene_ids.add(candidate.scene_id)
        self.clips.append((candidate.source_id, candidate.clip_start))

    def adjusted(self, candidate: Candidate) -> float | None:
        """Score after reuse penalties; None = already used, don't pick."""
        if candidate.scene_id in self.scene_ids:
            return None
        near = any(
            source_id == candidate.source_id and abs(start - candidate.clip_start) < NEIGHBOUR_SECONDS
            for source_id, start in self.clips
        )
        return candidate.score - (NEIGHBOUR_PENALTY if near else 0.0)


def choose(slot: Slot, ranked: list[Candidate], usage: Usage) -> None:
    """Keeps the best CANDIDATES_PER_SLOT for review and selects the best one not
    already used. Falls back to a used one only if nothing else exists."""
    fresh = [(usage.adjusted(c), c) for c in ranked]
    fresh = sorted(((s, c) for s, c in fresh if s is not None), key=lambda pair: pair[0], reverse=True)
    ordered = [c for _, c in fresh] + [c for c in ranked if c.scene_id in usage.scene_ids]
    slot.candidates = ordered[:CANDIDATES_PER_SLOT]
    slot.selected = 0 if slot.candidates else None
    if slot.candidates:
        usage.add(slot.candidates[0])
