import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api/client";
import type { EditorCandidate, EditorProject, EditorSlot } from "../api/client";
import ScenePicker from "./ScenePicker";

function episodeLabel(c: EditorCandidate): string {
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
  slot,
  active,
  onClick,
}: {
  slot: EditorSlot;
  active: boolean;
  onClick: () => void;
}) {
  const candidate = slot.selected != null ? slot.candidates[slot.selected] : null;
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
        <img
          src={api.keyframeUrl(candidate.scene_id, 0)}
          alt=""
          loading="lazy"
          style={{ width: "100%", aspectRatio: "4 / 3", objectFit: "cover", display: "block", borderRadius: "var(--radius-sm)" }}
        />
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
  disabled,
  onChange,
}: {
  projectId: string;
  beatId: string;
  slotIndex: number;
  slot: EditorSlot;
  projectShows: string[];
  disabled: boolean;
  onChange: (project: EditorProject) => void;
}) {
  const [textQuery, setTextQuery] = useState(slot.text_query);
  const [visualQuery, setVisualQuery] = useState(slot.visual_query);
  const [shows, setShows] = useState<string[]>(slot.shows);
  const available = projectShows;

  const updateMutation = useMutation({
    mutationFn: (payload: { selected?: number; locked?: boolean }) =>
      api.updateEditorSlot(projectId, beatId, slotIndex, payload),
    onSuccess: onChange,
  });
  const searchMutation = useMutation({
    mutationFn: () => api.searchEditorSlot(projectId, beatId, slotIndex, { text_query: textQuery, visual_query: visualQuery, shows }),
    onSuccess: onChange,
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
  const busy = disabled || updateMutation.isPending || searchMutation.isPending || manualMutation.isPending;
  const error = (updateMutation.error ?? searchMutation.error ?? manualMutation.error) as Error | null;

  return (
    <div style={{ display: "flex", gap: "1rem", flexWrap: "wrap", padding: "0.5rem 0" }}>
      <div style={{ width: "360px", maxWidth: "100%" }}>
        {selected ? (
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
              <img
                src={api.keyframeUrl(c.scene_id, 0)}
                alt=""
                loading="lazy"
                style={{ width: "100%", aspectRatio: "4 / 3", objectFit: "cover", display: "block", borderRadius: "var(--radius-sm)" }}
              />
              <div className="hint" style={{ margin: 0, fontSize: "0.65rem" }}>
                {episodeLabel(c)}
              </div>
            </button>
          ))}
        </div>

        <label style={{ marginTop: "0.6rem", fontSize: "0.8rem" }}>What happens (searches scene descriptions)</label>
        <input value={textQuery} onKeyDown={searchOnEnter} onChange={(e) => setTextQuery(e.target.value)} disabled={busy} />
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

        <div style={{ marginTop: "0.5rem", display: "flex", gap: "0.5rem", alignItems: "center" }}>
          <button className="small" onClick={() => setPicking(true)} disabled={busy} title="Pick any scene of any episode yourself">
            Browse scenes…
          </button>
          <button className="small" onClick={() => searchMutation.mutate()} disabled={busy || (!textQuery.trim() && !visualQuery.trim())}>
            {searchMutation.isPending ? "Searching…" : "Search again"}
          </button>
          {selected && (
            <button className="ghost small" onClick={() => updateMutation.mutate({ locked: !slot.locked })} disabled={busy}>
              {slot.locked ? "Unlock" : "Lock"}
            </button>
          )}
          <span className="hint" style={{ margin: 0 }}>
            {slot.locked ? "Locked - automatic selection keeps this clip." : "Picking a clip or searching again locks the slot."}
          </span>
        </div>
        {error && <p className="error-text">{error.message}</p>}
        {picking && (
          <ScenePicker
            shows={projectShows}
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
