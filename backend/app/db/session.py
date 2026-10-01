import sqlite3
from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.db.models import Base

engine = create_engine(
    f"sqlite:///{settings.database_path}",
    connect_args={"check_same_thread": False},
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


@event.listens_for(engine, "connect")
def _load_sqlite_vec(dbapi_connection: sqlite3.Connection, _connection_record) -> None:
    dbapi_connection.enable_load_extension(True)
    try:
        import sqlite_vec

        sqlite_vec.load(dbapi_connection)
    finally:
        dbapi_connection.enable_load_extension(False)
    dbapi_connection.execute("PRAGMA foreign_keys = ON")


def _ensure_column(conn, table: str, column: str, ddl: str) -> None:
    """create_all never alters an existing table, so a column added to a model
    after the database was created has to be added by hand."""
    existing = {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})")}
    if column not in existing:
        conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def init_db() -> None:
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        _ensure_column(conn, "scenes", "region", "VARCHAR")
        conn.exec_driver_sql(
            "CREATE VIRTUAL TABLE IF NOT EXISTS scene_text_vec "
            "USING vec0(scene_id INTEGER PRIMARY KEY, embedding FLOAT[1024])"
        )
        conn.exec_driver_sql(
            "CREATE VIRTUAL TABLE IF NOT EXISTS scene_visual_vec "
            "USING vec0(scene_id INTEGER PRIMARY KEY, embedding FLOAT[1152])"
        )
        conn.exec_driver_sql(
            "CREATE VIRTUAL TABLE IF NOT EXISTS transcript_fts "
            "USING fts5(text, content='transcript', content_rowid='id')"
        )
        conn.exec_driver_sql(
            "CREATE VIRTUAL TABLE IF NOT EXISTS transcript_chunk_vec "
            "USING vec0(chunk_id INTEGER PRIMARY KEY, embedding FLOAT[1024])"
        )


@contextmanager
def get_session() -> Generator[Session, None, None]:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Generator[Session, None, None]:
    with get_session() as session:
        yield session
