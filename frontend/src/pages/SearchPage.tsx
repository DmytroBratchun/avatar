import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import SceneCard from "../components/SceneCard";
import TagMultiSelect from "../components/TagMultiSelect";
import { api } from "../api/client";

function formatTime(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}

export default function SearchPage() {
  const [query, setQuery] = useState("");
  const [submittedQuery, setSubmittedQuery] = useState("");
  // text mode ranked plot-relevant scenes noticeably better than hybrid in testing on
  // real footage - visual (CLIP) similarity between visually-similar-but-unrelated
  // scenes (e.g. lots of snow/ice shots) was diluting the narrative match
  const [mode, setMode] = useState<"hybrid" | "text" | "visual" | "transcript">("text");
  const [book, setBook] = useState("");
  const [characters, setCharacters] = useState<string[]>([]);
  const [shows, setShows] = useState<string[]>([]);

  const { data: facets } = useQuery({ queryKey: ["facets"], queryFn: api.getFacets });

  const { data: sceneResults, isFetching: sceneFetching } = useQuery({
    queryKey: ["search", submittedQuery, mode, book, characters, shows],
    queryFn: () =>
      api.search({
        q: submittedQuery,
        mode,
        book: book || undefined,
        characters: characters.length ? characters : undefined,
        shows: shows.length ? shows : undefined,
      }),
    enabled: submittedQuery.length > 0 && mode !== "transcript",
  });

  const { data: transcriptResults, isFetching: transcriptFetching } = useQuery({
    queryKey: ["transcriptSearch", submittedQuery, book],
    queryFn: () => api.searchTranscript({ q: submittedQuery, book: book || undefined }),
    enabled: submittedQuery.length > 0 && mode === "transcript",
  });

  const isFetching = mode === "transcript" ? transcriptFetching : sceneFetching;
  const resultCount = mode === "transcript" ? transcriptResults?.length : sceneResults?.length;

  return (
    <div>
      <h1>Search</h1>
      <form
        className="search-bar"
        onSubmit={(e) => {
          e.preventDefault();
          setSubmittedQuery(query);
        }}
      >
        <input
          placeholder={mode === "transcript" ? "e.g. yip yip" : "e.g. Azula attacks Aang during the eclipse"}
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
            <label>Mode</label>
            <select value={mode} onChange={(e) => setMode(e.target.value as typeof mode)}>
              <option value="text">Semantic (text)</option>
              <option value="hybrid">Semantic (hybrid)</option>
              <option value="visual">Semantic (visual)</option>
              <option value="transcript">Transcript keyword</option>
            </select>
            {mode === "transcript" && (
              <p className="hint" style={{ marginTop: "0.4rem" }}>
                Exact-ish match on dialogue text. Works even on episodes still waiting on captioning - no
                description/tags needed.
              </p>
            )}
            {mode !== "transcript" && (
              <>
                <label style={{ marginTop: "0.75rem" }}>Shows</label>
                <TagMultiSelect value={shows} onChange={setShows} suggestions={facets?.shows ?? []} placeholder="e.g. Avatar" />
              </>
            )}
            <label style={{ marginTop: "0.75rem" }}>Book</label>
            <input placeholder="1-3" value={book} onChange={(e) => setBook(e.target.value)} />
            {mode !== "transcript" && (
              <>
                <label style={{ marginTop: "0.75rem" }}>Characters</label>
                <TagMultiSelect
                  value={characters}
                  onChange={setCharacters}
                  suggestions={facets?.characters ?? []}
                  placeholder="e.g. Azula"
                />
              </>
            )}
          </div>
        </aside>

        <div className="results">
          {isFetching && <p>Searching…</p>}
          {!isFetching && submittedQuery && resultCount === 0 && (
            <div className="card">
              <p className="empty-state" style={{ padding: 0 }}>
                {mode === "transcript"
                  ? "No dialogue matched that phrase."
                  : "No scenes matched — try a different phrasing, or fewer filters."}
              </p>
            </div>
          )}
          {!submittedQuery && (
            <div className="card">
              <p className="empty-state" style={{ padding: 0 }}>
                {mode === "transcript"
                  ? "Type a phrase from the dialogue to find it."
                  : "Describe a moment from the show to find the matching scene."}
              </p>
            </div>
          )}

          {mode !== "transcript" && sceneResults?.map((r) => <SceneCard key={r.scene.id} scene={r.scene} />)}

          {mode === "transcript" &&
            transcriptResults?.map((r, i) => (
              <div className="card" key={i}>
                <div className="scene-meta">
                  Book {r.book} · Ep {r.episode_number} {r.episode_title ? `(${r.episode_title})` : ""} ·{" "}
                  {formatTime(r.start_time)}
                </div>
                <p style={{ margin: 0 }}>{r.text}</p>
              </div>
            ))}
        </div>
      </div>
    </div>
  );
}
