import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api/client";
import type { EditorPreset, EditorPresetPatch, EditorProject } from "../api/client";
import { FeatureChecks } from "./FeaturesCard";
import Help from "./Help";

/** The Presets tab of the settings: what a new video starts with, by name. Every
 * change is saved at once. The voice and the look are set from a project.
 *
 * Opened on a video's page it shows that video's preset, and picking another one
 * applies it to the video (its features, look and voice); elsewhere the list only
 * picks which preset to edit. */
export default function PresetsPane({ projectId }: { projectId?: string }) {
  const queryClient = useQueryClient();
  const { data: presets } = useQuery({ queryKey: ["editorPresets"], queryFn: api.listPresets });
  // the same query as the video's page, so both show the same thing
  const { data: project } = useQuery({
    queryKey: ["editorProject", projectId],
    queryFn: () => api.getEditorProject(projectId ?? ""),
    enabled: !!projectId,
  });
  // away from a video: the preset being edited, remembered between openings
  const [editedId, setEditedIdState] = useState(() => {
    try {
      return localStorage.getItem("editedPreset") || "default";
    } catch {
      return "default";
    }
  });
  const setEditedId = (id: string) => {
    setEditedIdState(id);
    try {
      localStorage.setItem("editedPreset", id);
    } catch {
      // private window or blocked storage: just not remembered
    }
  };
  const selectedId = project ? project.preset : editedId;
  const [newName, setNewName] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const preset = presets?.find((p) => p.id === selectedId) ?? presets?.find((p) => p.id === "default") ?? presets?.[0];

  const setProject = (saved: EditorProject) => queryClient.setQueryData(["editorProject", saved.id], saved);
  // on a video's page: the video takes the preset's features, look and voice
  const apply = useMutation({
    mutationFn: (id: string) => api.applyPreset(projectId ?? "", id),
    onSuccess: setProject,
  });
  const select = (id: string) => {
    setConfirmDelete(false);
    if (project) apply.mutate(id);
    else setEditedId(id);
  };
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["editorPresets"] });
  const create = useMutation({
    mutationFn: async (name: string) => {
      const saved = await api.createPreset(name, preset?.id ?? "default");
      // a copy of the video's preset: the video moves to it with nothing to apply
      if (project) setProject(await api.updateEditorProject(project.id, { preset: saved.id }));
      return saved;
    },
    onSuccess: (saved) => {
      setNewName(null);
      if (!project) setEditedId(saved.id);
      refresh();
    },
  });
  const remove = useMutation({
    mutationFn: (id: string) => api.deletePreset(id),
    onSuccess: () => {
      setConfirmDelete(false);
      if (project) queryClient.invalidateQueries({ queryKey: ["editorProject", project.id] });
      else setEditedId("default");
      refresh();
    },
  });
  const error = (create.error ?? remove.error ?? apply.error) as Error | null;

  if (!presets || !preset || (projectId && !project)) return <p className="hint">Loading…</p>;

  return (
    <>
      <label style={{ marginTop: 0 }}>
        {project ? `Preset of "${project.title}"` : "Edit preset"}
        <Help
          text={
            "A preset is what a new video starts with - for a channel, a series, a language: its features, look and voice, and the fields below. " +
            (project
              ? "Picking another preset applies it to this video: its features, look and voice. Universe, shows, script and notes stay. Changes below are made to the preset, for new videos."
              : "This list picks which preset to edit. A video's preset is chosen in the New video dialog, or here when the settings are opened on the video's page.")
          }
        />
      </label>
      <div style={{ display: "flex", gap: "0.5rem", alignItems: "center", flexWrap: "wrap" }}>
        <select
          style={{ flex: 1, minWidth: "10rem" }}
          value={preset.id}
          disabled={apply.isPending}
          onChange={(e) => select(e.target.value)}
        >
          {presets.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
        <button type="button" className="small" onClick={() => setNewName("")} disabled={newName !== null}>
          New preset
        </button>
        {preset.id !== "default" &&
          (confirmDelete ? (
            <>
              <button type="button" className="small danger" onClick={() => remove.mutate(preset.id)} disabled={remove.isPending}>
                Delete "{preset.name}"
              </button>
              <button type="button" className="ghost small" onClick={() => setConfirmDelete(false)}>
                Cancel
              </button>
            </>
          ) : (
            <button type="button" className="ghost small" onClick={() => setConfirmDelete(true)}>
              Delete
            </button>
          ))}
      </div>
      {newName !== null && (
        <form
          style={{ display: "flex", gap: "0.5rem", marginTop: "0.5rem" }}
          onSubmit={(e) => {
            e.preventDefault();
            if (newName.trim()) create.mutate(newName.trim());
          }}
        >
          <input autoFocus style={{ flex: 1 }} placeholder={`Name - starts as a copy of "${preset.name}"`} value={newName} onChange={(e) => setNewName(e.target.value)} />
          <button type="submit" className="small primary" disabled={!newName.trim() || create.isPending}>
            Create
          </button>
          <button type="button" className="ghost small" onClick={() => setNewName(null)}>
            Cancel
          </button>
        </form>
      )}
      {(apply.isPending || apply.isSuccess) && (
        <p className="hint" style={{ margin: "0.3rem 0 0" }}>
          {apply.isPending ? "Applying…" : `Applied to this video: features, look${preset.voice.voice_id ? " and voice" : ""}.`}
        </p>
      )}
      {error && <p className="error-text">{error.message}</p>}
      <PresetFields key={preset.id} preset={preset} onSaved={() => refresh()} />
    </>
  );
}

function PresetFields({ preset, onSaved }: { preset: EditorPreset; onSaved: () => void }) {
  const { data: universes } = useQuery({ queryKey: ["universes"], queryFn: api.listUniverses });
  const [draft, setDraft] = useState(preset);
  const save = useMutation({ mutationFn: (patch: EditorPresetPatch) => api.updatePreset(preset.id, patch), onSuccess: onSaved });
  const universe = universes?.find((u) => u.id === draft.universe);

  // a text field is saved when it loses focus, a choice right away
  const text = (field: "name" | "visual_notes" | "footage_notes" | "voice_instructions") => ({
    value: draft[field],
    onChange: (e: { target: { value: string } }) => setDraft({ ...draft, [field]: e.target.value }),
    onBlur: () => {
      if (draft[field] !== preset[field]) save.mutate({ [field]: draft[field] });
    },
  });
  const choose = (patch: Omit<EditorPresetPatch, "features">) => {
    setDraft({ ...draft, ...patch });
    save.mutate(patch);
  };
  const look = preset.look;
  const voice = preset.voice;

  return (
    <div style={{ marginTop: "1rem" }}>
      <label>Name</label>
      <input {...text("name")} />

      <label style={{ marginTop: "0.75rem" }}>Universe</label>
      <select value={draft.universe} onChange={(e) => choose({ universe: e.target.value, shows: [] })}>
        <option value="">Chosen for each video</option>
        {(universes ?? []).map((u) => (
          <option key={u.id} value={u.id}>
            {u.name}
          </option>
        ))}
      </select>
      {universe && (
        <>
          <label style={{ marginTop: "0.75rem" }}>Footage from</label>
          <div className="form-checks">
            {universe.available_shows.map((show) => (
              <label key={show}>
                <input
                  type="checkbox"
                  checked={draft.shows.includes(show)}
                  onChange={() => choose({ shows: draft.shows.includes(show) ? draft.shows.filter((s) => s !== show) : [...draft.shows, show] })}
                />
                {show}
              </label>
            ))}
          </div>
          <p className="hint" style={{ margin: "0.25rem 0 0" }}>None ticked: everything in this universe.</p>
        </>
      )}

      <label style={{ marginTop: "0.75rem" }}>
        Features
        <Help text="What its videos use besides footage. Off: hidden on the video's page and never run - a video can still turn it on for itself under Features on its Pipeline tab." />
      </label>
      <FeatureChecks
        features={draft.features}
        onChange={(changes) => {
          setDraft({ ...draft, features: { ...draft.features, ...changes } });
          save.mutate({ features: changes });
        }}
      />

      <label style={{ marginTop: "0.75rem" }}>Visual wishes</label>
      <textarea rows={3} style={{ width: "100%" }} placeholder="More close-ups of faces. Fights over people talking." {...text("visual_notes")} />

      <label style={{ marginTop: "0.75rem" }}>Footage prompt</label>
      <textarea rows={3} style={{ width: "100%" }} placeholder="Where the material usually comes from." {...text("footage_notes")} />

      <label style={{ marginTop: "0.75rem" }}>How it should sound</label>
      <input placeholder="For Optimize for voice: calm documentary, whisper the dark parts…" {...text("voice_instructions")} />

      <label className="form-inline-check" style={{ marginTop: "0.75rem" }}>
        <input type="checkbox" checked={draft.run_all} onChange={(e) => choose({ run_all: e.target.checked })} />
        Run to the end once the narration is in
      </label>

      <label style={{ marginTop: "0.75rem" }}>
        Voice and look
        <Help text={'Set them on a video made from this preset: "Save to preset" on its Voice and Look tabs.'} />
      </label>
      <p className="hint" style={{ margin: 0 }}>
        Voice: {voice.voice_id ? `${voice.voice_name || voice.voice_id}${voice.language_code ? ` (${voice.language_code})` : ""}` : "none yet"}
        {" · "}
        Look: {look.layout ? `${look.layout}${look.frame ? `, frame ${look.frame}` : ""}${look.frame_rate ? `, ${look.frame_rate} fps` : ""}` : "built-in"}
      </p>
      {save.isError && <p className="error-text">{(save.error as Error).message}</p>}
    </div>
  );
}
