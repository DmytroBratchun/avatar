import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api/client";
import type { EditorProject, EditorQuote } from "../api/client";

function formatTime(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}

function QuoteRow({
  project,
  quote,
  disabled,
  onChange,
}: {
  project: EditorProject;
  quote: EditorQuote;
  disabled: boolean;
  onChange: (project: EditorProject) => void;
}) {
  const [previewing, setPreviewing] = useState(false);
  const mutation = useMutation({
    mutationFn: (payload: { status?: string; after_word?: number }) => api.updateEditorQuote(project.id, quote.id, payload),
    onSuccess: onChange,
  });
  const busy = disabled || mutation.isPending;
  const length = quote.clip_end - quote.clip_start;

  return (
    <div style={{ padding: "0.6rem 0", borderTop: "1px solid var(--border, rgba(128,128,128,0.25))" }}>
      <div style={{ display: "flex", gap: "0.5rem", alignItems: "baseline", flexWrap: "wrap" }}>
        <span className={`badge ${quote.status === "approved" ? "success" : quote.status === "rejected" ? "" : "accent"}`}>
          {quote.status === "approved" ? "in the video" : quote.status}
        </span>
        <strong>{quote.speaker || "unknown"}</strong>
        <span className="hint" style={{ margin: 0 }}>
          Book {quote.book ?? "?"} Ep {quote.episode_number ?? "?"} · {formatTime(quote.clip_start)} · {length.toFixed(1)}s
        </span>
      </div>
      <p style={{ margin: "0.3rem 0" }}>“{quote.text}”</p>
      <p className="hint" style={{ margin: 0 }}>{quote.reason}</p>

      <div style={{ marginTop: "0.4rem", display: "flex", gap: "0.5rem", alignItems: "center", flexWrap: "wrap" }}>
        <span className="hint" style={{ margin: 0 }}>Plays after</span>
        {project.beats.length > 0 ? (
          <select
            style={{ width: "auto", maxWidth: "420px" }}
            value={quote.after_word}
            disabled={busy}
            onChange={(e) => mutation.mutate({ after_word: Number(e.target.value) })}
          >
            {project.beats.map((beat) => (
              <option key={beat.id} value={beat.word_end}>
                {formatTime(beat.end)} - …{beat.text.slice(-60)}
              </option>
            ))}
          </select>
        ) : (
          <span style={{ fontSize: "0.85rem" }}>
            {formatTime(quote.at)} - …{quote.after_text.slice(-60)}
          </span>
        )}
      </div>

      <div style={{ marginTop: "0.5rem", display: "flex", gap: "0.5rem" }}>
        <button className="ghost small" onClick={() => setPreviewing((v) => !v)}>
          {previewing ? "Hide clip" : "Play clip"}
        </button>
        {quote.status !== "approved" && (
          <button className="small primary" onClick={() => mutation.mutate({ status: "approved" })} disabled={busy}>
            Add to video
          </button>
        )}
        {quote.status === "approved" && (
          <button className="small" onClick={() => mutation.mutate({ status: "proposed" })} disabled={busy}>
            Take out
          </button>
        )}
        {quote.status !== "rejected" && (
          <button className="ghost small" onClick={() => mutation.mutate({ status: "rejected" })} disabled={busy}>
            Reject
          </button>
        )}
      </div>
      {previewing && (
        <video
          src={`${api.streamUrl(quote.source_id)}#t=${quote.clip_start},${quote.clip_end}`}
          controls
          autoPlay
          style={{ width: "100%", maxWidth: "480px", marginTop: "0.5rem" }}
        />
      )}
      {mutation.isError && <p className="error-text">{(mutation.error as Error).message}</p>}
    </div>
  );
}

export default function QuotesCard({
  project,
  disabled,
  onChange,
}: {
  project: EditorProject;
  disabled: boolean;
  onChange: (project: EditorProject) => void;
}) {
  if (project.quotes.length === 0) return null;
  const approved = project.quotes.filter((q) => q.status === "approved");
  const added = approved.reduce((sum, q) => sum + (q.clip_end - q.clip_start), 0);

  return (
    <div className="card">
      <div className="card-header">
        <h3 style={{ margin: 0 }}>Quotes</h3>
        <span className="hint" style={{ margin: 0 }}>
          {approved.length} of {project.quotes.length} in the video
          {approved.length > 0 && ` · adds ${added.toFixed(0)}s`}
        </span>
      </div>
      <p className="hint">
        Clips from the show that play with their own sound between two parts of the narration. Only quotes you add are
        used; render again to see them.
      </p>
      {project.quotes.map((quote) => (
        <QuoteRow key={quote.id} project={project} quote={quote} disabled={disabled} onChange={onChange} />
      ))}
    </div>
  );
}
