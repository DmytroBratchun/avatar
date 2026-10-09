"""Builds the free text index from fan-wiki transcripts: gives every scene the wiki's
account of it (Scene.wiki_description, see app/services/wiki_descriptions.py) and
embeds that text into scene_wiki_vec. Run after scripts/import_wiki_transcripts.py
(episodes) or scripts/import_wiki_comics.py (comics, where a scene is a page).

Scenes that already have an up-to-date vector are skipped, so it can be stopped and
resumed. Captions and their vectors are not touched.

Usage (from backend/):
    .venv/Scripts/python scripts/index_wiki_descriptions.py                 # preview: how many scenes get a text
    .venv/Scripts/python scripts/index_wiki_descriptions.py --apply          # write texts and vectors
    .venv/Scripts/python scripts/index_wiki_descriptions.py --apply --captioned-only   # only episodes that have captions
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select, text

from app.db.models import Scene, Source
from app.db.session import SessionLocal, init_db
from app.services.wiki_descriptions import scene_texts
from app.vectorstore.sqlite_vec_client import upsert_wiki_vector

BATCH = 32


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Write changes (default: dry-run preview)")
    parser.add_argument("--captioned-only", action="store_true", help="Only episodes that have captioned scenes")
    args = parser.parse_args()

    init_db()
    db = SessionLocal()
    try:
        stmt = select(Source).where(Source.wiki_page.isnot(None)).order_by(Source.show_title, Source.book, Source.episode_number)
        sources = list(db.execute(stmt).scalars())
        if args.captioned_only:
            captioned = set(db.execute(select(Scene.source_id).where(Scene.caption_status == "done").distinct()).scalars())
            sources = [s for s in sources if s.id in captioned]

        has_vector = {row[0] for row in db.execute(text("SELECT scene_id FROM scene_wiki_vec")).all()}
        todo: list[tuple[Scene, str]] = []
        stale: list[Scene] = []  # had a text before, get none now
        total_scenes = with_text = 0
        for source in sources:
            texts = scene_texts(db, source.id)
            scenes = list(db.execute(select(Scene).where(Scene.source_id == source.id)).scalars())
            total_scenes += len(scenes)
            with_text += len(texts)
            for scene in scenes:
                new_text = texts.get(scene.id)
                if new_text and (scene.wiki_description != new_text or scene.id not in has_vector):
                    todo.append((scene, new_text))
                elif not new_text and (scene.wiki_description or scene.id in has_vector):
                    stale.append(scene)
        print(f"{len(sources)} episodes and comics, {total_scenes} scenes; {with_text} get a wiki text ({with_text / max(total_scenes, 1):.0%}); {len(todo)} to embed, {len(stale)} to remove from the index")
        if not args.apply:
            print("Preview only - nothing was changed. Run again with --apply.")
            return

        for scene in stale:
            scene.wiki_description = None
            db.execute(text("DELETE FROM scene_wiki_vec WHERE scene_id = :id"), {"id": scene.id})
        db.commit()
        if not todo:
            return

        from app.services.embeddings import _text_model

        model = _text_model()
        print("embedding on", model.device, flush=True)
        started = time.time()
        for offset in range(0, len(todo), BATCH):
            batch = todo[offset : offset + BATCH]
            vectors = model.encode([t for _, t in batch], normalize_embeddings=True, batch_size=BATCH)
            for (scene, new_text), vector in zip(batch, vectors):
                scene.wiki_description = new_text
                upsert_wiki_vector(db, scene.id, vector.tolist())
            db.commit()
            done = offset + len(batch)
            if done % (BATCH * 10) == 0 or done == len(todo):
                rate = done / max(time.time() - started, 1e-6)
                print(f"  {done}/{len(todo)}  ({rate:.1f} scenes/s, {(len(todo) - done) / max(rate, 1e-6) / 60:.0f} min left)", flush=True)
    finally:
        db.close()


if __name__ == "__main__":
    main()
