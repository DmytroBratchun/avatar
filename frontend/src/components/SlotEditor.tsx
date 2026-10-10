import { useMutation, useQuery } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { api } from "../api/client";
import type { EditorCandidate, EditorProject, EditorSlot } from "../api/client";
import ScenePicker from "./ScenePicker";

// the picture that stands for a candidate: a photo's own file, else the scene's keyframe
export function candidateImage(c: EditorCandidate, projectId: string): string {
  return c.kind === "photo" && c.image_path ? api.editorPhotoUrl(projectId, c.image_path) : api.keyframeUrl(c.scene_id, 0);
}

// Footage of these shows is rendered as one still frame with a slow camera move, not as video
function useStillShows(): Set<string> {
  const { data: universes } = useQuery({ queryKey: ["universes"], queryFn: api.listUniverses });
  return new Set((universes ?? []).flatMap((u) => u.still_shows ?? []));
}

function isStill(c: EditorCandidate, stillShows: Set<string>): boolean {
  return (c.kind ?? "video") === "video" && c.show_title != null && stillShows.has(c.show_title);
}

// A candidate's thumbnail: for a still, the very frame the video will show
function thumbnail(
  c: EditorCandidate, projectId: string, beatId: string, slotIndex: number, index: number, stillShows: Set<string>,
): string {
  if (!isStill(c, stillShows)) return candidateImage(c, projectId);
  return api.candidateStillUrl(projectId, beatId, slotIndex, index, `${c.clip_start}-${c.still_at ?? "key"}`, 320);
}

// marks a thumbnail whose footage the video shows as a still frame
function StillMark() {
  return (
    <span
      title="Shown in the video as one still frame with a slow pan"
      style={{
        position: "absolute",
        top: "3px",
        left: "3px",
        padding: "0 0.3rem",
        borderRadius: "4px",
        fontSize: "0.6rem",
        fontWeight: 700,
        letterSpacing: "0.04em",
        color: "#fff",
        background: "rgba(0, 0, 0, 0.7)",
      }}
    >
      STILL
    </span>
  );
}

// how many frames of the scene the picker offers
const FRAME_CHOICES = 10;

function frameChoices(c: EditorCandidate): number[] {
  const start = c.scene_start;
  const length = Math.max(0.1, c.scene_end - c.scene_start);
  return Array.from({ length: FRAME_CHOICES }, (_, i) => start + (length * (i + 0.5)) / FRAME_CHOICES);
}

function formatSeconds(t: number): string {
  const m = Math.floor(t / 60);
  const s = t % 60;
  return `${m}:${s.toFixed(1).padStart(4, "0")}`;
}

function episodeLabel(c: EditorCandidate): string {
  if (c.kind === "photo") return "Photo";
  if (c.kind === "comic") {
    return `${c.show_title ?? "Comic"}${c.episode_number != null ? ` ${c.episode_number}` : ""} p.${Math.round(c.scene_start) + 1}`;
  }
  const m = Math.floor(c.clip_start / 60);
  const s = Math.floor(c.clip_start % 60);
  return `B${c.book ?? "?"}E${c.episode_number ?? "?"} ${m}:${s.toString().padStart(2, "0")}`;
}

function matchLabel(c: EditorCandidate): string {
  const parts = [];
  if (c.text_score != null) parts.push(`text ${c.text_score.toFixed(1)}`);
  if (c.wiki_score != null) parts.push(`wiki ${c.wiki_score.toFixed(1)}`);
  if (c.visual_score != null) parts.push(`visual ${c.visual_score.toFixed(1)}`);
  if (c.slivers) parts.push(`${c.slivers} flicker${c.slivers > 1 ? "s" : ""}`);
  return parts.join(" · ");
}

export function SlotThumb({
  projectId,
  beatId,
  slotIndex,
  slot,
  active,
  onClick,
}: {
  projectId: string;
  beatId: string;
  slotIndex: number;
  slot: EditorSlot;
  active: boolean;
  onClick: () => void;
}) {
  const candidate = slot.selected != null ? slot.candidates[slot.selected] : null;
  const stillShows = useStillShows();
  return (
    <button
      onClick={onClick}
      title={candidate ? `${episodeLabel(candidate)} · ${matchLabel(candidate)}` : "No footage found"}
      style={{
        padding: 0,
        width: "112px",
        border: active ? "2px solid var(--accent, #6366f1)" : "2px solid transparent",
        borderRadius: "var(--radius-sm)",
        background: "transparent",
        textAlign: "left",
      }}
    >
      {candidate ? (
        <div style={{ position: "relative" }}>
          <img
            src={thumbnail(candidate, projectId, beatId, slotIndex, slot.selected ?? 0, stillShows)}
            alt=""
            loading="lazy"
            style={{ width: "100%", aspectRatio: "4 / 3", objectFit: "cover", display: "block", borderRadius: "var(--radius-sm)" }}
          />
          {isStill(candidate, stillShows) && <StillMark />}
        </div>
      ) : (
        <div
          className="hint"
          style={{ width: "100%", aspectRatio: "4 / 3", display: "flex", alignItems: "center", justifyContent: "center", margin: 0 }}
        >
          {slot.text_query || slot.visual_query ? "nothing found" : "no query"}
        </div>
      )}
      <div className="hint" style={{ margin: "0.1rem 0 0", fontSize: "0.7rem" }}>
        {(slot.end - slot.start).toFixed(1)}s{slot.locked ? " · locked" : ""}
      </div>
    </button>
  );
}

export function SlotEditor({
  projectId,
  beatId,
  slotIndex,
  slot,
  projectShows,
  projectComics = [],
  disabled,
  onChange,
}: {
  projectId: string;
  beatId: string;
  slotIndex: number;
  slot: EditorSlot;
  projectShows: string[];
  // comic series of the project's universe: their pages can be picked by hand too
  projectComics?: string[];
  disabled: boolean;
  onChange: (project: EditorProject) => void;
}) {
  const [textQuery, setTextQuery] = useState(slot.text_query);
  const [visualQuery, setVisualQuery] = useState(slot.visual_query);
  const [shows, setShows] = useState<string[]>(slot.shows);
  const available = projectShows;

  const updateMutation = useMutation({
    mutationFn: (payload: { selected?: number; locked?: boolean; still_at?: number | null }) =>
      api.updateEditorSlot(projectId, beatId, slotIndex, payload),
    onSuccess: onChange,
  });
  // editing the queries and searching again, in a popup
  const [searching, setSearching] = useState(false);
  const searchMutation = useMutation({
    mutationFn: () => api.searchEditorSlot(projectId, beatId, slotIndex, { text_query: textQuery, visual_query: visualQuery, shows }),
    onSuccess: (project) => {
      setSearching(false);
      onChange(project);
    },
  });

  // picking footage by hand, in a popup
  const [picking, setPicking] = useState(false);
  const manualMutation = useMutation({
    mutationFn: (payload: { scene_id?: number; source_id?: number; start?: number }) =>
      api.setEditorSlotManually(projectId, beatId, slotIndex, payload),
    onSuccess: (project) => {
      setPicking(false);
      onChange(project);
    },
  });

  // Enter in either query field runs the search, same as the button
  const searchOnEnter = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter" && !disabled && !searchMutation.isPending && (textQuery.trim() || visualQuery.trim())) {
      searchMutation.mutate();
    }
  };

  const selected = slot.selected != null ? slot.candidates[slot.selected] : null;
  const stillShows = useStillShows();
  const videoRef = useRef<HTMLVideoElement>(null);
  // the player for picking any frame of a still, shown on request
  const [scrubbing, setScrubbing] = useState(false);
  // the frame of the strip under the mouse, shown enlarged
  const [hoveredFrame, setHoveredFrame] = useState<number | null>(null);
  const busy = disabled || updateMutation.isPending || searchMutation.isPending || manualMutation.isPending;
  const error = (updateMutation.error ?? manualMutation.error) as Error | null; // a search error shows in its popup

  return (
    <div style={{ display: "flex", gap: "1rem", flexWrap: "wrap", padding: "0.5rem 0" }}>
      <div style={{ width: "360px", maxWidth: "100%" }}>
        {selected && selected.kind === "photo" ? (
          <>
            {/* a photo from Wikimedia Commons, for a shot about the real world */}
            <img
              key={selected.scene_id}
              src={candidateImage(selected, projectId)}
              alt=""
              style={{ width: "100%", maxHeight: "520px", objectFit: "contain", background: "var(--bg-hover)" }}
            />
            <p className="hint" style={{ marginTop: "0.3rem" }}>
              Photo for "{slot.photo_query}" · {(selected.clip_end - selected.clip_start).toFixed(1)}s
            </p>
            {selected.credit && <p style={{ fontSize: "0.8rem", margin: 0, wordBreak: "break-word" }}>{selected.credit}</p>}
          </>
        ) : selected && selected.kind === "comic" ? (
          <>
            {/* a comic page: shown whole here; in the video it scrolls from top to bottom */}
            <img
              key={selected.scene_id}
              src={api.keyframeUrl(selected.scene_id, 0)}
              alt=""
              style={{ width: "100%", maxHeight: "520px", objectFit: "contain", background: "var(--bg-hover)" }}
            />
            <p className="hint" style={{ marginTop: "0.3rem" }}>
              {episodeLabel(selected)} · comic page, scrolls for {(selected.clip_end - selected.clip_start).toFixed(1)}s · {matchLabel(selected)}
            </p>
            {selected.description && <p style={{ fontSize: "0.85rem", margin: 0 }}>{selected.description}</p>}
          </>
        ) : selected && slot.selected != null && isStill(selected, stillShows) ? (
          <>
            {/* footage of a show that may only be shown as stills: the frame the video will show */}
            <div style={{ position: "relative" }}>
              <img
                src={api.candidateStillUrl(projectId, beatId, slotIndex, slot.selected, `${selected.clip_start}-${selected.still_at ?? "key"}`)}
                alt=""
                style={{ width: "100%", display: "block", background: "var(--bg-hover)" }}
              />
              <StillMark />
            </div>
            <p className="hint" style={{ margin: "0.2rem 0 0" }}>
              {episodeLabel(selected)} · in the video this frame, with a slow pan, for{" "}
              {(selected.clip_end - selected.clip_start).toFixed(1)}s ·{" "}
              {selected.still_at != null ? `picked frame ${formatSeconds(selected.still_at)}` : "the scene's keyframe"}
              {selected.still_at != null && (
                <>
                  {" "}
                  <button className="ghost small" disabled={busy} onClick={() => updateMutation.mutate({ still_at: null })}>
                    Back to keyframe
                  </button>
                </>
              )}
            </p>
            {/* left as a whole, not per frame: the gaps between frames would make the preview blink */}
            <div
              style={{ position: "relative", display: "flex", gap: "0.2rem", flexWrap: "wrap", marginTop: "0.4rem" }}
              onMouseLeave={() => setHoveredFrame(null)}
            >
              {/* the hovered frame, enlarged, to look at before picking it */}
              {hoveredFrame != null && (
                <div
                  style={{
                    position: "absolute",
                    bottom: "100%",
                    left: 0,
                    marginBottom: "0.3rem",
                    width: "min(560px, 90vw)",
                    zIndex: 50,
                    pointerEvents: "none",
                    background: "var(--bg, #000)",
                    borderRadius: "var(--radius-sm)",
                    boxShadow: "0 6px 24px rgba(0, 0, 0, 0.45)",
                    overflow: "hidden",
                  }}
                >
                  <img src={api.frameUrl(selected.source_id, hoveredFrame, 1120)} alt="" style={{ width: "100%", display: "block" }} />
                  <div className="hint" style={{ margin: 0, padding: "0.2rem 0.4rem" }}>
                    Click to use the frame at {formatSeconds(hoveredFrame)}
                  </div>
                </div>
              )}
              {frameChoices(selected).map((t) => (
                <button
                  key={t}
                  onClick={() => {
                    // the button is disabled while saving, which would leave the preview without a mouseleave
                    setHoveredFrame(null);
                    updateMutation.mutate({ still_at: Math.round(t * 1000) / 1000 });
                  }}
                  onMouseEnter={() => setHoveredFrame(t)}
                  disabled={busy}
                  style={{ padding: 0, width: "68px", border: "2px solid transparent", background: "transparent" }}
                >
                  <img
                    src={api.frameUrl(selected.source_id, t)}
                    alt=""
                    loading="lazy"
                    style={{ width: "100%", aspectRatio: "16 / 9", objectFit: "cover", display: "block", borderRadius: "var(--radius-sm)" }}
                  />
                </button>
              ))}
            </div>
            {/* any other frame: find it in the player (it can be scrubbed past the clip) and take it */}
            {scrubbing ? (
              <>
                <video
                  ref={videoRef}
                  key={`${selected.scene_id}-${selected.clip_start}`}
                  src={`${api.streamUrl(selected.source_id)}#t=${selected.scene_start},${selected.scene_end}`}
                  controls
                  muted
                  preload="metadata"
                  style={{ width: "100%", marginTop: "0.4rem" }}
                />
                <div style={{ display: "flex", gap: "0.5rem" }}>
                  <button
                    className="small"
                    disabled={busy}
                    onClick={() => {
                      const t = videoRef.current?.currentTime;
                      if (t != null) {
                        updateMutation.mutate({ still_at: Math.round(t * 1000) / 1000 });
                        setScrubbing(false);
                      }
                    }}
                  >
                    Use this frame
                  </button>
                  <button className="ghost small" onClick={() => setScrubbing(false)}>
                    Cancel
                  </button>
                </div>
              </>
            ) : (
              <button className="ghost small" style={{ marginTop: "0.3rem" }} onClick={() => setScrubbing(true)} disabled={busy}>
                Other frame…
              </button>
            )}
          </>
        ) : selected ? (
          <>
            {/* #t=start,end makes the browser play just this clip out of the episode */}
            <video
              key={`${selected.scene_id}-${selected.clip_start}`}
              src={`${api.streamUrl(selected.source_id)}#t=${selected.clip_start},${selected.clip_end}`}
              controls
              muted
              autoPlay
              style={{ width: "100%" }}
            />
            <div className="hint" style={{ margin: "0.2rem 0 0" }}>
              {episodeLabel(selected)} · {(selected.clip_end - selected.clip_start).toFixed(1)}s · {matchLabel(selected)}
            </div>
            {selected.description && <p style={{ margin: "0.3rem 0 0", fontSize: "0.8rem" }}>{selected.description}</p>}
          </>
        ) : (
          <p className="hint">No footage selected.</p>
        )}
      </div>

      <div style={{ flex: 1, minWidth: "320px" }}>
        <div style={{ display: "flex", gap: "0.3rem", flexWrap: "wrap" }}>
          {slot.candidates.map((c, i) => (
            <button
              key={`${c.scene_id}-${c.clip_start}`}
              onClick={() => updateMutation.mutate({ selected: i })}
              disabled={busy}
              title={`${episodeLabel(c)} · ${matchLabel(c)}${c.description ? `\n${c.description}` : ""}`}
              style={{
                padding: 0,
                width: "96px",
                border: i === slot.selected ? "2px solid var(--accent, #6366f1)" : "2px solid transparent",
                borderRadius: "var(--radius-sm)",
                background: "transparent",
              }}
            >
              <div style={{ position: "relative" }}>
                <img
                  src={thumbnail(c, projectId, beatId, slotIndex, i, stillShows)}
                  alt=""
                  loading="lazy"
                  style={{ width: "100%", aspectRatio: "4 / 3", objectFit: "cover", display: "block", borderRadius: "var(--radius-sm)" }}
                />
                {isStill(c, stillShows) && <StillMark />}
              </div>
              <div className="hint" style={{ margin: 0, fontSize: "0.65rem" }}>
                {episodeLabel(c)}
              </div>
            </button>
          ))}
        </div>

        {slot.moment && (
          <p className="hint" style={{ margin: "0.6rem 0 0" }} title="Found by reading the episodes it happens in, before any other shot is searched">
            <span className="badge accent">specific moment</span> {slot.moment}
          </p>
        )}
        <div style={{ marginTop: "0.6rem", display: "flex", gap: "0.5rem", alignItems: "center", flexWrap: "wrap" }}>
          <button className="small" onClick={() => setSearching(true)} disabled={busy} title="Edit this slot's queries and search again">
            Search again…
          </button>
          <button className="small" onClick={() => setPicking(true)} disabled={busy} title="Pick any scene of any episode yourself">
            Browse scenes…
          </button>
          {selected && (
            <button
              className="ghost small"
              onClick={() => updateMutation.mutate({ locked: !slot.locked })}
              disabled={busy}
              title={slot.locked ? "Locked: automatic selection keeps this clip" : "Picking a clip or searching again locks the slot"}
            >
              {slot.locked ? "Unlock" : "Lock"}
            </button>
          )}
        </div>
        {error && <p className="error-text">{error.message}</p>}
        {searching && (
          <div
            style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.5)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 100 }}
            onClick={() => setSearching(false)}
          >
            <div className="card" style={{ width: "min(640px, 94vw)" }} onClick={(e) => e.stopPropagation()}>
              <div className="card-header">
                <h2 style={{ margin: 0, fontSize: "1rem" }}>Search again</h2>
                <button className="ghost small" onClick={() => setSearching(false)}>
                  Close
                </button>
              </div>
              <label style={{ fontSize: "0.8rem" }}>What happens (searches scene descriptions)</label>
              <input value={textQuery} onKeyDown={searchOnEnter} onChange={(e) => setTextQuery(e.target.value)} disabled={busy} autoFocus />
              <label style={{ marginTop: "0.4rem", fontSize: "0.8rem" }}>What the frame looks like (searches images, no names)</label>
              <input value={visualQuery} onKeyDown={searchOnEnter} onChange={(e) => setVisualQuery(e.target.value)} disabled={busy} />
              {available.length > 1 && (
                <>
                  <label style={{ marginTop: "0.4rem", fontSize: "0.8rem" }}>
                    Search in{shows.length === 0 ? " (all)" : ""}
                  </label>
                  <div style={{ display: "flex", gap: "0.75rem", flexWrap: "wrap" }}>
                    {available.map((show) => (
                      <label key={show} style={{ display: "flex", alignItems: "center", gap: "0.3rem", margin: 0, fontSize: "0.8rem" }}>
                        <input
                          type="checkbox"
                          style={{ width: "auto" }}
                          checked={shows.includes(show)}
                          disabled={busy}
                          onChange={() => setShows(shows.includes(show) ? shows.filter((s) => s !== show) : [...shows, show])}
                        />
                        {show}
                      </label>
                    ))}
                  </div>
                </>
              )}
              <div style={{ marginTop: "0.8rem", display: "flex", gap: "0.5rem", alignItems: "center" }}>
                <button onClick={() => searchMutation.mutate()} disabled={busy || (!textQuery.trim() && !visualQuery.trim())}>
                  {searchMutation.isPending ? "Searching…" : "Search"}
                </button>
                <span className="hint" style={{ margin: 0 }}>The result locks the slot.</span>
              </div>
              {searchMutation.error && <p className="error-text">{(searchMutation.error as Error).message}</p>}
            </div>
          </div>
        )}
        {picking && (
          <ScenePicker
            shows={[...projectShows, ...projectComics]}
            clipSeconds={slot.end - slot.start}
            busy={manualMutation.isPending}
            error={manualMutation.error ? (manualMutation.error as Error).message : null}
            onPickScene={(scene) => manualMutation.mutate({ scene_id: scene.id })}
            onPickTime={(sourceId, start) => manualMutation.mutate({ source_id: sourceId, start })}
            onClose={() => setPicking(false)}
          />
        )}
      </div>
    </div>
  );
}
