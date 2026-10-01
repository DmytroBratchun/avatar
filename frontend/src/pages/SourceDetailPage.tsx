import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api/client";
import SceneCard from "../components/SceneCard";

function formatTime(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}

function IndexBadge({ label, title, done, total }: { label: string; title: string; done: number; total: number }) {
  if (total === 0) return null;
  const full = done === total;
  return (
    <span className={`badge ${full ? "success" : done > 0 ? "accent" : ""}`} title={`${title}: ${done}/${total}`}>
      {label} {done}/{total}
    </span>
  );
}

function TaskButton({
  label,
  requested,
  total,
  indexedCount,
  onClick,
  onCancel,
  pending,
  cancelling,
}: {
  label: string;
  requested: boolean;
  total: number;
  indexedCount: number;
  onClick: () => void;
  onCancel: () => void;
  pending: boolean;
  cancelling: boolean;
}) {
  const [hovering, setHovering] = useState(false);

  if (total === 0) return null;
  if (requested) {
    return (
      <button
        className="ghost small"
        onMouseEnter={() => setHovering(true)}
        onMouseLeave={() => setHovering(false)}
        onClick={onCancel}
        disabled={cancelling}
      >
        {hovering ? `Cancel ${label}` : `${label}: queued…`}
      </button>
    );
  }
  return (
    <button className="ghost small" onClick={onClick} disabled={pending}>
      {indexedCount === 0 ? `Index ${label}` : `Reindex ${label}`}
    </button>
  );
}

export default function SourceDetailPage() {
  const { id } = useParams();
  const sourceId = Number(id);
  const queryClient = useQueryClient();

  const { data: source } = useQuery({
    queryKey: ["source", sourceId],
    queryFn: () => api.getSource(sourceId),
  });
  const { data: scenes } = useQuery({
    queryKey: ["scenes", "source", sourceId],
    queryFn: () => api.listScenes({ source_id: sourceId, limit: 500 }),
    enabled: !!source,
  });
  const { data: chunks } = useQuery({
    queryKey: ["chunks", "source", sourceId],
    queryFn: () => api.listTranscriptChunks(sourceId),
    enabled: !!source,
  });

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["source", sourceId] });
    queryClient.invalidateQueries({ queryKey: ["sources"] });
  };

  const captionMutation = useMutation({
    mutationFn: () => api.reindexSource(sourceId),
    onSuccess: invalidate,
  });
  const reindexTextMutation = useMutation({ mutationFn: () => api.reindexText(sourceId), onSuccess: invalidate });
  const cancelTextMutation = useMutation({ mutationFn: () => api.cancelTextReindex(sourceId), onSuccess: invalidate });
  const reindexVisualMutation = useMutation({ mutationFn: () => api.reindexVisual(sourceId), onSuccess: invalidate });
  const cancelVisualMutation = useMutation({
    mutationFn: () => api.cancelVisualReindex(sourceId),
    onSuccess: invalidate,
  });
  const summaryMutation = useMutation({
    mutationFn: () => api.requestEpisodeSummary(sourceId),
    onSuccess: invalidate,
  });
  const cancelSummaryMutation = useMutation({
    mutationFn: () => api.cancelEpisodeSummary(sourceId),
    onSuccess: invalidate,
  });
  const chunksMutation = useMutation({
    mutationFn: () => api.reindexTranscriptChunks(sourceId),
    onSuccess: invalidate,
  });
  const cancelChunksMutation = useMutation({
    mutationFn: () => api.cancelTranscriptChunkReindex(sourceId),
    onSuccess: invalidate,
  });

  if (!source) return <p>Loading…</p>;

  return (
    <div>
      <Link to="/" className="hint" style={{ display: "inline-block", marginBottom: "0.75rem" }}>
        ← Back to library
      </Link>
      <h1>
        {source.show_title ?? "Unknown show"} · Book {source.book} · Episode {source.episode_number}
        {source.episode_title && (
          <span style={{ color: "var(--text-tertiary)", fontWeight: 400, fontSize: "0.9rem", marginLeft: "0.6rem" }}>
            {source.episode_title}
          </span>
        )}
      </h1>

      <div className="card" style={{ marginTop: "0.75rem" }}>
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))",
            gap: "0.75rem",
            fontSize: "0.85rem",
          }}
        >
          <div>
            <div className="hint">Status</div>
            <div>{source.status}</div>
          </div>
          <div>
            <div className="hint">Duration</div>
            <div>{source.duration_seconds ? formatTime(source.duration_seconds) : "—"}</div>
          </div>
          <div>
            <div className="hint">Caption cost</div>
            <div>{source.caption_cost_usd != null ? `$${source.caption_cost_usd.toFixed(4)}` : "—"}</div>
          </div>
          <div>
            <div className="hint">Scenes</div>
            <div>{source.scene_count}</div>
          </div>
          <div>
            <div className="hint">Index</div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: "0.4rem", fontFamily: "var(--font-mono)", fontSize: "0.85rem" }}>
              <IndexBadge label="T" title="Text index" done={source.text_indexed_count} total={source.scene_count} />
              <IndexBadge label="V" title="Visual index" done={source.visual_indexed_count} total={source.scene_count} />
              <IndexBadge label="C" title="Chunk index" done={source.chunk_indexed_count} total={source.chunk_count} />
            </div>
          </div>
          <div>
            <div className="hint">Added</div>
            <div>{new Date(source.added_at).toLocaleString()}</div>
          </div>
        </div>

        {source.error_message && (
          <p style={{ marginTop: "0.75rem", color: "var(--danger, #c0392b)" }}>Error: {source.error_message}</p>
        )}

        <p
          style={{
            marginTop: "0.75rem",
            fontSize: "0.78rem",
            fontFamily: "var(--font-mono)",
            color: "var(--text-secondary)",
            wordBreak: "break-all",
          }}
        >
          {source.file_path}
        </p>
      </div>

      <div className="card" style={{ marginTop: "0.75rem" }}>
        <h3>Pipeline actions</h3>
        <div className="row-actions" style={{ flexWrap: "wrap", justifyContent: "flex-start" }}>
          <button className="ghost small" onClick={() => captionMutation.mutate()} disabled={captionMutation.isPending}>
            {source.status === "ready_for_captioning" || source.status === "failed" ? "Start captioning" : "Reindex (full)"}
          </button>
          <TaskButton
            label="text"
            requested={source.text_reindex_requested}
            total={source.scene_count}
            indexedCount={source.text_indexed_count}
            onClick={() => reindexTextMutation.mutate()}
            onCancel={() => cancelTextMutation.mutate()}
            pending={reindexTextMutation.isPending}
            cancelling={cancelTextMutation.isPending}
          />
          <TaskButton
            label="visual"
            requested={source.visual_reindex_requested}
            total={source.scene_count}
            indexedCount={source.visual_indexed_count}
            onClick={() => reindexVisualMutation.mutate()}
            onCancel={() => cancelVisualMutation.mutate()}
            pending={reindexVisualMutation.isPending}
            cancelling={cancelVisualMutation.isPending}
          />
          <TaskButton
            label="summary"
            requested={source.episode_summary_requested}
            total={1}
            indexedCount={source.episode_summary ? 1 : 0}
            onClick={() => summaryMutation.mutate()}
            onCancel={() => cancelSummaryMutation.mutate()}
            pending={summaryMutation.isPending}
            cancelling={cancelSummaryMutation.isPending}
          />
          <TaskButton
            label="chunks"
            requested={source.transcript_chunk_reindex_requested}
            total={source.chunk_count}
            indexedCount={source.chunk_indexed_count}
            onClick={() => chunksMutation.mutate()}
            onCancel={() => cancelChunksMutation.mutate()}
            pending={chunksMutation.isPending}
            cancelling={cancelChunksMutation.isPending}
          />
        </div>
      </div>

      {source.episode_summary && (
        <div className="card" style={{ marginTop: "0.75rem" }}>
          <h3>Episode summary</h3>
          <p style={{ margin: 0 }}>{source.episode_summary}</p>
        </div>
      )}

      <div className="card" style={{ marginTop: "0.75rem" }}>
        <h3>Scenes ({scenes?.length ?? 0})</h3>
        <div className="results">{scenes?.map((scene) => <SceneCard key={scene.id} scene={scene} />)}</div>
      </div>

      {chunks && chunks.length > 0 && (
        <div className="card" style={{ marginTop: "0.75rem", maxHeight: "420px", overflowY: "auto" }}>
          <h3>Transcript chunks ({chunks.length})</h3>
          {chunks.map((chunk) => (
            <div className="transcript-line" key={chunk.id}>
              <span className="ts">
                {formatTime(chunk.start_time)}–{formatTime(chunk.end_time)}
              </span>
              {chunk.raw_text}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
