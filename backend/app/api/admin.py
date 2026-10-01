from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.session import get_db

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _coerce(value):
    """Make raw sqlite values JSON-safe (blobs -> a short placeholder)."""
    if isinstance(value, (bytes, bytearray)):
        return f"<blob, {len(value)} bytes>"
    return value


def _rows_to_dicts(columns: list[str], rows) -> list[dict]:
    return [{col: _coerce(val) for col, val in zip(columns, row)} for row in rows]


@router.get("/tables")
def list_tables(db: Session = Depends(get_db)) -> list[str]:
    rows = db.execute(
        text("SELECT name FROM sqlite_master WHERE type IN ('table', 'view') "
             "AND name NOT LIKE 'sqlite_%' ORDER BY name")
    ).all()
    return [r[0] for r in rows]


@router.get("/tables/{table_name}")
def browse_table(table_name: str, limit: int = 100, offset: int = 0, db: Session = Depends(get_db)) -> dict:
    valid_tables = set(list_tables(db))
    if table_name not in valid_tables:
        raise HTTPException(404, f"Unknown table: {table_name}")

    limit = min(limit, 500)
    total = db.execute(text(f"SELECT COUNT(*) FROM {table_name}")).scalar()
    result = db.execute(text(f"SELECT * FROM {table_name} LIMIT :limit OFFSET :offset"), {"limit": limit, "offset": offset})
    columns = list(result.keys())
    rows = _rows_to_dicts(columns, result.fetchall())

    return {"table": table_name, "columns": columns, "rows": rows, "total": total, "limit": limit, "offset": offset}


class QueryIn(BaseModel):
    sql: str


@router.post("/query")
def run_query(payload: QueryIn, db: Session = Depends(get_db)) -> dict:
    sql = payload.sql.strip()
    if not sql.lower().startswith("select"):
        raise HTTPException(400, "Only SELECT queries are allowed here")
    if ";" in sql.rstrip(";"):
        raise HTTPException(400, "Only a single statement is allowed")

    try:
        result = db.execute(text(sql))
    except Exception as exc:
        raise HTTPException(400, f"Query error: {exc}")

    columns = list(result.keys())
    rows = _rows_to_dicts(columns, result.fetchmany(500))
    return {"columns": columns, "rows": rows, "truncated": len(rows) == 500}
