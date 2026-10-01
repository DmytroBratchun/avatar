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

export interface SceneDetailOut extends SceneOut {
  transcript_lines: TranscriptLineOut[];
  source_file_path: string | null;
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

  streamUrl: (sourceId: number) => `${BASE_URL}/api/media/${sourceId}/stream`,
  keyframeUrl: (sceneId: number, index: number) => `${BASE_URL}/api/media/scenes/${sceneId}/keyframes/${index}`,
};
