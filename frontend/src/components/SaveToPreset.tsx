import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { EditorPreset, EditorProject } from "../api/client";

/** "Save to preset" on a project's Voice or Look tab: writes that part of the
 * project into its preset - what new videos made from it start with. The preset
 * can be switched here; it is remembered on the project. */
export default function SaveToPreset({
  project,
  what,
  save,
  disabled,
  onChange,
}: {
  project: EditorProject;
  what: string; // "voice" | "look", for the hint
  save: (projectId: string) => Promise<EditorPreset>;
  disabled?: boolean;
  onChange: (project: EditorProject) => void;
}) {
  const queryClient = useQueryClient();
  const { data: presets } = useQuery({ queryKey: ["editorPresets"], queryFn: api.listPresets });
  const current = presets?.find((p) => p.id === project.preset) ?? presets?.find((p) => p.id === "default");
  const choose = useMutation({ mutationFn: (preset: string) => api.updateEditorProject(project.id, { preset }), onSuccess: onChange });
  const saveMutation = useMutation({
    mutationFn: () => save(project.id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["editorPresets"] }),
  });
  const error = (choose.error ?? saveMutation.error) as Error | null;

  return (
    <div style={{ marginTop: "0.75rem" }}>
      <div style={{ display: "flex", gap: "0.5rem", alignItems: "center", flexWrap: "wrap" }}>
        <button className="small" onClick={() => saveMutation.mutate()} disabled={disabled || saveMutation.isPending || !current}>
          Save {what} to preset
        </button>
        <select
          style={{ width: "auto" }}
          value={current?.id ?? "default"}
          disabled={disabled || choose.isPending}
          onChange={(e) => {
            saveMutation.reset();
            choose.mutate(e.target.value);
          }}
        >
          {(presets ?? []).map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
        <span className="hint" style={{ margin: 0 }}>
          {saveMutation.isSuccess
            ? `Saved - new videos from "${current?.name}" start with this ${what}.`
            : `New videos made from the preset start with its ${what}. Existing ones are not changed.`}
        </span>
      </div>
      {error && <p className="error-text">{error.message}</p>}
    </div>
  );
}
