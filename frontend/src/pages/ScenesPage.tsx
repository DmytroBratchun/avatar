import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import SceneCard from "../components/SceneCard";
import TagMultiSelect from "../components/TagMultiSelect";
import { api } from "../api/client";

export default function ScenesPage() {
  const [searchParams] = useSearchParams();
  const [book, setBook] = useState(searchParams.get("book") ?? "");
  const [episode, setEpisode] = useState(searchParams.get("episode_number") ?? "");
  const [characters, setCharacters] = useState<string[]>([]);
  const [shows, setShows] = useState<string[]>([]);
  const [captionStatus, setCaptionStatus] = useState("");
  // "" = everything, "video" = episodes, "comic" = comic pages
  const [kind, setKind] = useState(searchParams.get("kind") ?? "");
  const [page, setPage] = useState("");
  const [offset, setOffset] = useState(0);
  const limit = 30;

  const { data: facets } = useQuery({ queryKey: ["facets"], queryFn: api.getFacets });

  const { data: scenes, isFetching } = useQuery({
    queryKey: ["scenesBrowse", kind, book, episode, page, characters, shows, captionStatus, offset],
    queryFn: () =>
      api.listScenes({
        kind: kind || undefined,
        page: kind === "comic" && page ? page : undefined,
        book: kind === "comic" ? undefined : book || undefined,
        episode_number: episode || undefined,
        characters: characters.length ? characters : undefined,
        shows: shows.length ? shows : undefined,
        caption_status: captionStatus || undefined,
        limit,
        offset,
      }),
  });

  function resetPage() {
    setOffset(0);
  }

  const comics = facets?.comics ?? [];
  // the shows list follows the kind chosen: episodes' shows, or comic series
  const showChoices =
    kind === "comic" ? comics : kind === "video" ? (facets?.shows ?? []).filter((s) => !comics.includes(s)) : (facets?.shows ?? []);

  return (
    <div>
      <h1>Scenes</h1>
      <p className="hint" style={{ marginTop: "-0.5rem" }}>
        Browse and filter every indexed scene - no search query needed.
      </p>

      <div className="search-layout" style={{ marginTop: "1rem" }}>
        <aside className="filters">
          <div className="card">
            <h3>Filters</h3>
            <label>Type</label>
            <select
              value={kind}
              onChange={(e) => {
                setKind(e.target.value);
                setShows([]);
                setBook("");
                setEpisode("");
                setPage("");
                resetPage();
              }}
            >
              <option value="">Everything</option>
              <option value="video">Episodes</option>
              <option value="comic">Comic pages</option>
            </select>
            <label style={{ marginTop: "0.75rem" }}>{kind === "comic" ? "Comics" : "Shows"}</label>
            <TagMultiSelect
              value={shows}
              onChange={(v) => {
                setShows(v);
                resetPage();
              }}
              suggestions={showChoices}
              placeholder={kind === "comic" ? "e.g. The Promise" : "e.g. Avatar"}
            />
            {kind !== "comic" && (
              <>
                <label style={{ marginTop: "0.75rem" }}>Book</label>
                <input
                  placeholder="1-3"
                  value={book}
                  onChange={(e) => {
                    setBook(e.target.value);
                    resetPage();
                  }}
                />
              </>
            )}
            <label style={{ marginTop: "0.75rem" }}>{kind === "comic" ? "Part" : "Episode"}</label>
            <input
              placeholder="e.g. 3"
              value={episode}
              onChange={(e) => {
                setEpisode(e.target.value);
                resetPage();
              }}
            />
            {kind === "comic" && (
              <>
                <label style={{ marginTop: "0.75rem" }}>Page</label>
                <input
                  placeholder="e.g. 16"
                  value={page}
                  onChange={(e) => {
                    setPage(e.target.value);
                    resetPage();
                  }}
                />
              </>
            )}
            <label style={{ marginTop: "0.75rem" }}>Characters</label>
            <TagMultiSelect
              value={characters}
              onChange={(v) => {
                setCharacters(v);
                resetPage();
              }}
              suggestions={facets?.characters ?? []}
              placeholder="e.g. Azula"
            />
            <label style={{ marginTop: "0.75rem" }}>Caption status</label>
            <select
              value={captionStatus}
              onChange={(e) => {
                setCaptionStatus(e.target.value);
                resetPage();
              }}
            >
              <option value="">Any</option>
              <option value="done">Done</option>
              <option value="pending">Pending</option>
              <option value="error">Error</option>
            </select>
          </div>
        </aside>

        <div className="results">
          {isFetching && <p>Loading…</p>}
          {!isFetching && scenes?.length === 0 && (
            <div className="card">
              <p className="empty-state" style={{ padding: 0 }}>
                No scenes match these filters.
              </p>
            </div>
          )}
          {scenes?.map((s) => (
            <SceneCard key={s.id} scene={s} />
          ))}

          {scenes && scenes.length > 0 && (
            <div className="row-actions" style={{ justifyContent: "flex-start" }}>
              <button className="small ghost" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - limit))}>
                ← Prev
              </button>
              <button className="small ghost" disabled={scenes.length < limit} onClick={() => setOffset(offset + limit)}>
                Next →
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
