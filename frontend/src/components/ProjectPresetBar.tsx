import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { EditorProject } from "../api/client";

/** The project's preset, a button in the line under its title. Clicking it opens the settings
 * on Presets, which show this preset and apply another one picked there. */
export default function ProjectPresetBar({ project }: { project: EditorProject }) {
  const { data: presets } = useQuery({ queryKey: ["editorPresets"], queryFn: api.listPresets });
  const own = presets?.find((p) => p.id === project.preset) ?? presets?.find((p) => p.id === "default");

  return (
    <button
      className="ghost small"
      style={{ padding: "0 0.3rem" }}
      title="This video's preset - opens it in the settings; pick another one there to apply it"
      onClick={() => window.dispatchEvent(new CustomEvent("open-settings", { detail: "presets" }))}
    >
      {own?.name ?? "…"}
    </button>
  );
}
