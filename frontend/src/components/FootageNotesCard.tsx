import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import type { ReactNode } from "react";
import { api } from "../api/client";
import type { EditorProject } from "../api/client";

// One of the author's notes that the shot-queries stage reads: where the script's
// material comes from (so a shot about a comic's story is looked for in the comic),
// or how the video should look.
export default function FootageNotesCard({
  project,
  field,
  title,
  hint,
  placeholder,
  onChange,
}: {
  project: EditorProject;
  field: "footage_notes" | "visual_notes";
  title: string;
  hint: ReactNode;
  placeholder?: string;
  onChange: (p: EditorProject) => void;
}) {
  const saved = project[field] ?? "";
  const [notes, setNotes] = useState(saved);
  const save = useMutation({
    mutationFn: () => api.updateEditorProject(project.id, { [field]: notes }),
    onSuccess: onChange,
  });

  return (
    <details className="card" style={{ marginTop: "0.75rem" }} open={saved.trim() !== "" && field === "visual_notes"}>
      <summary style={{ cursor: "pointer", fontWeight: 600 }}>
        {title}
        <span className="hint" style={{ margin: "0 0 0 0.5rem", fontWeight: 400 }}>
          {saved.trim() ? "set" : "empty"}
        </span>
      </summary>
      <p className="hint" style={{ marginTop: "0.5rem" }}>
        {hint}
      </p>
      <textarea value={notes} onChange={(e) => setNotes(e.target.value)} rows={8} placeholder={placeholder ?? "Optional"} style={{ width: "100%" }} />
      <div style={{ marginTop: "0.4rem", display: "flex", gap: "0.5rem", alignItems: "center" }}>
        <button className="small primary" onClick={() => save.mutate()} disabled={save.isPending || notes.trim() === saved.trim()}>
          {save.isPending ? "Saving…" : "Save"}
        </button>
        {notes.trim() !== saved.trim() && <span className="hint" style={{ margin: 0 }}>unsaved</span>}
        {save.error && <span className="error-text">{(save.error as Error).message}</span>}
      </div>
    </details>
  );
}
