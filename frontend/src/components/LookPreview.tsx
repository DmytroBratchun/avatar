import { useState } from "react";
import { api } from "../api/client";
import type { EditorProject } from "../api/client";

export default function LookPreview({ project }: { project: EditorProject }) {
  const filled = project.beats.flatMap((b) => b.slots).filter((s) => s.selected != null);
  const [index, setIndex] = useState(0);
  const [loadedUrl, setLoadedUrl] = useState<string | null>(null);
  const [failedUrl, setFailedUrl] = useState<string | null>(null);

  if (filled.length === 0) {
    return (
      <div className="card look-preview">
        <p className="hint" style={{ margin: 0 }}>
          A preview of the look appears here once footage has been found.
        </p>
      </div>
    );
  }

  const slot = filled[index % filled.length];
  const clip = slot.candidates[slot.selected as number];
  // everything the still depends on - a new value makes the browser fetch a new still
  const version = JSON.stringify([project.settings, clip.scene_id, clip.clip_start, project.rendered_at]);
  const url = api.editorPreviewUrl(project.id, index % filled.length, version);
  const step = (by: number) => setIndex((i) => (i + by + filled.length) % filled.length);

  return (
    <div className="card look-preview">
      <div className="card-header">
        <h3 style={{ margin: 0 }}>Preview</h3>
        <div style={{ display: "flex", alignItems: "center", gap: "0.4rem" }}>
          <button className="ghost small" onClick={() => step(-1)} aria-label="Previous clip">
            ◀
          </button>
          <span className="hint" style={{ margin: 0 }}>
            clip {(index % filled.length) + 1} / {filled.length}
          </span>
          <button className="ghost small" onClick={() => step(1)} aria-label="Next clip">
            ▶
          </button>
        </div>
      </div>
      {failedUrl === url ? (
        <p className="error-text">Could not build the preview - see the Backend log.</p>
      ) : (
        <img
          src={url}
          alt="Preview of the current look"
          onLoad={() => setLoadedUrl(url)}
          onError={() => setFailedUrl(url)}
          style={{
            width: "100%",
            aspectRatio: "16 / 9",
            display: "block",
            background: "#000",
            borderRadius: "var(--radius-sm)",
            // dimmed while the new still is being built
            opacity: loadedUrl === url ? 1 : 0.45,
            transition: "opacity 0.15s",
          }}
        />
      )}
      <p className="hint" style={{ marginBottom: 0 }}>
        One still with the current settings; it updates when you change them. The background comes from the last
        render - before the first render it is a blurred copy of the clip.
      </p>
    </div>
  );
}
