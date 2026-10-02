import struct

from sqlalchemy import text
from sqlalchemy.orm import Session


def _serialize(vector: list[float]) -> bytes:
    return struct.pack(f"{len(vector)}f", *vector)


def upsert_text_vector(db: Session, scene_id: int, vector: list[float]) -> None:
    db.execute(text("DELETE FROM scene_text_vec WHERE scene_id = :id"), {"id": scene_id})
    db.execute(
        text("INSERT INTO scene_text_vec(scene_id, embedding) VALUES (:id, :vec)"),
        {"id": scene_id, "vec": _serialize(vector)},
    )


def upsert_visual_vector(db: Session, scene_id: int, vector: list[float]) -> None:
    db.execute(text("DELETE FROM scene_visual_vec WHERE scene_id = :id"), {"id": scene_id})
    db.execute(
        text("INSERT INTO scene_visual_vec(scene_id, embedding) VALUES (:id, :vec)"),
        {"id": scene_id, "vec": _serialize(vector)},
    )


def upsert_wiki_vector(db: Session, scene_id: int, vector: list[float]) -> None:
    db.execute(text("DELETE FROM scene_wiki_vec WHERE scene_id = :id"), {"id": scene_id})
    db.execute(
        text("INSERT INTO scene_wiki_vec(scene_id, embedding) VALUES (:id, :vec)"),
        {"id": scene_id, "vec": _serialize(vector)},
    )


def knn_wiki(db: Session, query_vector: list[float], candidate_ids: list[int] | None, k: int) -> list[tuple[int, float]]:
    return _knn(db, "scene_wiki_vec", "scene_id", query_vector, candidate_ids, k)


def knn_text(db: Session, query_vector: list[float], candidate_ids: list[int] | None, k: int) -> list[tuple[int, float]]:
    return _knn(db, "scene_text_vec", "scene_id", query_vector, candidate_ids, k)


def knn_visual(db: Session, query_vector: list[float], candidate_ids: list[int] | None, k: int) -> list[tuple[int, float]]:
    return _knn(db, "scene_visual_vec", "scene_id", query_vector, candidate_ids, k)


def upsert_chunk_vector(db: Session, chunk_id: int, vector: list[float]) -> None:
    db.execute(text("DELETE FROM transcript_chunk_vec WHERE chunk_id = :id"), {"id": chunk_id})
    db.execute(
        text("INSERT INTO transcript_chunk_vec(chunk_id, embedding) VALUES (:id, :vec)"),
        {"id": chunk_id, "vec": _serialize(vector)},
    )


def knn_chunks(db: Session, query_vector: list[float], candidate_ids: list[int] | None, k: int) -> list[tuple[int, float]]:
    return _knn(db, "transcript_chunk_vec", "chunk_id", query_vector, candidate_ids, k)


def _knn(
    db: Session,
    table: str,
    id_column: str,
    query_vector: list[float],
    candidate_ids: list[int] | None,
    k: int,
) -> list[tuple[int, float]]:
    params: dict = {"vec": _serialize(query_vector), "k": k}
    filter_clause = ""
    if candidate_ids is not None:
        if not candidate_ids:
            return []
        placeholders = ",".join(f":id{i}" for i in range(len(candidate_ids)))
        filter_clause = f"AND {id_column} IN ({placeholders})"
        params.update({f"id{i}": cid for i, cid in enumerate(candidate_ids)})

    query = text(
        f"SELECT {id_column}, distance FROM {table} "
        f"WHERE embedding MATCH :vec {filter_clause} "
        f"AND k = :k ORDER BY distance"
    )
    rows = db.execute(query, params).all()
    return [(row[0], row[1]) for row in rows]
