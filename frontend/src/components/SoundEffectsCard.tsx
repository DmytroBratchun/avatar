import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { EditorProject } from "../api/client";

const USED_FOR: Record<string, string> = {
  comic: "when a comic page comes on screen",
  photo: "when a photo comes on screen",
};

// Standard sound effects: generated once (through Lumean) into a library every
// project shares, then played at the start of comic pages and photos.
export default function SoundEffectsCard({
  project,
  disabled,
  onChange,
}: {
  project: EditorProject;
  disabled: boolean;
  onChange: (p: EditorProject) => void;
}) {
  const queryClient = useQueryClient();
  const { data: sounds } = useQuery({ queryKey: ["editorSounds"], queryFn: api.listSounds });
  const generate = useMutation({
    mutationFn: (name: string) => api.generateSound(name),
    onSuccess: (list) => queryClient.setQueryData(["editorSounds"], list),
  });
  const settings = useMutation({
    mutationFn: (payload: { sound_effects?: boolean; sfx_volume?: number }) => api.updateEditorProject(project.id, payload),
    onSuccess: onChange,
  });

  return (
    <div className="card" style={{ marginTop: "0.75rem" }}>
      <h3>Sound effects</h3>
      <p className="hint" style={{ marginTop: 0 }}>
        Short standard sounds, generated once through Lumean and reused by every project. Changes are heard after rendering again.
      </p>
      <div style={{ display: "flex", gap: "0.75rem", alignItems: "center", flexWrap: "wrap", marginBottom: "0.6rem" }}>
        <label style={{ margin: 0, display: "flex", gap: "0.35rem", alignItems: "center" }}>
          <input
            type="checkbox"
            checked={project.settings.sound_effects}
            disabled={disabled || settings.isPending}
            onChange={(e) => settings.mutate({ sound_effects: e.target.checked })}
          />
          Use in this video
        </label>
        <label style={{ margin: 0 }} title="How far below their full level the effects play, in dB">
          Volume
        </label>
        <select
          style={{ width: "auto" }}
          value={project.settings.sfx_volume}
          disabled={disabled || settings.isPending || !project.settings.sound_effects}
          onChange={(e) => settings.mutate({ sfx_volume: Number(e.target.value) })}
        >
          {[6, 9, 12, 15, 18, 22].map((n) => (
            <option key={n} value={n}>
              −{n} dB
            </option>
          ))}
        </select>
      </div>
      {sounds?.map((sound) => {
        const pending = generate.isPending && generate.variables === sound.name;
        return (
          <div key={sound.name} style={{ padding: "0.45rem 0", borderTop: "1px solid var(--border)" }}>
            <div style={{ display: "flex", gap: "0.6rem", alignItems: "center", flexWrap: "wrap" }}>
              <strong style={{ fontSize: "0.9rem", cursor: "help" }} title={sound.prompt}>
                {sound.label}
              </strong>
              <span className="hint" style={{ margin: 0 }}>
                {USED_FOR[sound.used_for] ?? sound.used_for} · {sound.files.length ? `${sound.files.length} variants` : "not generated yet"}
              </span>
              <button className="small" disabled={generate.isPending} onClick={() => generate.mutate(sound.name)}>
                {pending ? "Generating…" : sound.files.length ? "Regenerate" : "Generate"}
              </button>
            </div>
            {sound.files.length > 0 && (
              <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap", marginTop: "0.35rem" }}>
                {sound.files.map((file) => (
                  <audio key={file} controls preload="none" src={api.soundFileUrl(file)} style={{ height: "32px", width: "220px" }} />
                ))}
              </div>
            )}
          </div>
        );
      })}
      {generate.error && <p className="error-text">{(generate.error as Error).message}</p>}
    </div>
  );
}
