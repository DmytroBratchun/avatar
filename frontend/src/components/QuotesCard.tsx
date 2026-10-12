import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { api, quoteSeconds, quoteSpans } from "../api/client";
import type { EditorProject, EditorQuote, EditorWord } from "../api/client";
import Help from "./Help";

function formatTime(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}

/** Time as m:ss.s, precise enough to cut on. */
function clock(seconds: number): string {
  const m = Math.floor(seconds / 60);
  return `${m}:${(seconds - m * 60).toFixed(1).padStart(4, "0")}`;
}

/** "8:18", "8:18.5" or plain seconds -> seconds; null if it isn't a time. */
function parseClock(value: string): number | null {
  const match = value.trim().match(/^(?:(\d+):)?(\d+(?:\.\d+)?)$/);
  if (!match) return null;
  return Number(match[1] ?? 0) * 60 + Number(match[2]);
}

const endsSentence = (word: string) => /[.!?…]["']?$/.test(word);

// how much narration to show either side of a quote: whole sentences, until there are
// at least this many words (a lone "And Zaheer proved it." says nothing by itself)
const CONTEXT_WORDS = 35;

/** A sentence of the narration as word indexes (inclusive) and its text. */
type Sentence = { first: number; last: number; text: string };

function sentences(words: EditorWord[], first: number, last: number): Sentence[] {
  const out: Sentence[] = [];
  let start = first;
  for (let i = first; i <= last; i++) {
    if (endsSentence(words[i].text) || i === last) {
      out.push({ first: start, last: i, text: words.slice(start, i + 1).map((w) => w.text).join(" ") });
      start = i + 1;
    }
  }
  return out;
}

/** The narration leading up to word `afterWord` and the narration that follows it,
 * in sentences. */
function around(words: EditorWord[], afterWord: number): { before: Sentence[]; after: Sentence[] } {
  if (words.length === 0) return { before: [], after: [] };
  let start = afterWord;
  // back to the start of a sentence, and further back while the context is still short
  while (start > 0 && (!endsSentence(words[start - 1].text) || afterWord - start + 1 < CONTEXT_WORDS)) start--;
  let end = afterWord + 1;
  while (end < words.length - 1 && (!endsSentence(words[end].text) || end - afterWord < CONTEXT_WORDS)) end++;
  return {
    before: sentences(words, start, afterWord),
    after: afterWord + 1 < words.length ? sentences(words, afterWord + 1, end) : [],
  };
}

/** What the narration player is playing: a stretch of the narration, in seconds. */
type Listening = { start: number; end: number; at: number } | null;

/** One player for the narration around all the quotes: playing a stretch stops
 * whatever was playing before. */
function useNarration(projectId: string) {
  const audio = useRef<HTMLAudioElement | null>(null);
  const [listening, setListening] = useState<Listening>(null);
  const stretch = useRef<Listening>(null);

  useEffect(() => {
    const player = new Audio(api.editorAudioUrl(projectId));
    player.preload = "none";
    const onTime = () => {
      const current = stretch.current;
      if (!current) return;
      if (player.currentTime >= current.end) {
        player.pause();
        stretch.current = null;
        setListening(null);
      } else {
        setListening({ ...current, at: player.currentTime });
      }
    };
    player.addEventListener("timeupdate", onTime);
    audio.current = player;
    return () => {
      player.pause();
      player.removeEventListener("timeupdate", onTime);
    };
  }, [projectId]);

  function play(start: number, end: number) {
    const player = audio.current;
    if (!player) return;
    // a click on what is playing stops it
    if (stretch.current && stretch.current.start === start && stretch.current.end === end) {
      player.pause();
      stretch.current = null;
      setListening(null);
      return;
    }
    stretch.current = { start, end, at: start };
    setListening(stretch.current);
    player.currentTime = start;
    void player.play();
  }

  return { listening, play };
}

/** Sentences of the narration that play from where they are clicked, up to `until`
 * (seconds); the one being heard is marked. */
function Narration({
  words,
  sentences: list,
  until,
  listening,
  play,
}: {
  words: EditorWord[];
  sentences: Sentence[];
  until: number;
  listening: Listening;
  play: (start: number, end: number) => void;
}) {
  return (
    <>
      {list.map((s, n) => {
        const start = words[s.first].start;
        const heard = listening != null && listening.end === until && listening.at >= start && listening.at < words[s.last].end + 0.3;
        return (
          <span key={s.first}>
            {n > 0 && " "}
            <span
              className={`narration-sentence${heard ? " heard" : ""}`}
              title="Click to hear the narration from here"
              onClick={() => play(start, until)}
            >
              {s.text}
            </span>
          </span>
        );
      })}
    </>
  );
}

/** Plays a quote the way it will sound in the video: its pieces back to back,
 * jumping over what was cut out between them. */
function QuoteClip({ quote }: { quote: EditorQuote }) {
  const spans = quoteSpans(quote);
  // the piece being played
  const piece = useRef(0);
  return (
    <video
      src={`${api.streamUrl(quote.source_id)}#t=${spans[0].start}`}
      controls
      autoPlay
      style={{ width: "100%", maxWidth: "480px", marginTop: "0.5rem", display: "block" }}
      onTimeUpdate={(e) => {
        const video = e.currentTarget;
        if (video.currentTime < spans[piece.current].end) return;
        if (piece.current + 1 < spans.length) {
          piece.current += 1;
          video.currentTime = spans[piece.current].start;
        } else {
          video.pause();
        }
      }}
      onPlay={(e) => {
        // pressing play after the end starts the quote again
        const video = e.currentTarget;
        if (piece.current === spans.length - 1 && video.currentTime >= spans[piece.current].end) {
          piece.current = 0;
          video.currentTime = spans[0].start;
        }
      }}
    />
  );
}

function QuoteRow({
  project,
  quote,
  words,
  disabled,
  listening,
  play,
  onChange,
}: {
  project: EditorProject;
  quote: EditorQuote;
  words: EditorWord[];
  disabled: boolean;
  listening: Listening;
  play: (start: number, end: number) => void;
  onChange: (project: EditorProject) => void;
}) {
  const [previewing, setPreviewing] = useState(false);
  const [editing, setEditing] = useState(false);
  const mutation = useMutation({
    mutationFn: (payload: { status?: string; after_word?: number; clip_start?: number; clip_end?: number; drop_part?: number }) =>
      api.updateEditorQuote(project.id, quote.id, payload),
    onSuccess: onChange,
  });
  const removeMutation = useMutation({
    mutationFn: () => api.deleteEditorQuote(project.id, quote.id),
    onSuccess: onChange,
  });
  const busy = disabled || mutation.isPending || removeMutation.isPending;
  const length = quoteSeconds(quote);
  const context = around(words, quote.after_word);
  const [startText, setStartText] = useState(clock(quote.clip_start));
  const [endText, setEndText] = useState(clock(quote.clip_end));
  const newStart = parseClock(startText);
  const newEnd = parseClock(endText);
  const rangeChanged = newStart !== null && newEnd !== null && (clock(newStart) !== clock(quote.clip_start) || clock(newEnd) !== clock(quote.clip_end));

  return (
    <div style={{ padding: "0.6rem 0", borderTop: "1px solid var(--border, rgba(128,128,128,0.25))" }}>
      <div style={{ display: "flex", gap: "0.5rem", alignItems: "baseline", flexWrap: "wrap" }}>
        <span className={`badge ${quote.status === "approved" ? "success" : quote.status === "rejected" ? "" : "accent"}`}>
          {quote.status === "approved" ? "in the video" : quote.status}
        </span>
        <strong>{quote.speaker || "unknown"}</strong>
        <span className="hint" style={{ margin: 0 }}>
          Book {quote.book ?? "?"} Ep {quote.episode_number ?? "?"} · {formatTime(quote.clip_start)} · {length.toFixed(1)}s
          {quote.parts.length > 0 && ` in ${quote.parts.length} pieces`} · placed at {formatTime(quote.at)} of the narration
        </span>
      </div>

      {/* the quote where it will sit: the narration before it, the quote, the narration after */}
      <div style={{ margin: "0.4rem 0", fontSize: "0.9rem" }}>
        <p className="hint" style={{ margin: 0 }}>
          …
          {context.before.length > 0 ? (
            <Narration words={words} sentences={context.before} until={words[quote.after_word].end} listening={listening} play={play} />
          ) : (
            quote.after_text
          )}
        </p>
        <p
          style={{ margin: "0.25rem 0", paddingLeft: "0.6rem", borderLeft: "3px solid var(--accent)", cursor: "help" }}
          title={quote.reason}
        >
          “{quote.text}”
        </p>
        <p className="hint" style={{ margin: 0 }}>
          {context.after.length > 0 ? (
            <>
              <Narration
                words={words}
                sentences={context.after}
                until={words[context.after[context.after.length - 1].last].end}
                listening={listening}
                play={play}
              />
              …
            </>
          ) : (
            "(end of the narration)"
          )}
        </p>
      </div>

      <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap" }}>
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
        <button className="ghost small" onClick={() => setEditing((v) => !v)}>
          {editing ? "Done" : "Edit"}
        </button>
        <button className="ghost small" onClick={() => removeMutation.mutate()} disabled={busy} title="Remove this clip from the list">
          Delete
        </button>
      </div>

      {editing &&
        quote.parts.map((part, index) => (
          <div key={part.start} style={{ marginTop: "0.5rem", display: "flex", gap: "0.5rem", alignItems: "baseline", flexWrap: "wrap" }}>
            <span className="hint" style={{ margin: 0 }}>
              Piece {index + 1} · {clock(part.start)}-{clock(part.end)} · {(part.end - part.start).toFixed(1)}s
            </span>
            <span style={{ fontSize: "0.9rem" }}>“{part.text}”</span>
            <button className="ghost small" disabled={busy} onClick={() => mutation.mutate({ drop_part: index })} title="Play the quote without this piece">
              × remove
            </button>
          </div>
        ))}
      {editing && (
        <div style={{ marginTop: "0.5rem", display: "flex", gap: "0.5rem", alignItems: "center", flexWrap: "wrap" }}>
          <span
            className="hint"
            style={{ margin: 0 }}
            title={
              "Where the clip starts and ends in the episode, as minutes:seconds." +
              (quote.parts.length > 0 ? " Applying a range plays everything between the two times, with nothing cut out." : "")
            }
          >
            Clip in the episode from
          </span>
          <input style={{ width: "5.5rem" }} value={startText} onChange={(e) => setStartText(e.target.value)} disabled={busy} />
          <span className="hint" style={{ margin: 0 }}>to</span>
          <input style={{ width: "5.5rem" }} value={endText} onChange={(e) => setEndText(e.target.value)} disabled={busy} />
          <button
            className="small"
            disabled={busy || !(rangeChanged || (quote.parts.length > 0 && newStart !== null && newEnd !== null))}
            onClick={() => newStart !== null && newEnd !== null && mutation.mutate({ clip_start: newStart, clip_end: newEnd })}
          >
            Apply
          </button>
          {(newStart === null || newEnd === null) && <span className="error-text">Use a time like 8:18 or 8:18.5</span>}
        </div>
      )}
      {editing && (
        <div style={{ marginTop: "0.5rem", display: "flex", gap: "0.5rem", alignItems: "center", flexWrap: "wrap" }}>
          <span className="hint" style={{ margin: 0 }}>Plays after</span>
          {project.beats.length > 0 ? (
            <select
              style={{ width: "auto", maxWidth: "460px" }}
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
            <span className="hint" style={{ margin: 0 }}>
              the place can be changed once beats are built - a quote sits between two beats
            </span>
          )}
        </div>
      )}

      {previewing && <QuoteClip quote={quote} />}
      {mutation.isError && <p className="error-text">{(mutation.error as Error).message}</p>}
    </div>
  );
}

/** Add a clip by hand: pick the episode and the time range instead of searching for a quote. */
export function AddQuoteCard({
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
  const { data: sources } = useQuery({ queryKey: ["sources"], queryFn: api.listSources });
  const episodes = (sources ?? []).filter((s) => shows.length === 0 || (s.show_title !== null && shows.includes(s.show_title)));
  const [sourceId, setSourceId] = useState<number | "">("");
  const [startText, setStartText] = useState("");
  const [endText, setEndText] = useState("");
  // "" = at the very end of the narration
  const [afterWord, setAfterWord] = useState<number | "">("");
  const start = parseClock(startText);
  const end = parseClock(endText);
  const valid = sourceId !== "" && start !== null && end !== null && end > start;

  const mutation = useMutation({
    mutationFn: () =>
      api.addEditorQuote(project.id, {
        source_id: sourceId as number,
        clip_start: start as number,
        clip_end: end as number,
        after_word: afterWord === "" ? undefined : afterWord,
      }),
    onSuccess: (p) => {
      onChange(p);
      setStartText("");
      setEndText("");
    },
  });

  return (
    <div className="card" style={{ marginTop: "0.75rem" }}>
      <h3>
        Add a clip by hand
        <Help text="A stretch of an episode you already have in mind, played with its own sound - no search. It goes straight into the video; the scene page of an episode shows exact times for every line and shot." />
      </h3>
      <div style={{ display: "flex", gap: "0.5rem", alignItems: "center", flexWrap: "wrap" }}>
        <select
          style={{ width: "auto", maxWidth: "360px" }}
          value={sourceId}
          disabled={disabled}
          onChange={(e) => setSourceId(e.target.value === "" ? "" : Number(e.target.value))}
        >
          <option value="">Episode…</option>
          {episodes.map((s) => (
            <option key={s.id} value={s.id}>
              {s.show_title} · {s.book != null ? `Book ${s.book} ` : ""}
              {s.episode_number != null ? `Ep ${s.episode_number} · ` : ""}
              {(s.episode_title ?? "").split(" - ").pop()}
            </option>
          ))}
        </select>
        <span className="hint" style={{ margin: 0 }}>from</span>
        <input style={{ width: "5.5rem" }} placeholder="8:18" value={startText} onChange={(e) => setStartText(e.target.value)} disabled={disabled} />
        <span className="hint" style={{ margin: 0 }}>to</span>
        <input style={{ width: "5.5rem" }} placeholder="8:44" value={endText} onChange={(e) => setEndText(e.target.value)} disabled={disabled} />
      </div>
      <div style={{ display: "flex", gap: "0.5rem", alignItems: "center", flexWrap: "wrap", marginTop: "0.5rem" }}>
        <span className="hint" style={{ margin: 0 }}>Plays after</span>
        {project.beats.length > 0 ? (
          <select
            style={{ width: "auto", maxWidth: "460px" }}
            value={afterWord}
            disabled={disabled}
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
        <button className="small primary" onClick={() => mutation.mutate()} disabled={disabled || !valid || mutation.isPending}>
          Add to video
        </button>
        {(startText || endText) && !valid && sourceId !== "" && (
          <span className="error-text">Use times like 8:18 or 8:18.5, the end after the start</span>
        )}
      </div>
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
  // the narration's words, to show each quote between the sentences around it
  const { data: words } = useQuery({
    queryKey: ["editorTranscript", project.id, project.word_count],
    queryFn: () => api.getEditorTranscript(project.id),
    enabled: project.quotes.length > 0,
  });

  const { listening, play } = useNarration(project.id);

  if (project.quotes.length === 0) return null;
  const approved = project.quotes.filter((q) => q.status === "approved");
  const added = approved.reduce((sum, q) => sum + quoteSeconds(q), 0);

  return (
    <div className="card">
      <div className="card-header">
        <h3 style={{ margin: 0 }}>
          Quotes
          <Help text="Clips from the show that play with their own sound between two parts of the narration, shown here between the sentences they would sit between. Hover a quote to see why it was suggested, click a sentence to hear the narration from there. Only quotes you add are used; render again to see them." />
        </h3>
        <span className="hint" style={{ margin: 0 }}>
          {approved.length} of {project.quotes.length} in the video
          {approved.length > 0 && ` · adds ${added.toFixed(0)}s`}
        </span>
      </div>
      {project.quotes.map((quote) => (
        <QuoteRow key={`${quote.id}-${quote.clip_start}-${quote.clip_end}-${quote.parts.length}`} project={project} quote={quote} words={words ?? []} disabled={disabled} listening={listening} play={play} onChange={onChange} />
      ))}
    </div>
  );
}
