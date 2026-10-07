import datetime as dt

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    file_path: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    # "video" (an episode or film) or "comic" (a PDF whose pages are indexed like
    # scenes, see services/comics.py)
    kind: Mapped[str] = mapped_column(String, default="video", server_default="video", nullable=False)
    title: Mapped[str | None] = mapped_column(String, nullable=True)
    show_title: Mapped[str | None] = mapped_column(String, nullable=True)
    book: Mapped[int | None] = mapped_column(Integer, nullable=True)
    episode_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    episode_title: Mapped[str | None] = mapped_column(String, nullable=True)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)

    status: Mapped[str] = mapped_column(String, default="pending", nullable=False)
    current_stage_detail: Mapped[str | None] = mapped_column(String, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    caption_cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    visual_reindex_requested: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    text_reindex_requested: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    episode_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    episode_summary_requested: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    episode_summary_model: Mapped[str | None] = mapped_column(String, nullable=True)
    transcript_chunk_reindex_requested: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # title of the fan-wiki transcript page this episode was matched to, if any
    wiki_page: Mapped[str | None] = mapped_column(String, nullable=True)

    added_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    indexed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    transcript_lines: Mapped[list["TranscriptLine"]] = relationship(
        back_populates="source", cascade="all, delete-orphan"
    )
    shots: Mapped[list["Shot"]] = relationship(back_populates="source", cascade="all, delete-orphan")
    scenes: Mapped[list["Scene"]] = relationship(back_populates="source", cascade="all, delete-orphan")
    transcript_chunks: Mapped[list["TranscriptChunk"]] = relationship(
        back_populates="source", cascade="all, delete-orphan"
    )


class TranscriptLine(Base):
    __tablename__ = "transcript"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"))
    start_time: Mapped[float] = mapped_column(Float, nullable=False)
    end_time: Mapped[float] = mapped_column(Float, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    speaker: Mapped[str | None] = mapped_column(String, nullable=True)

    source: Mapped[Source] = relationship(back_populates="transcript_lines")


class Shot(Base):
    __tablename__ = "shots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"))
    start_time: Mapped[float] = mapped_column(Float, nullable=False)
    end_time: Mapped[float] = mapped_column(Float, nullable=False)
    shot_index: Mapped[int] = mapped_column(Integer, nullable=False)

    source: Mapped[Source] = relationship(back_populates="shots")


class Scene(Base):
    __tablename__ = "scenes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"))
    show_title: Mapped[str | None] = mapped_column(String, nullable=True)
    book: Mapped[int | None] = mapped_column(Integer, nullable=True)
    episode_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    start_time: Mapped[float] = mapped_column(Float, nullable=False)
    end_time: Mapped[float] = mapped_column(Float, nullable=False)

    keyframe_paths: Mapped[list] = mapped_column(JSON, default=list)
    visual_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    context_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # what the fan-wiki transcript says happens (and is said) during this scene -
    # a free counterpart to the paid caption, searched through scene_wiki_vec
    wiki_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    character_tags: Mapped[list] = mapped_column(JSON, default=list)
    # which part of the universe's world this is - one of the universe's regions
    region: Mapped[str | None] = mapped_column(String, nullable=True)
    location_tags: Mapped[list] = mapped_column(JSON, default=list)
    mood_action_tags: Mapped[list] = mapped_column(JSON, default=list)
    linked_transcript_ids: Mapped[list] = mapped_column(JSON, default=list)

    caption_status: Mapped[str] = mapped_column(String, default="pending", nullable=False)
    caption_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    caption_attempts: Mapped[int] = mapped_column(Integer, default=0)
    model_version: Mapped[str | None] = mapped_column(String, nullable=True)

    embedded_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    text_embedding_model: Mapped[str | None] = mapped_column(String, nullable=True)
    visual_embedding_model: Mapped[str | None] = mapped_column(String, nullable=True)
    edited_by_user: Mapped[bool] = mapped_column(Boolean, default=False)
    edited_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    source: Mapped[Source] = relationship(back_populates="scenes")
    scene_shots: Mapped[list["SceneShot"]] = relationship(back_populates="scene", cascade="all, delete-orphan")

    @property
    def kind(self) -> str:
        """"video", or "comic" when this scene is a page of a comic."""
        return self.source.kind if self.source is not None else "video"


class TranscriptChunk(Base):
    """~60s sliding-window chunks over TranscriptLine, independent of Scene
    boundaries - built for RAG-style retrieval (bigger, coarser context an LLM can
    reason over), not precise search. Embedded as episode_summary + raw_text
    together, so each chunk carries whole-episode context even though the
    transcript itself has no speaker attribution."""

    __tablename__ = "transcript_chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"))
    show_title: Mapped[str | None] = mapped_column(String, nullable=True)
    book: Mapped[int | None] = mapped_column(Integer, nullable=True)
    episode_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    start_time: Mapped[float] = mapped_column(Float, nullable=False)
    end_time: Mapped[float] = mapped_column(Float, nullable=False)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)

    text_embedding_model: Mapped[str | None] = mapped_column(String, nullable=True)
    embedded_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    source: Mapped[Source] = relationship(back_populates="transcript_chunks")


class WikiEntry(Base):
    """One row of an episode's fan-wiki transcript, in page order: a spoken line
    (with its speaker and exact wording) or a description of what happens on screen.
    The wiki has no timings; start/end are where the entry was matched in our own
    transcript - for a description, the gap between the lines around it - and are
    empty when it couldn't be placed."""

    __tablename__ = "wiki_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String, nullable=False)  # "line" | "desc"
    speaker: Mapped[str | None] = mapped_column(String, nullable=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    start_time: Mapped[float | None] = mapped_column(Float, nullable=True)
    end_time: Mapped[float | None] = mapped_column(Float, nullable=True)


class SceneShot(Base):
    __tablename__ = "scene_shots"
    __table_args__ = (UniqueConstraint("scene_id", "shot_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scene_id: Mapped[int] = mapped_column(ForeignKey("scenes.id", ondelete="CASCADE"))
    shot_id: Mapped[int] = mapped_column(ForeignKey("shots.id", ondelete="CASCADE"))
    shot_order: Mapped[int] = mapped_column(Integer, nullable=False)

    scene: Mapped[Scene] = relationship(back_populates="scene_shots")


class BankQuote(Base):
    """A notable quote from a curated collection (see services/quote_bank.py), located
    in an episode: ready to be dropped into a video with its own sound."""

    __tablename__ = "quote_bank"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"), index=True)
    start_time: Mapped[float] = mapped_column(Float, nullable=False)
    end_time: Mapped[float] = mapped_column(Float, nullable=False)
    # the lines as collected, "Speaker: text" one per line
    text: Mapped[str] = mapped_column(Text, nullable=False)
    speakers: Mapped[str] = mapped_column(String, nullable=False, default="")  # comma-separated
    origin: Mapped[str] = mapped_column(String, nullable=False)
    match_score: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
