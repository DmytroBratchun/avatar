import { useMutation } from "@tanstack/react-query";
import { api } from "../api/client";
import type { EditorFeatures, EditorProject } from "../api/client";
import SaveToPreset from "./SaveToPreset";
import Help from "./Help";

// in the order they come up when making a video
const FEATURES: { key: keyof EditorFeatures; label: string; hint: string }[] = [
  { key: "voiceover", label: "Voice-over", hint: "The script voiced through Lumean, on the Voice tab. Off: the narration is uploaded as a file." },
  { key: "quotes", label: "Quotes", hint: "Lines from the show dropped into the narration: the Quotes tab and stage." },
  { key: "comics", label: "Comics", hint: "Comic pages in place of clips, where the narration is about a comic." },
  { key: "photos", label: "Photos", hint: "Photos from Wikimedia Commons for shots about the real world." },
  { key: "maps", label: "Maps", hint: "Journeys drawn on the map of the world (the Map… button of a clip)." },
  { key: "background", label: "Background scenery", hint: "Calm scenery behind framed clips (the Background stage). Off: a blurred copy of each clip." },
  { key: "music", label: "Music", hint: "Music under the narration, composed through Lumean (the Music stage)." },
  { key: "sound_effects", label: "Sound effects", hint: "A page turn for comic pages and a camera shutter for photos." },
];

/** A checkbox per feature. */
export function FeatureChecks({
  features,
  disabled,
  onChange,
}: {
  features: EditorFeatures;
  disabled?: boolean;
  onChange: (changes: Partial<EditorFeatures>) => void;
}) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(14rem, 1fr))", gap: "0.3rem 1rem" }}>
      {FEATURES.map((f) => (
        <label key={f.key} className="form-inline-check" style={{ margin: 0 }} title={f.hint}>
          <input type="checkbox" checked={features[f.key]} disabled={disabled} onChange={(e) => onChange({ [f.key]: e.target.checked })} />
          {f.label}
        </label>
      ))}
    </div>
  );
}

/** The project's features, folded on the Pipeline tab: what this video uses besides
 * footage. A feature turned off disappears from the page and costs nothing. */
export default function FeaturesCard({
  project,
  disabled,
  onChange,
}: {
  project: EditorProject;
  disabled: boolean;
  onChange: (project: EditorProject) => void;
}) {
  const save = useMutation({
    mutationFn: (features: Partial<EditorFeatures>) => api.updateEditorProject(project.id, { features }),
    onSuccess: onChange,
  });
  const off = FEATURES.filter((f) => !project.features[f.key]).map((f) => f.label.toLowerCase());

  return (
    <details className="card">
      <summary style={{ cursor: "pointer" }}>
        <h3 style={{ display: "inline", margin: 0 }}>
          Features
          <Help text="What this video uses besides footage. A feature turned off is gone from this page - its tab, stage, settings and buttons - and nothing is spent on it. The video started with its preset's features." />
        </h3>{" "}
        <span className="hint" style={{ margin: 0 }}>
          {off.length ? `off: ${off.join(", ")}` : "everything on"}
        </span>
      </summary>
      <div style={{ marginTop: "0.6rem" }} />
      <FeatureChecks features={project.features} disabled={disabled || save.isPending} onChange={(changes) => save.mutate(changes)} />
      <div style={{ marginTop: "0.75rem" }}>
        <SaveToPreset project={project} what="features" save={api.saveFeaturesToPreset} disabled={disabled} />
      </div>
      {save.error && <p className="error-text">{(save.error as Error).message}</p>}
    </details>
  );
}
