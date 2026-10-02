import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { api } from "../api/client";
import type { SceneOut } from "../api/client";

function clock(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}

/** "8:18", "8:18.5" or plain seconds -> seconds; null if it isn't a time. */
function parseClock(value: string): number | null {
  const match = value.trim().match(/^(?:(\d+):)?(\d+(?:\.\d+)?)$/);
  return match ? Number(match[1] ?? 0) * 60 + Number(match[2]) : null;
}

/**
 * Popup for choosing footage by hand: pick a show, then an episode, then a scene
 * from a grid of that episode's scenes in order.
 */
export default function ScenePicker({
  shows,
  clipSeconds,
  busy,
  error,
  onPickScene,
  onPickTime,
  onClose,
}: {
  // shows the project may use; empty = everything in the library
  shows: string[];
  clipSeconds: number;
  busy: boolean;
  error: string | null;
  onPickScene: (scene: SceneOut) => void;
  onPickTime: (sourceId: number, start: number) => void;
  onClose: () => void;
}) {
  const { data: sources } = useQuery({ queryKey: ["sources"], queryFn: api.listSources });
  const usable = useMemo(
    () => (sources ?? []).filter((s) => s.scene_count > 0 && (shows.length === 0 || (s.show_title !== null && shows.includes(s.show_title)))),
    [sources, shows]
  );
  const showNames = useMemo(() => [...new Set(usable.map((s) => s.show_title ?? "Untitled"))], [usable]);

  const [show, setShow] = useState<string | null>(null);
  const [sourceId, setSourceId] = useState<number | null>(null);
  const [filter, setFilter] = useState("");
  const [timeText, setTimeText] = useState("");
  // the scene being looked at: a click previews it, a button uses it
  const [previewId, setPreviewId] = useState<number | null>(null);

  const currentShow = show ?? showNames[0] ?? null;
  const episodes = usable.filter((s) => (s.show_title ?? "Untitled") === currentShow);
  const currentSource = episodes.find((e) => e.id === sourceId) ?? episodes[0] ?? null;

  const { data: scenes, isLoading } = useQuery({
    queryKey: ["scenes", "source", currentSource?.id],
    queryFn: () => api.listScenes({ source_id: currentSource!.id, limit: 2000 }),
    enabled: currentSource !== null,
  });
  const ordered = useMemo(() => [...(scenes ?? [])].sort((a, b) => a.start_time - b.start_time), [scenes]);
  const needle = filter.trim().toLowerCase();
  const shown = needle
    ? ordered.filter((s) =>
        [s.context_description, s.visual_description, ...(s.character_tags ?? []), ...(s.location_tags ?? [])]
          .join(" ")
          .toLowerCase()
          .includes(needle)
      )
    : ordered;
  const start = parseClock(timeText);
  const preview = ordered.find((scene) => scene.id === previewId) ?? null;

  return (
    <div
      style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.5)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 100 }}
      onClick={onClose}
    >
      <div
        className="card"
        style={{ width: "min(1100px, 94vw)", height: "88vh", display: "flex", flexDirection: "column" }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="card-header">
          <h2 style={{ margin: 0, fontSize: "1rem" }}>Choose a scene</h2>
          <button className="ghost small" onClick={onClose}>
            ✕
          </button>
        </div>

        <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap", alignItems: "center" }}>
          <select
            style={{ width: "auto", maxWidth: "320px" }}
            value={currentShow ?? ""}
            onChange={(e) => {
              setShow(e.target.value);
              setSourceId(null);
            }}
          >
            {showNames.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
          <select style={{ width: "auto", maxWidth: "380px" }} value={currentSource?.id ?? ""} onChange={(e) => setSourceId(Number(e.target.value))}>
            {episodes.map((e) => (
              <option key={e.id} value={e.id}>
                {e.book != null ? `Book ${e.book} ` : ""}
                {e.episode_number != null ? `Ep ${e.episode_number} · ` : ""}
                {(e.episode_title ?? "").split(" - ").pop()}
              </option>
            ))}
          </select>
          <input
            style={{ flex: 1, minWidth: "180px" }}
            placeholder="Filter by description, character or place"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          />
        </div>
        <p className="hint" style={{ margin: "0.4rem 0" }}>
          {isLoading ? "Loading scenes…" : `${shown.length} scenes, in episode order.`} The clip is cut around the picture you see,
          {clipSeconds.toFixed(1)}s long. Scenes without a caption can only be found by scrolling.
        </p>

        <div style={{ flex: 1, minHeight: 0, display: "flex", gap: "0.75rem" }}>
          <div
            style={{
              flex: 1,
              overflowY: "auto",
              display: "grid",
              gridTemplateColumns: "repeat(auto-fill, minmax(150px, 1fr))",
              gap: "0.5rem",
              alignContent: "start",
            }}
          >
            {shown.map((scene) => (
              <button
                key={scene.id}
                onClick={() => setPreviewId(scene.id)}
                onDoubleClick={() => onPickScene(scene)}
                disabled={busy}
                title={scene.context_description ?? scene.visual_description ?? "No caption"}
                style={{
                  padding: 0,
                  border: scene.id === previewId ? "2px solid var(--accent, #6366f1)" : "2px solid transparent",
                  borderRadius: "var(--radius-sm)",
                  background: "transparent",
                  textAlign: "left",
                }}
              >
                <img
                  src={api.keyframeUrl(scene.id, 0)}
                  alt=""
                  loading="lazy"
                  style={{ width: "100%", aspectRatio: "4 / 3", objectFit: "cover", display: "block", borderRadius: "var(--radius-sm)" }}
                />
                <div className="hint" style={{ margin: "0.1rem 0 0", fontSize: "0.7rem" }}>
                  {clock(scene.start_time)} · {(scene.end_time - scene.start_time).toFixed(1)}s
                </div>
              </button>
            ))}
          </div>

          {/* the scene that was clicked: watch it before deciding */}
          <div style={{ width: "360px", flexShrink: 0, overflowY: "auto" }}>
            {preview ? (
              <>
                {/* #t=start,end plays just this scene out of the episode */}
                <video
                  key={preview.id}
                  src={`${api.streamUrl(preview.source_id)}#t=${preview.start_time},${preview.end_time}`}
                  controls
                  autoPlay
                  muted
                  style={{ width: "100%", display: "block" }}
                />
                <p className="hint" style={{ margin: "0.3rem 0" }}>
                  {clock(preview.start_time)}–{clock(preview.end_time)} · {(preview.end_time - preview.start_time).toFixed(1)}s
                </p>
                <button className="small primary" onClick={() => onPickScene(preview)} disabled={busy}>
                  {busy ? "Adding…" : "Use this scene"}
                </button>
                <p style={{ fontSize: "0.8rem", marginTop: "0.5rem" }}>
                  {preview.context_description ?? preview.visual_description ?? <span className="hint">No caption for this scene.</span>}
                </p>
              </>
            ) : (
              <p className="hint">Click a scene to watch it here. Double-click to use it straight away.</p>
            )}
          </div>
        </div>

        <div style={{ display: "flex", gap: "0.5rem", alignItems: "center", flexWrap: "wrap", marginTop: "0.6rem" }}>
          <span className="hint" style={{ margin: 0 }}>Or start at an exact time in this episode</span>
          <input style={{ width: "5.5rem" }} placeholder="8:18" value={timeText} onChange={(e) => setTimeText(e.target.value)} />
          <button
            className="small"
            disabled={busy || currentSource === null || start === null}
            onClick={() => currentSource !== null && start !== null && onPickTime(currentSource.id, start)}
          >
            Use this time
          </button>
          {timeText && start === null && <span className="error-text">Use a time like 8:18 or 8:18.5</span>}
          {error && <span className="error-text">{error}</span>}
        </div>
      </div>
    </div>
  );
}
