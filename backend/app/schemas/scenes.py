import datetime as dt

from pydantic import BaseModel, ConfigDict


class TranscriptLineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    start_time: float
    end_time: float
    text: str
    speaker: str | None = None


class SceneOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source_id: int
    show_title: str | None = None
    book: int | None
    episode_number: int | None
    start_time: float
    end_time: float
    keyframe_paths: list[str]
    visual_description: str | None
    context_description: str | None
    character_tags: list[str]
    region: str | None = None
    location_tags: list[str]
    mood_action_tags: list[str]
    caption_status: str
    edited_by_user: bool
    model_version: str | None = None
    text_embedding_model: str | None = None
    visual_embedding_model: str | None = None


class ShotOut(BaseModel):
    shot_index: int
    start_time: float
    end_time: float


class SceneTechnicalOut(BaseModel):
    """How the scene was built - for understanding the index, not for search."""

    shots: list[ShotOut] = []
    # time of each keyframe in the episode; the first one is what the visual index holds
    keyframe_times: list[float] = []
    previous_scene_id: int | None = None
    next_scene_id: int | None = None
    # why the scene ends where it does, as the grouping rules saw it
    end_reason: str = ""
    dialogue_gap_after: float | None = None
    caption_attempts: int = 0
    caption_error: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class SceneDetailOut(SceneOut):
    transcript_lines: list[TranscriptLineOut] = []
    source_file_path: str | None = None
    technical: SceneTechnicalOut = SceneTechnicalOut()


class SceneUpdateIn(BaseModel):
    context_description: str | None = None
    visual_description: str | None = None
    character_tags: list[str] | None = None
    location_tags: list[str] | None = None
    mood_action_tags: list[str] | None = None


class FacetsOut(BaseModel):
    shows: list[str]
    characters: list[str]
    locations: list[str]
    moods: list[str]


class SearchResult(BaseModel):
    scene: SceneOut
    score: float
    matched_by: str


class SourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    file_path: str
    title: str | None
    show_title: str | None
    book: int | None
    episode_number: int | None
    episode_title: str | None
    duration_seconds: float | None
    status: str
    current_stage_detail: str | None
    error_message: str | None
    caption_cost_usd: float | None
    added_at: dt.datetime
    indexed_at: dt.datetime | None
    scene_count: int = 0
    text_indexed_count: int = 0
    visual_indexed_count: int = 0
    visual_reindex_requested: bool = False
    text_reindex_requested: bool = False
    episode_summary: str | None = None
    episode_summary_requested: bool = False
    transcript_chunk_reindex_requested: bool = False
    chunk_count: int = 0
    chunk_indexed_count: int = 0


class SourceCreateIn(BaseModel):
    file_path: str
    title: str | None = None
    show_title: str | None = None
    book: int | None = None
    episode_number: int | None = None
    episode_title: str | None = None


class SourceUpdateIn(BaseModel):
    title: str | None = None
    show_title: str | None = None
    book: int | None = None
    episode_number: int | None = None
    episode_title: str | None = None


class ScanFolderIn(BaseModel):
    folder_path: str
    show_title: str | None = None


class ScanFolderResult(BaseModel):
    added: list[SourceOut]
    skipped_existing: int


class TranscriptChunkOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source_id: int
    show_title: str | None
    book: int | None
    episode_number: int | None
    start_time: float
    end_time: float
    raw_text: str


class TranscriptChunkSearchResult(BaseModel):
    chunk: TranscriptChunkOut
    episode_summary: str | None
    score: float


class TranscriptChunkContextOut(BaseModel):
    chunk: TranscriptChunkOut
    episode_summary: str | None
    overlapping_scenes: list[SceneOut]
