import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api/client";
import type { EditorProject, LookFormat } from "../api/client";

const FORMATS: { key: LookFormat; label: string }[] = [
  { key: "4:3", label: "4:3" },
  { key: "16:9", label: "16:9" },
  { key: "wide", label: "Wider" },
  { key: "comic", label: "Comic" },
];

const SOURCE_NOTES = {
  project: "A clip from this video.",
  library: "This video has no clip of this format yet - showing footage of this format from the library.",
  placeholder: "No footage of this format in the library - showing a placeholder of the same shape.",
  none: "There are no comic pages in the library to show.",
};

export default function LookPreview({
  project,
  format,
  onFormat,
}: {
  project: EditorProject;
  format: LookFormat;
  onFormat: (format: LookFormat) => void;
}) {
  // one position per format, so switching back returns to the clip you were on
  const [indexes, setIndexes] = useState<Record<LookFormat, number>>({ "4:3": 0, "16:9": 0, wide: 0, comic: 0 });
  const [loadedUrl, setLoadedUrl] = useState<string | null>(null);
  const [failedUrl, setFailedUrl] = useState<string | null>(null);

  const selections = project.beats.flatMap((b) => b.slots).map((s) => (s.selected != null ? s.candidates[s.selected].scene_id : 0));
  const { data: info } = useQuery({
    // which clips each format has changes when the project's clips do
    queryKey: ["editorPreviewInfo", project.id, selections.join(",")],
    queryFn: () => api.getEditorPreviewInfo(project.id),
  });

  const current = info?.[format];
  const count = current?.count ?? 1;
  const index = count > 0 ? indexes[format] % count : 0;
  // everything the still depends on - a new value makes the browser fetch a new still
  const version = JSON.stringify([project.settings, selections, project.rendered_at]);
  const url = api.editorPreviewUrl(project.id, index, format, version);
  const step = (by: number) => setIndexes((all) => ({ ...all, [format]: (index + by + count) % count }));

  return (
    <div className="card look-preview">
      <div className="card-header">
        <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
          <h3 style={{ margin: 0 }}>Preview</h3>
          {FORMATS.map((f) => (
            <button key={f.key} className={f.key === format ? "small primary" : "small ghost"} onClick={() => onFormat(f.key)}>
              {f.label}
            </button>
          ))}
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: "0.4rem" }}>
          <button className="ghost small" onClick={() => step(-1)} aria-label="Previous clip" disabled={count < 2}>
            ◀
          </button>
          <span className="hint" style={{ margin: 0 }}>
            {index + 1} / {count}
          </span>
          <button className="ghost small" onClick={() => step(1)} aria-label="Next clip" disabled={count < 2}>
            ▶
          </button>
        </div>
      </div>
      {count === 0 ? (
        <p className="hint">Nothing to preview.</p>
      ) : failedUrl === url ? (
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
        {current ? SOURCE_NOTES[current.source] : "Loading…"} One still with the current settings; it updates as you change
        them. The background comes from the last render - before the first render it is a blurred copy of the clip.
      </p>
    </div>
  );
}
