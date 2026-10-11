import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api/client";
import type { EditorPreset, EditorPresetPatch } from "../api/client";

/** The Presets tab of the settings: what a new video starts with, by name. Every
 * change is saved at once. The voice and the look are set from a project. */
export default function PresetsPane() {
  const queryClient = useQueryClient();
  const { data: presets } = useQuery({ queryKey: ["editorPresets"], queryFn: api.listPresets });
  const [selectedId, setSelectedId] = useState("default");
  const [newName, setNewName] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const preset = presets?.find((p) => p.id === selectedId) ?? presets?.[0];

  const refresh = (saved?: EditorPreset) => {
    queryClient.invalidateQueries({ queryKey: ["editorPresets"] });
    if (saved) setSelectedId(saved.id);
  };
  const create = useMutation({
    mutationFn: (name: string) => api.createPreset(name, preset?.id ?? "default"),
    onSuccess: (saved) => {
      setNewName(null);
      refresh(saved);
    },
  });
  const remove = useMutation({
    mutationFn: (id: string) => api.deletePreset(id),
    onSuccess: () => {
      setConfirmDelete(false);
      setSelectedId("default");
      refresh();
    },
  });
  const error = (create.error ?? remove.error) as Error | null;

  if (!presets || !preset) return <p className="hint">Loading…</p>;

  return (
    <>
      <p className="hint" style={{ marginTop: 0 }}>
        A preset is what a new video starts with - for a channel, a series, a language. The video copies it when it is made, so
        changing a preset later leaves existing videos as they are.
      </p>
      <div style={{ display: "flex", gap: "0.5rem", alignItems: "center", flexWrap: "wrap" }}>
        <select
          style={{ flex: 1, minWidth: "10rem" }}
          value={preset.id}
          onChange={(e) => {
            setSelectedId(e.target.value);
            setConfirmDelete(false);
          }}
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
  const choose = (patch: EditorPresetPatch) => {
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

      <label style={{ marginTop: "0.75rem" }}>Voice and look</label>
      <p className="hint" style={{ margin: 0 }}>
        Voice: {voice.voice_id ? `${voice.voice_name || voice.voice_id}${voice.language_code ? ` (${voice.language_code})` : ""}` : "none yet"}
        {" · "}
        Look: {look.layout ? `${look.layout}${look.frame ? `, frame ${look.frame}` : ""}${look.frame_rate ? `, ${look.frame_rate} fps` : ""}` : "built-in"}
        <br />
        Set them on a video made from this preset: "Save to preset" on its Voice and Look tabs.
      </p>
      {save.isError && <p className="error-text">{(save.error as Error).message}</p>}
    </div>
  );
}
