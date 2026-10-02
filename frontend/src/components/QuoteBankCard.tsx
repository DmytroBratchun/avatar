import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api/client";
import type { EditorProject } from "../api/client";

function formatTime(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}

/** Notable quotes collected from the web and already located in the episodes: search,
 * watch, and drop one into the video. */
export default function QuoteBankCard({
  project,
  shows,
  disabled,
  onChange,
}: {
  project: EditorProject;
  // shows this project may use; empty = any
  shows: string[];
  disabled: boolean;
  onChange: (project: EditorProject) => void;
}) {
  const [text, setText] = useState("");
  const [query, setQuery] = useState("");
  const [speaker, setSpeaker] = useState("");
  // "" = at the very end of the narration
  const [afterWord, setAfterWord] = useState<number | "">("");
  const [previewId, setPreviewId] = useState<number | null>(null);

  const { data, isFetching } = useQuery({
    queryKey: ["quoteBank", query, speaker, shows],
    queryFn: () => api.listBankQuotes({ q: query, speaker, shows }),
  });
  // a clip already in the video is marked, so the same quote isn't added twice
  const used = new Set(project.quotes.map((q) => `${q.source_id}:${Math.round(q.clip_start)}`));

  const mutation = useMutation({
    mutationFn: (quote: { source_id: number; start_time: number; end_time: number }) =>
      api.addEditorQuote(project.id, {
        source_id: quote.source_id,
        clip_start: quote.start_time,
        clip_end: quote.end_time,
        after_word: afterWord === "" ? undefined : afterWord,
      }),
    onSuccess: onChange,
  });

  return (
    <div className="card" style={{ marginTop: "0.75rem" }}>
      <h3>Quote bank</h3>
      <p className="hint" style={{ marginTop: 0 }}>
        Lines people have singled out as worth quoting, already found in your episodes. Added clips can be trimmed
        afterwards with Edit.
      </p>
      <div style={{ display: "flex", gap: "0.5rem", alignItems: "center", flexWrap: "wrap" }}>
        <input
          style={{ width: "260px" }}
          placeholder="Words in the quote…"
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && setQuery(text.trim())}
        />
        <button className="small" onClick={() => setQuery(text.trim())}>
          Search
        </button>
        <select style={{ width: "auto" }} value={speaker} onChange={(e) => setSpeaker(e.target.value)}>
          <option value="">Anyone</option>
          {(data?.speakers ?? []).map((name) => (
            <option key={name} value={name}>
              {name}
            </option>
          ))}
        </select>
        <span className="hint" style={{ margin: 0 }}>
          {isFetching ? "…" : data ? `${data.total} quotes${data.total > data.quotes.length ? `, first ${data.quotes.length} shown` : ""}` : ""}
        </span>
      </div>
      <div style={{ display: "flex", gap: "0.5rem", alignItems: "center", flexWrap: "wrap", marginTop: "0.5rem" }}>
        <span className="hint" style={{ margin: 0 }}>Added quotes play after</span>
        {project.beats.length > 0 ? (
          <select
            style={{ width: "auto", maxWidth: "460px" }}
            value={afterWord}
            onChange={(e) => setAfterWord(e.target.value === "" ? "" : Number(e.target.value))}
          >
            <option value="">the end of the narration</option>
            {project.beats.map((beat) => (
              <option key={beat.id} value={beat.word_end}>
                {formatTime(beat.end)} - …{beat.text.slice(-60)}
              </option>
            ))}
          </select>
        ) : (
          <span className="hint" style={{ margin: 0 }}>
            the end of the narration (it can be moved once beats are built)
          </span>
        )}
      </div>
      {mutation.isError && <p className="error-text">{(mutation.error as Error).message}</p>}

      <div style={{ maxHeight: "460px", overflowY: "auto", marginTop: "0.6rem" }}>
        {data && data.quotes.length === 0 && (
          <p className="hint">
            {data.speakers.length === 0 && !query ? "The bank is empty - fill it with scripts/import_quote_bank.py." : "Nothing matches."}
          </p>
        )}
        {(data?.quotes ?? []).map((quote) => {
          const inVideo = used.has(`${quote.source_id}:${Math.round(quote.start_time)}`);
          return (
            <div key={quote.id} style={{ borderTop: "1px solid var(--border, #333)", padding: "0.5rem 0" }}>
              <div style={{ display: "flex", gap: "0.5rem", alignItems: "center", flexWrap: "wrap" }}>
                <span className="hint" style={{ margin: 0 }} title={quote.origin}>
                  {quote.show_title === "The Legend of Korra" ? "Korra" : quote.show_title === "Avatar: The Last Airbender" ? "ATLA" : quote.show_title}
                  {quote.book != null ? ` · Book ${quote.book}` : ""}
                  {quote.episode_number != null ? ` · Ep ${quote.episode_number}` : ""} · {formatTime(quote.start_time)}–
                  {formatTime(quote.end_time)} · {Math.round(quote.end_time - quote.start_time)}s
                </span>
                <button className="small" onClick={() => setPreviewId(previewId === quote.id ? null : quote.id)}>
                  {previewId === quote.id ? "Hide" : "Watch"}
                </button>
                <button
                  className="small primary"
                  disabled={disabled || mutation.isPending || inVideo || project.word_count === 0}
                  title={project.word_count === 0 ? "Transcribe the narration first" : undefined}
                  onClick={() => mutation.mutate(quote)}
                >
                  {inVideo ? "In the video" : "Add to video"}
                </button>
              </div>
              <p style={{ margin: "0.3rem 0 0", whiteSpace: "pre-line", fontSize: "0.88rem" }}>{quote.text}</p>
              {previewId === quote.id && (
                // #t=start,end plays just this stretch of the episode
                <video
                  src={`${api.streamUrl(quote.source_id)}#t=${quote.start_time},${quote.end_time}`}
                  controls
                  autoPlay
                  style={{ width: "100%", maxWidth: "420px", display: "block", marginTop: "0.4rem" }}
                />
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
