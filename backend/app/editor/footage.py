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
from app.editor.comic_pages import page_candidate
from app.editor.store import Candidate, Project, Slot, project_comics, project_shows
from app.pipeline.stages.extract_keyframes import _OFFSETS_BY_COUNT
from app.services.embeddings import embed_text, embed_text_for_visual_search
from app.vectorstore.sqlite_vec_client import _serialize, knn_text, knn_visual, knn_wiki

# A piece of a shot shorter than this inside a clip reads as a flicker, not as a shot.
MIN_FRAGMENT_SECONDS = 0.8
# how far a clip may run longer or shorter than its even share of the beat, to end on a cut
FLEX_SECONDS = 1.0
MIN_CLIP_SECONDS = 1.5
# a clip with a flicker in it loses this much per flicker against a clean one
SLIVER_PENALTY = 0.75
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
        self.comics = set(project_comics(project))
        self._by_shows: dict[tuple[str, ...], list[int] | None] = {}

        rng = random.Random(0)
        self.text_sample = self._sample(rng, Scene.text_embedding_model)
        self.wiki_sample = self._sample(rng, Scene.wiki_description)
        self.visual_sample = self._sample(rng, Scene.visual_embedding_model)

    def candidates_for(self, shows: list[str]) -> list[int] | None:
        """Scene ids a clip may come from: the shows chosen for that clip, limited to
        the ones the project allows. No (valid) choice = the whole project."""
        wanted = tuple(sorted(s for s in shows if not self.project_shows or s in self.project_shows or s in self.comics))
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


def shot_fragments(cuts: list[float], start: float, end: float) -> list[float]:
    """Lengths of the pieces a clip [start, end] is cut into by the shot cuts inside it."""
    inner = [c for c in cuts if start + 1e-3 < c < end - 1e-3]
    edges = [start, *inner, end]
    return [later - earlier for earlier, later in zip(edges, edges[1:])]


def _cuts(db: Session, source_id: int, start: float, end: float) -> list[float]:
    """Shot cuts of a source between two times (with a little either side)."""
    shots = db.execute(
        select(Shot.start_time, Shot.end_time).where(
            Shot.source_id == source_id, Shot.end_time > start - 1, Shot.start_time < end + 1
        )
    ).all()
    return sorted({t for shot in shots for t in shot})


def count_slivers(db: Session, source_id: int, start: float, end: float) -> int:
    pieces = shot_fragments(_cuts(db, source_id, start, end), start, end)
    return sum(1 for piece in pieces if piece < MIN_FRAGMENT_SECONDS)


def fit_to_duration(db: Session, slot: Slot) -> None:
    """After a slot's length changes, every candidate keeps its start and gets the
    new length; their flicker counts are measured again."""
    duration = slot.end - slot.start
    for candidate in slot.candidates:
        candidate.clip_end = round(candidate.clip_start + duration, 3)
        if candidate.kind != "video":
            continue  # a comic page or a photo has no shots to cut across
        candidate.slivers = count_slivers(db, candidate.source_id, candidate.clip_start, candidate.clip_end)


def end_on_a_cut(db: Session, slot: Slot, following: Slot, max_clip_seconds: float) -> bool:
    """Clips in a beat share its time evenly, which is arbitrary - the footage has
    its own rhythm. If the chosen clip ends a moment before or after a cut, leaving a
    flicker, this moves the boundary between the two slots onto the cut: this clip
    runs a little longer or shorter and the next one makes up the difference.
    Returns True if the boundary moved."""
    if slot.selected is None:
        return False
    chosen = slot.candidates[slot.selected]
    duration = slot.end - slot.start
    if chosen.slivers == 0:
        return False
    room = following.end - slot.start  # this clip and the next one together
    best = None
    for cut in _cuts(db, chosen.source_id, chosen.clip_start, chosen.clip_start + duration + FLEX_SECONDS):
        length = cut - chosen.clip_start
        if abs(length - duration) > FLEX_SECONDS or length < MIN_CLIP_SECONDS:
            continue
        if not MIN_CLIP_SECONDS <= room - length <= max_clip_seconds + FLEX_SECONDS:
            continue
        slivers = count_slivers(db, chosen.source_id, chosen.clip_start, cut)
        key = (slivers, abs(length - duration))
        if slivers < chosen.slivers and (best is None or key < best[0]):
            best = (key, length)
    if best is None:
        return False
    slot.end = round(slot.start + best[1], 3)
    following.start = slot.end
    fit_to_duration(db, slot)
    return True


def _clip_window(
    db: Session, scene: Scene, source: Source, duration: float, anchor_on_keyframe: bool, anchor: float | None = None
) -> tuple[float, float]:
    """Where in the source to cut `duration` seconds for this scene.

    The clip has to contain the moment the scene was found by (a visual match is a
    match on one keyframe; for a caption match the middle of the scene stands in).
    Around that moment the window can slide, and where it sits matters: if it starts
    a few frames before a cut, the clip opens with a flash of the previous shot.
    So every sensible position is tried - centred on the moment, and starting or
    ending exactly on each nearby cut - and the one with no sliver of a shot wins,
    then the one with the fewest cuts, then the one that stays inside the scene and
    keeps the moment nearest its middle. anchor: the moment, when it is known (a
    specific moment found by reading the episode)."""
    scene_length = scene.end_time - scene.start_time
    offsets = _OFFSETS_BY_COUNT.get(len(scene.keyframe_paths or []))
    if anchor is None:
        if anchor_on_keyframe and offsets:
            anchor = scene.start_time + scene_length * offsets[0]
        else:
            anchor = scene.start_time + scene_length / 2

    source_end = source.duration_seconds or scene.end_time
    latest_start = max(0.0, source_end - duration)
    shots = db.execute(
        select(Shot.start_time, Shot.end_time).where(
            Shot.source_id == scene.source_id,
            Shot.end_time > anchor - duration,
            Shot.start_time < anchor + duration,
        )
    ).all()
    cuts = sorted({t for shot in shots for t in shot})

    # the moment itself must not sit right at the edge of the clip
    margin = min(MIN_FRAGMENT_SECONDS, duration / 4)
    earliest = max(0.0, anchor - duration + margin)
    latest = min(latest_start, anchor - margin)
    if latest < earliest:  # clip longer than the source allows around this moment
        start = max(0.0, min(anchor - duration / 2, latest_start))
        return round(start, 3), round(start + duration, 3)

    options = {min(max(anchor - duration / 2, earliest), latest)}
    options.update(c for c in cuts if earliest <= c <= latest)  # start on a cut
    options.update(c - duration for c in cuts if earliest <= c - duration <= latest)  # end on a cut

    def badness(start: float) -> tuple:
        pieces = shot_fragments(cuts, start, start + duration)
        slivers = sum(1 for piece in pieces if piece < MIN_FRAGMENT_SECONDS)
        outside = max(0.0, scene.start_time - start) + max(0.0, start + duration - scene.end_time)
        off_centre = abs(start + duration / 2 - anchor)
        return slivers, len(pieces), round(outside, 2), off_centre

    start = min(options, key=badness)
    return round(start, 3), round(start + duration, 3)


def search_slot(ctx: SearchContext, slot: Slot, scene_ids: list[int] | None = None) -> list[Candidate]:
    """Ranked candidates for one slot, best first (no reuse handling here).
    scene_ids: search only these scenes (e.g. the scenes of one episode)."""
    db = ctx.db
    text_scores: dict[int, float] = {}
    wiki_scores: dict[int, float] = {}
    visual_scores: dict[int, float] = {}
    candidate_ids = scene_ids if scene_ids is not None else ctx.candidates_for(slot.shows)
    # a shot written for a comic: pages are found by what happens on them, whatever
    # the project's search mode, and have no visual index to search
    in_comic = bool(slot.shows) and all(show in ctx.comics for show in slot.shows)

    if (ctx.mode in ("smart_hybrid", "text") or in_comic) and slot.text_query.strip():
        # the narrative query is searched in both text indexes: paid captions (few
        # episodes) and the fan-wiki descriptions (nearly all of them, free)
        vector = embed_text(slot.text_query)
        hits = knn_text(db, vector, candidate_ids, k=KNN_K)
        text_scores = _scored(hits, _background(db, "scene_text_vec", ctx.text_sample, vector))
        hits = knn_wiki(db, vector, candidate_ids, k=KNN_K)
        wiki_scores = _scored(hits, _background(db, "scene_wiki_vec", ctx.wiki_sample, vector))
    if ctx.mode in ("smart_hybrid", "visual") and slot.visual_query.strip() and not in_comic:
        vector = embed_text_for_visual_search(slot.visual_query)
        hits = knn_visual(db, vector, candidate_ids, k=KNN_K)
        visual_scores = _scored(hits, _background(db, "scene_visual_vec", ctx.visual_sample, vector))

    combined: dict[int, float] = {}
    for scene_id in text_scores.keys() | wiki_scores.keys() | visual_scores.keys():
        scores = sorted(
            (s for s in (text_scores.get(scene_id), wiki_scores.get(scene_id), visual_scores.get(scene_id)) if s is not None),
            reverse=True,
        )
        # the strongest signal decides; a second one agreeing adds a little
        combined[scene_id] = scores[0] + (AGREEMENT_BONUS * scores[1] if len(scores) > 1 else 0.0)

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
        wiki_score = wiki_scores.get(scene_id)
        visual_score = visual_scores.get(scene_id)
        best_text = max((s for s in (text_score, wiki_score) if s is not None), default=None)
        if source.kind == "comic":
            if scene.keyframe_paths:
                candidates.append(page_candidate(scene, source, duration, combined[scene_id], best_text))
            continue
        # cut around the keyframe unless a text index was the stronger reason for the match
        on_keyframe = visual_score is not None and (best_text is None or visual_score >= best_text)
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
                slivers=count_slivers(db, source.id, clip_start, clip_end),
                description=scene.context_description or scene.wiki_description,
                text_score=round(text_score, 2) if text_score is not None else None,
                wiki_score=round(wiki_score, 2) if wiki_score is not None else None,
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
        return candidate.score - (NEIGHBOUR_PENALTY if near else 0.0) - SLIVER_PENALTY * candidate.slivers


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
