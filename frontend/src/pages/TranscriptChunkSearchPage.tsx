import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api/client";
import type { TranscriptChunkSearchResult } from "../api/client";
import SceneCard from "../components/SceneCard";

function formatTime(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}

function ChunkCard({ result, filePath }: { result: TranscriptChunkSearchResult; filePath: string | undefined }) {
  const [copied, setCopied] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const { chunk } = result;

  const { data: context, isFetching: contextFetching } = useQuery({
    queryKey: ["chunkContext", chunk.id],
    queryFn: () => api.getChunkContext(chunk.id),
    enabled: expanded,
  });

  function copyTimestamp() {
    if (!filePath) return;
    navigator.clipboard.writeText(`${filePath} @ ${chunk.start_time.toFixed(2)}s`);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }

  return (
    <div className="card">
      <div
        className="scene-meta"
        style={{ cursor: "pointer" }}
        onClick={() => setExpanded((v) => !v)}
        title="Click for more context and overlapping scenes"
      >
        {expanded ? "▾" : "▸"} {chunk.show_title} · Book {chunk.book} · Ep {chunk.episode_number} ·{" "}
        {formatTime(chunk.start_time)}–{formatTime(chunk.end_time)} · {(chunk.end_time - chunk.start_time).toFixed(0)}s
      </div>
      <p style={{ margin: "0.4rem 0" }}>{chunk.raw_text}</p>
      {result.episode_summary && (
        <details style={{ marginTop: "0.4rem" }}>
          <summary className="hint" style={{ cursor: "pointer" }}>
            Episode context used for this match
          </summary>
          <p className="hint" style={{ marginTop: "0.3rem" }}>
            {result.episode_summary}
          </p>
        </details>
      )}
      <button className="ghost small" style={{ marginTop: "0.5rem" }} onClick={copyTimestamp} disabled={!filePath}>
        {copied ? "Copied ✓" : "Copy timestamp + path"}
      </button>

      {expanded && (
        <div style={{ marginTop: "0.75rem", borderTop: "1px solid var(--border)", paddingTop: "0.6rem" }}>
          <label style={{ marginBottom: "0.4rem" }}>Overlapping scenes ({formatTime(chunk.start_time)}–{formatTime(chunk.end_time)})</label>
          {contextFetching && <p className="hint">Loading…</p>}
          {!contextFetching && context?.overlapping_scenes.length === 0 && (
            <p className="hint" style={{ margin: 0 }}>
              No captioned scenes overlap this window yet.
            </p>
          )}
          <div style={{ display: "flex", flexDirection: "column", gap: "0.5rem" }}>
            {context?.overlapping_scenes.map((scene) => (
              <SceneCard key={scene.id} scene={scene} />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

export default function TranscriptChunkSearchPage() {
  const [query, setQuery] = useState("");
  const [submittedQuery, setSubmittedQuery] = useState("");
  const [book, setBook] = useState("");

  const { data: sources } = useQuery({ queryKey: ["sources"], queryFn: api.listSources });
  const filePathBySourceId = new Map((sources ?? []).map((s) => [s.id, s.file_path]));

  const { data: results, isFetching } = useQuery({
    queryKey: ["transcriptChunkSearch", submittedQuery, book],
    queryFn: () => api.searchTranscriptChunks({ q: submittedQuery, book: book || undefined }),
    enabled: submittedQuery.length > 0,
  });

  return (
    <div>
      <h1>Transcript Chunks</h1>
      <p className="hint" style={{ marginTop: "-0.5rem" }}>
        Coarser, ~60s overlapping windows over raw dialogue, each embedded together with an episode summary for
        context. Built for feeding an LLM an answer, not for precise quote lookup - see the Scenes search for that.
      </p>

      <form
        className="search-bar"
        style={{ marginTop: "1rem" }}
        onSubmit={(e) => {
          e.preventDefault();
          setSubmittedQuery(query);
        }}
      >
        <input
          placeholder="e.g. what did Iroh say about balance"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          autoFocus
        />
        <button className="primary" type="submit">
          Search
        </button>
      </form>

      <div className="search-layout" style={{ marginTop: "1.25rem" }}>
        <aside className="filters">
          <div className="card">
            <h3>Filters</h3>
            <label>Book</label>
            <input placeholder="1-4" value={book} onChange={(e) => setBook(e.target.value)} />
          </div>
        </aside>

        <div className="results">
          {isFetching && <p>Searching…</p>}
          {!isFetching && submittedQuery && results?.length === 0 && (
            <div className="card">
              <p className="empty-state" style={{ padding: 0 }}>
                No chunks matched - try a different phrasing, or check that transcript chunks have been indexed
                for this show yet (Pipeline page).
              </p>
            </div>
          )}
          {!submittedQuery && (
            <div className="card">
              <p className="empty-state" style={{ padding: 0 }}>
                Ask a question about the dialogue to find matching windows.
              </p>
            </div>
          )}
          {results?.map((r) => (
            <ChunkCard key={r.chunk.id} result={r} filePath={filePathBySourceId.get(r.chunk.source_id)} />
          ))}
        </div>
      </div>
    </div>
  );
}
