import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api/client";
import type { EditorProject } from "../api/client";

// The author's notes on where the script's material comes from. The shot-queries
// stage reads them, so a shot about a comic's story is looked for in the comic.
export default function FootageNotesCard({ project, onChange }: { project: EditorProject; onChange: (p: EditorProject) => void }) {
  const [notes, setNotes] = useState(project.footage_notes ?? "");
  const saved = project.footage_notes ?? "";
  const save = useMutation({
    mutationFn: () => api.updateEditorProject(project.id, { footage_notes: notes }),
    onSuccess: onChange,
  });

  return (
    <details className="card" style={{ marginTop: "0.75rem" }}>
      <summary style={{ cursor: "pointer", fontWeight: 600 }}>
        Footage prompt
        <span className="hint" style={{ margin: "0 0 0 0.5rem", fontWeight: 400 }}>
          {saved.trim() ? "set" : "empty"}
        </span>
      </summary>
      <p className="hint" style={{ marginTop: "0.5rem" }}>
        Where the script's material comes from - Shot queries reads this and looks for each shot where its story is. For
        example: "Keung and June are from the comic The Bounty Hunter and the Tea Brewer. The part about the tea shop in Ba Sing
        Se is from The Promise." Takes effect the next time Shot queries runs.
      </p>
      <textarea
        value={notes}
        onChange={(e) => setNotes(e.target.value)}
        rows={12}
        placeholder="Optional"
        style={{ width: "100%" }}
      />
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
