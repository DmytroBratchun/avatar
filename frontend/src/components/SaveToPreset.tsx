import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { EditorPreset, EditorProject } from "../api/client";

/** "Save to preset" on a project's Voice and Look tabs and its Features: writes
 * that part of the project into its preset - what new videos made from it start
 * with. One button, to sit in a row with others; what it does is in its tooltip. */
export default function SaveToPreset({
  project,
  what,
  save,
  disabled,
}: {
  project: EditorProject;
  what: string; // "voice" | "look" | "features", for the hint
  save: (projectId: string) => Promise<EditorPreset>;
  disabled?: boolean;
}) {
  const queryClient = useQueryClient();
  const { data: presets } = useQuery({ queryKey: ["editorPresets"], queryFn: api.listPresets });
  const current = presets?.find((p) => p.id === project.preset) ?? presets?.find((p) => p.id === "default");
  const saveMutation = useMutation({
    mutationFn: () => save(project.id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["editorPresets"] }),
  });

  return (
    <span style={{ display: "inline-flex", gap: "0.4rem", alignItems: "center" }}>
      <button
        className="small"
        onClick={() => saveMutation.mutate()}
        disabled={disabled || saveMutation.isPending || !current}
        title={`New videos made from "${current?.name ?? "the preset"}" will start with this ${what}. Existing videos are not changed.`}
      >
        Save {what} to "{current?.name ?? "…"}"
      </button>
      {saveMutation.isSuccess && <span className="hint" style={{ margin: 0 }}>Saved</span>}
      {saveMutation.error && <span className="error-text">{(saveMutation.error as Error).message}</span>}
    </span>
  );
}
