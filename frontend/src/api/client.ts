const BASE_URL = "http://localhost:8000";

export interface SourceOut {
  id: number;
  file_path: string;
  title: string | null;
  show_title: string | null;
  book: number | null;
  episode_number: number | null;
  episode_title: string | null;
  duration_seconds: number | null;
  status: string;
  current_stage_detail: string | null;
  error_message: string | null;
  caption_cost_usd: number | null;
  added_at: string;
  indexed_at: string | null;
  scene_count: number;
  text_indexed_count: number;
  to_caption_count: number;
  visual_indexed_count: number;
  visual_reindex_requested: boolean;
  text_reindex_requested: boolean;
  episode_summary: string | null;
  episode_summary_requested: boolean;
  transcript_chunk_reindex_requested: boolean;
  chunk_count: number;
  chunk_indexed_count: number;
}

export interface ConfigOut {
  caption_provider: string;
  caption_model: string;
}

export interface FacetsOut {
  shows: string[];
  characters: string[];
  locations: string[];
  moods: string[];
}

export interface IndexStatsOut {
  total_scenes: number;
  captioned: number;
  caption_error: number;
  text_indexed: number;
  visual_indexed: number;
  wiki_indexed: number;
  described: number;
  in_both_text_indexes: number;
  caption_only_undescribed: boolean;
  transcript_lines: number;
  visual_reindex_queue: number;
  text_reindex_queue: number;
  total_chunks: number;
  chunks_indexed: number;
  total_sources: number;
  episodes_summarized: number;
  episode_summary_queue: number;
  transcript_chunk_reindex_queue: number;
}

export interface TableBrowseResult {
  table: string;
  columns: string[];
  rows: Record<string, unknown>[];
  total: number;
  limit: number;
  offset: number;
}

export interface QueryResult {
  columns: string[];
  rows: Record<string, unknown>[];
  truncated: boolean;
}

export interface TranscriptSearchResult {
  source_id: number;
  book: number | null;
  episode_number: number | null;
  episode_title: string | null;
  start_time: number;
  end_time: number;
  text: string;
}

export interface WorkerStatusOut {
  running: boolean;
  reason: string | null;
  status?: string;
  current_source_id?: number | null;
  current_episode?: string | null;
  last_seen?: string | null;
  detail?: string | null;
}

export interface SettingsOut {
  caption_provider: string;
  caption_provider_from_env: boolean;
  caption_model: string;
  caption_model_from_env: boolean;
  anthropic_api_key: string;
  anthropic_api_key_from_env: boolean;
}

export interface SceneOut {
  id: number;
  source_id: number;
  show_title: string | null;
  book: number | null;
  episode_number: number | null;
  start_time: number;
  end_time: number;
  keyframe_paths: string[];
  visual_description: string | null;
  context_description: string | null;
  character_tags: string[];
  region?: string | null;
  wiki_description?: string | null;
  location_tags: string[];
  mood_action_tags: string[];
  caption_status: string;
  edited_by_user: boolean;
  model_version: string | null;
  text_embedding_model: string | null;
  visual_embedding_model: string | null;
}

export interface TranscriptLineOut {
  id: number;
  start_time: number;
  end_time: number;
  text: string;
  speaker: string | null;
}

export interface SceneTechnicalOut {
  shots: { shot_index: number; start_time: number; end_time: number }[];
  keyframe_times: number[];
  previous_scene_id: number | null;
  next_scene_id: number | null;
  end_reason: string;
  dialogue_gap_after: number | null;
  caption_attempts: number;
  caption_error: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface SceneDetailOut extends SceneOut {
  transcript_lines: TranscriptLineOut[];
  source_file_path: string | null;
  technical: SceneTechnicalOut;
}

export interface SearchResult {
  scene: SceneOut;
  score: number;
  matched_by: string;
}

export interface TranscriptChunkOut {
  id: number;
  source_id: number;
  show_title: string | null;
  book: number | null;
  episode_number: number | null;
  start_time: number;
  end_time: number;
  raw_text: string;
}

export interface TranscriptChunkSearchResult {
  chunk: TranscriptChunkOut;
  episode_summary: string | null;
  score: number;
}

export interface TranscriptChunkContextOut {
  chunk: TranscriptChunkOut;
  episode_summary: string | null;
  overlapping_scenes: SceneOut[];
}

export interface UniverseOut {
  id: string;
  name: string;
  description?: string;
  shows: string[];
  regions: string[];
  // the universe's shows that are actually in the library
  available_shows: string[];
}

export interface EditorWord {
  text: string;
  start: number;
  end: number;
}

export interface EditorSegment {
  id: string;
  type: string;
  audio_start: number;
  audio_end: number;
}

export interface EditorCandidate {
  scene_id: number;
  source_id: number;
  file_path: string;
  show_title: string | null;
  book: number | null;
  episode_number: number | null;
  scene_start: number;
  scene_end: number;
  clip_start: number;
  clip_end: number;
  description: string | null;
  text_score: number | null;
  wiki_score?: number | null;
  visual_score: number | null;
  score: number;
  slivers?: number;
}

export interface EditorSlot {
  start: number;
  end: number;
  text_query: string;
  visual_query: string;
  shows: string[];
  candidates: EditorCandidate[];
  selected: number | null;
  locked: boolean;
}

export interface EditorBackground {
  scene_id: number;
  source_id: number;
  file_path: string;
  clip_start: number;
  clip_end: number;
}

export interface EditorBeat {
  slots: EditorSlot[];
  background_query: string;
  backgrounds: EditorBackground[];
  id: string;
  segment_id: string;
  word_start: number;
  word_end: number;
  start: number;
  end: number;
  text: string;
  summary: string;
}

/** A notable quote from a curated collection, located in an episode. */
export interface BankQuote {
  id: number;
  source_id: number;
  show_title: string | null;
  book: number | null;
  episode_number: number | null;
  start_time: number;
  end_time: number;
  text: string;
  speakers: string;
  origin: string;
}

export interface EditorQuote {
  id: string;
  status: "proposed" | "approved" | "rejected";
  after_word: number;
  at: number;
  after_text: string;
  source_id: number;
  file_path: string;
  show_title: string | null;
  book: number | null;
  episode_number: number | null;
  clip_start: number;
  clip_end: number;
  // pieces that play back to back when the quote is cut together; empty = one continuous clip
  parts: { start: number; end: number; text: string }[];
  text: string;
  speaker: string;
  reason: string;
}

/** The stretches of the episode a quote plays, in order. */
export function quoteSpans(quote: EditorQuote): { start: number; end: number; text: string }[] {
  return quote.parts.length > 0 ? quote.parts : [{ start: quote.clip_start, end: quote.clip_end, text: quote.text }];
}

/** How long a quote plays: its pieces added up. */
export function quoteSeconds(quote: EditorQuote): number {
  return quoteSpans(quote).reduce((sum, part) => sum + (part.end - part.start), 0);
}

// the three kinds of footage the Look tab styles separately
export type LookFormat = "4:3" | "16:9" | "wide";

export type EditorStage = "transcribe" | "quotes" | "beats" | "queries" | "select" | "background" | "render";

export interface EditorJob {
  stage: string;
  status: "running" | "done" | "error";
  detail: string;
  progress: number | null;
  error: string | null;
  started_at: string;
}

export interface EditorProject {
  version: number;
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  universe: string;
  audio_file: string | null;
  audio_duration: number | null;
  script_text: string | null;
  shows: string[];
  settings: {
    layout: "framed" | "simple";
    search_mode: "smart_hybrid" | "text" | "visual";
    max_clip_seconds: number;
    frame: string;
    clip_height: number;
    blur: number;
    dim: number;
    frame_scale: number;
    clip_scale: number;
    corner_radius: number;
    corner_radii: number[] | null;
    wide_full_screen: boolean;
    wide_fill: number;
    background_speed: number;
    quote_cuts: boolean;
  };
  word_count: number;
  timeline: EditorSegment[];
  beats: EditorBeat[];
  quotes: EditorQuote[];
  llm_cost_usd: number;
  output_file: string | null;
  rendered_at: string | null;
  job: EditorJob | null;
  queue: EditorStage[];
}

type QueryValue = string | number | string[] | undefined;

function buildQuery(params: Record<string, QueryValue>): string {
  const qs = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined) continue;
    if (Array.isArray(value)) {
      for (const v of value) qs.append(key, v);
    } else {
      qs.append(key, String(value));
    }
  }
  const str = qs.toString();
  return str ? `?${str}` : "";
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`${res.status} ${res.statusText}: ${body}`);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export interface ScanFolderResult {
  added: SourceOut[];
  skipped_existing: number;
}

export const api = {
  getConfig: () => request<ConfigOut>("/api/config"),
  listSources: () => request<SourceOut[]>("/api/sources"),
  getSource: (id: number) => request<SourceOut>(`/api/sources/${id}`),
  createSource: (payload: {
    file_path: string;
    title?: string;
    show_title?: string;
    book?: number;
    episode_number?: number;
    episode_title?: string;
  }) => request<SourceOut>("/api/sources", { method: "POST", body: JSON.stringify(payload) }),
  scanFolder: (folder_path: string, show_title?: string) =>
    request<ScanFolderResult>("/api/sources/scan-folder", {
      method: "POST",
      body: JSON.stringify({ folder_path, show_title }),
    }),
  updateSource: (
    id: number,
    payload: Partial<Pick<SourceOut, "title" | "show_title" | "book" | "episode_number" | "episode_title">>
  ) => request<SourceOut>(`/api/sources/${id}`, { method: "PATCH", body: JSON.stringify(payload) }),
  reindexSource: (id: number) => request<SourceOut>(`/api/sources/${id}/reindex`, { method: "POST" }),
  dequeueSource: (id: number) => request<SourceOut>(`/api/sources/${id}/dequeue`, { method: "POST" }),
  prepareSource: (id: number) => request<SourceOut>(`/api/sources/${id}/prepare`, { method: "POST" }),
  reindexVisual: (id: number) => request<SourceOut>(`/api/sources/${id}/reindex-visual`, { method: "POST" }),
  reindexText: (id: number) => request<SourceOut>(`/api/sources/${id}/reindex-text`, { method: "POST" }),
  cancelVisualReindex: (id: number) => request<SourceOut>(`/api/sources/${id}/cancel-visual-reindex`, { method: "POST" }),
  cancelTextReindex: (id: number) => request<SourceOut>(`/api/sources/${id}/cancel-text-reindex`, { method: "POST" }),
  requestEpisodeSummary: (id: number) => request<SourceOut>(`/api/sources/${id}/request-episode-summary`, { method: "POST" }),
  cancelEpisodeSummary: (id: number) => request<SourceOut>(`/api/sources/${id}/cancel-episode-summary`, { method: "POST" }),
  reindexTranscriptChunks: (id: number) => request<SourceOut>(`/api/sources/${id}/reindex-transcript-chunks`, { method: "POST" }),
  cancelTranscriptChunkReindex: (id: number) =>
    request<SourceOut>(`/api/sources/${id}/cancel-transcript-chunk-reindex`, { method: "POST" }),
  deleteSource: (id: number) => request<void>(`/api/sources/${id}`, { method: "DELETE" }),

  listScenes: (params: Record<string, QueryValue> = {}) => request<SceneOut[]>(`/api/scenes${buildQuery(params)}`),
  getScene: (id: number) => request<SceneDetailOut>(`/api/scenes/${id}`),
  updateScene: (id: number, payload: Partial<SceneOut>) =>
    request<SceneOut>(`/api/scenes/${id}`, { method: "PATCH", body: JSON.stringify(payload) }),
  getFacets: () => request<FacetsOut>("/api/scenes/facets"),
  getIndexStats: () => request<IndexStatsOut>("/api/scenes/index-stats"),
  getWorkerStatus: () => request<WorkerStatusOut>("/api/worker/status"),
  startWorker: () => request<{ started: boolean; pid: number }>("/api/worker/start", { method: "POST" }),
  stopWorker: () => request<{ stopped: boolean }>("/api/worker/stop", { method: "POST" }),
  listTables: () => request<string[]>("/api/admin/tables"),
  browseTable: (table: string, limit: number, offset: number) =>
    request<TableBrowseResult>(`/api/admin/tables/${table}${buildQuery({ limit, offset })}`),
  runQuery: (sql: string) => request<QueryResult>("/api/admin/query", { method: "POST", body: JSON.stringify({ sql }) }),
  getLocalWorkerStatus: () => request<WorkerStatusOut>("/api/worker/local-status"),

  getSettings: () => request<SettingsOut>("/api/settings"),
  updateSettings: (payload: { caption_provider?: string; caption_model?: string; anthropic_api_key?: string }) =>
    request<SettingsOut>("/api/settings", { method: "PATCH", body: JSON.stringify(payload) }),

  search: (params: Record<string, QueryValue>) => request<SearchResult[]>(`/api/search${buildQuery(params)}`),
  searchTranscript: (params: Record<string, QueryValue>) =>
    request<TranscriptSearchResult[]>(`/api/transcript/search${buildQuery(params)}`),
  searchTranscriptChunks: (params: Record<string, QueryValue>) =>
    request<TranscriptChunkSearchResult[]>(`/api/transcript-chunks/search${buildQuery(params)}`),
  getChunkContext: (chunkId: number) => request<TranscriptChunkContextOut>(`/api/transcript-chunks/${chunkId}/context`),
  listTranscriptChunks: (sourceId: number) =>
    request<TranscriptChunkOut[]>(`/api/transcript-chunks${buildQuery({ source_id: sourceId })}`),

  listLogs: () =>
    request<{ name: string; label: string; exists: boolean; size: number; modified: number | null; active: boolean }[]>("/api/logs"),
  tailLog: (name: string, lines: number) =>
    request<{ name: string; lines: string[]; size: number }>(`/api/logs/${name}${buildQuery({ lines })}`),

  getEditorSettings: () => request<{ projects_root: string | null }>("/api/editor/settings"),
  setEditorProjectsRoot: (projects_root: string) =>
    request<{ projects_root: string | null }>("/api/editor/settings", {
      method: "PUT",
      body: JSON.stringify({ projects_root }),
    }),
  listEditorProjects: () => request<EditorProject[]>("/api/editor/projects"),
  getEditorProject: (id: string) => request<EditorProject>(`/api/editor/projects/${id}`),
  getEditorDefaultLook: () => request<EditorProject["settings"]>("/api/editor/default-look"),
  saveEditorDefaultLook: (id: string) =>
    request<EditorProject["settings"]>(`/api/editor/projects/${id}/save-default-look`, { method: "POST" }),
  listUniverses: () => request<UniverseOut[]>("/api/universes"),
  listBankQuotes: (params: { q?: string; speaker?: string; shows?: string[] }) => {
    const search = new URLSearchParams();
    if (params.q) search.set("q", params.q);
    if (params.speaker) search.set("speaker", params.speaker);
    (params.shows ?? []).forEach((show) => search.append("shows", show));
    return request<{ total: number; speakers: string[]; quotes: BankQuote[] }>(`/api/quote-bank?${search}`);
  },
  createEditorProject: (payload: { title: string; universe: string; script_text?: string; shows: string[]; layout?: string }) =>
    request<EditorProject>("/api/editor/projects", { method: "POST", body: JSON.stringify(payload) }),
  updateEditorProject: (
    id: string,
    payload: {
      title?: string;
      script_text?: string;
      shows?: string[];
      layout?: string;
      search_mode?: string;
      max_clip_seconds?: number;
      frame?: string;
      blur?: number;
      dim?: number;
      frame_scale?: number;
      clip_scale?: number;
      corner_radius?: number;
      corner_radii?: number[] | null;
      background_speed?: number;
      wide_full_screen?: boolean;
      wide_fill?: number;
      quote_cuts?: boolean;
    }
  ) => request<EditorProject>(`/api/editor/projects/${id}`, { method: "PATCH", body: JSON.stringify(payload) }),
  // raw body upload - the backend has no multipart parser
  uploadEditorAudio: (id: string, file: File) =>
    request<EditorProject>(`/api/editor/projects/${id}/audio${buildQuery({ filename: file.name })}`, {
      method: "PUT",
      headers: { "Content-Type": "application/octet-stream" },
      body: file,
    }),
  runEditorStage: (id: string, stage: EditorStage) =>
    request<EditorProject>(`/api/editor/projects/${id}/run/${stage}`, { method: "POST" }),
  getEditorTranscript: (id: string) => request<EditorWord[]>(`/api/editor/projects/${id}/transcript`),
  unqueueEditorStage: (id: string, stage: EditorStage) =>
    request<EditorProject>(`/api/editor/projects/${id}/queue/${stage}`, { method: "DELETE" }),
  updateEditorSlot: (id: string, beatId: string, slotIndex: number, payload: { selected?: number; locked?: boolean }) =>
    request<EditorProject>(`/api/editor/projects/${id}/beats/${beatId}/slots/${slotIndex}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  searchEditorSlot: (id: string, beatId: string, slotIndex: number, payload: { text_query: string; visual_query: string; shows?: string[] }) =>
    request<EditorProject>(`/api/editor/projects/${id}/beats/${beatId}/slots/${slotIndex}/search`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  setEditorSlotManually: (id: string, beatId: string, slotIndex: number, payload: { scene_id?: number; source_id?: number; start?: number }) =>
    request<EditorProject>(`/api/editor/projects/${id}/beats/${beatId}/slots/${slotIndex}/manual`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  addEditorQuote: (id: string, payload: { source_id: number; clip_start: number; clip_end: number; after_word?: number }) =>
    request<EditorProject>(`/api/editor/projects/${id}/quotes`, { method: "POST", body: JSON.stringify(payload) }),
  deleteEditorQuote: (id: string, quoteId: string) =>
    request<EditorProject>(`/api/editor/projects/${id}/quotes/${quoteId}`, { method: "DELETE" }),
  updateEditorQuote: (
    id: string,
    quoteId: string,
    payload: { status?: string; after_word?: number; clip_start?: number; clip_end?: number; drop_part?: number }
  ) =>
    request<EditorProject>(`/api/editor/projects/${id}/quotes/${quoteId}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  listEditorFrames: () => request<string[]>("/api/editor/frames"),
  uploadEditorFrame: (file: File) =>
    request<string[]>(`/api/editor/frames${buildQuery({ filename: file.name })}`, {
      method: "PUT",
      headers: { "Content-Type": "application/octet-stream" },
      body: file,
    }),
  // rendered_at in the URL so the browser drops its cached copy after a re-render
  editorOutputUrl: (id: string, renderedAt: string | null) =>
    `${BASE_URL}/api/editor/projects/${id}/output${buildQuery({ v: renderedAt ?? undefined })}`,
  // `version` changes whenever the look or the clip changes, so the browser asks for a fresh still
  editorPreviewUrl: (id: string, slot: number, format: LookFormat, version: string) =>
    `${BASE_URL}/api/editor/projects/${id}/preview${buildQuery({ slot, format, v: version })}`,
  getEditorPreviewInfo: (id: string) =>
    request<Record<LookFormat, { source: "project" | "library" | "placeholder"; count: number }>>(
      `/api/editor/projects/${id}/preview-info`
    ),
  editorAudioUrl: (id: string) => `${BASE_URL}/api/editor/projects/${id}/audio`,

  streamUrl: (sourceId: number) => `${BASE_URL}/api/media/${sourceId}/stream`,
  keyframeUrl: (sceneId: number, index: number) => `${BASE_URL}/api/media/scenes/${sceneId}/keyframes/${index}`,
};
