"""Measures how well the free wiki-description index finds scenes, next to the paid
caption index and the visual index. Read-only.

There is no hand-made set of "right answers", so paid captions stand in for queries:
each captioned scene's caption is searched for in the other indexes, and the search
counts as a hit when it returns that scene or one right next to it (within
NEAR_SECONDS in the same episode - scenes are short and neighbours show the same
moment). A caption finds itself in the caption index, so that index is scored the
other way round, with wiki texts as the queries.

Usage (from backend/):
    .venv/Scripts/python scripts/compare_wiki_index.py [--sample 400]
"""

import argparse
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
from sqlalchemy import select, text

from app.db.models import Scene
from app.db.session import SessionLocal, init_db
from app.services.embeddings import _text_model, embed_text_for_visual_search

NEAR_SECONDS = 15.0
KS = (1, 5, 10, 20)


def load(db, table: str, ids: set[int]) -> tuple[np.ndarray, np.ndarray]:
    rows = [r for r in db.execute(text(f"SELECT scene_id, embedding FROM {table}")).all() if r[0] in ids]
    matrix = np.frombuffer(b"".join(r[1] for r in rows), dtype=np.float32).reshape(len(rows), -1)
    return np.array([r[0] for r in rows]), matrix


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample", type=int, default=400)
    args = parser.parse_args()
    init_db()
    db = SessionLocal()

    scenes = list(
        db.execute(select(Scene).where(Scene.caption_status == "done", Scene.wiki_description.isnot(None))).scalars()
    )
    episodes = {s.source_id for s in scenes}
    pool = {s.id: s for s in db.execute(select(Scene).where(Scene.source_id.in_(episodes))).scalars()}
    random.seed(7)
    sample = random.sample(scenes, min(args.sample, len(scenes)))
    print(f"{len(scenes)} scenes have both a caption and a wiki text, in {len(episodes)} episodes; testing {len(sample)} of them")
    print(f"each search looks through all {len(pool)} scenes of those episodes\n")

    def near(found_id: int, target: Scene) -> bool:
        found = pool[found_id]
        return found.source_id == target.source_id and found.start_time < target.end_time + NEAR_SECONDS and found.end_time > target.start_time - NEAR_SECONDS

    def recall(ids: np.ndarray, matrix: np.ndarray, queries: np.ndarray) -> dict[int, float]:
        order = np.argsort(-(queries @ matrix.T), axis=1)[:, : max(KS)]
        hits = {k: 0 for k in KS}
        for target, row in zip(sample, order):
            ranks = [n for n, column in enumerate(row) if near(int(ids[column]), target)]
            for k in KS:
                hits[k] += bool(ranks and ranks[0] < k)
        return {k: hits[k] / len(sample) for k in KS}

    model = _text_model()
    caption_queries = model.encode([s.context_description for s in sample], normalize_embeddings=True, batch_size=32)
    wiki_queries = model.encode([s.wiki_description for s in sample], normalize_embeddings=True, batch_size=32)
    visual_queries = np.array([embed_text_for_visual_search(s.visual_description or s.context_description) for s in sample], dtype=np.float32)

    results = {
        "caption text -> wiki index (free)": recall(*load(db, "scene_wiki_vec", set(pool)), caption_queries),
        "wiki text -> caption index (paid)": recall(*load(db, "scene_text_vec", set(pool)), wiki_queries),
        "visual description -> visual index": recall(*load(db, "scene_visual_vec", set(pool)), visual_queries),
    }
    print(f"{'query -> index':38}" + "".join(f"top {k:<5}" for k in KS))
    for name, scores in results.items():
        print(f"{name:38}" + "".join(f"{scores[k]:<9.0%}" for k in KS))
    print(f"\nA hit = the scene itself or one within {NEAR_SECONDS:.0f}s of it in the same episode.")
    db.close()


if __name__ == "__main__":
    main()
