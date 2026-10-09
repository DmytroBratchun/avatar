import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { EditorProject } from "../api/client";
import VoiceTextTools from "../components/VoiceTextTools";

function formatDuration(seconds: number | null): string {
  if (seconds == null) return "-";
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}

function projectStage(p: EditorProject): string {
  if (p.job?.status === "running") return `${p.job.stage}…`;
  if (p.beats.length > 0) return `${p.beats.length} beats`;
  if (p.word_count > 0) return "transcribed";
  if (p.audio_file) return "audio added";
  return "no audio";
}

function ProjectsRootCard({ root }: { root: string | null }) {
  const queryClient = useQueryClient();
  const [value, setValue] = useState(root ?? "");
  const [editing, setEditing] = useState(!root);

  const saveMutation = useMutation({
    mutationFn: () => api.setEditorProjectsRoot(value),
    onSuccess: () => {
      setEditing(false);
      queryClient.invalidateQueries({ queryKey: ["editorSettings"] });
      queryClient.invalidateQueries({ queryKey: ["editorProjects"] });
    },
  });

  if (!editing) {
    return (
      <p className="hint" style={{ marginTop: "-0.5rem" }}>
        Projects folder: <code>{root}</code>{" "}
        <button className="ghost small" onClick={() => setEditing(true)}>
          change
        </button>
      </p>
    );
  }

  return (
    <div className="card">
      <h3>Projects folder</h3>
      <p className="hint">
        Each video gets its own sub-folder here with its audio, transcript and project state. Changing this does not
        move existing projects.
      </p>
      <div style={{ display: "flex", gap: "0.5rem" }}>
        <input
          style={{ flex: 1 }}
          placeholder="D:\videos\projects"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          spellCheck={false}
        />
        <button className="primary" onClick={() => saveMutation.mutate()} disabled={!value.trim() || saveMutation.isPending}>
          Save
        </button>
        {root && (
          <button className="ghost" onClick={() => setEditing(false)}>
            Cancel
          </button>
        )}
      </div>
      {saveMutation.isError && <p className="error-text">{(saveMutation.error as Error).message}</p>}
    </div>
  );
}

function NewProjectModal({ onClose }: { onClose: () => void }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { data: universes } = useQuery({ queryKey: ["universes"], queryFn: api.listUniverses });
  const [universeId, setUniverseId] = useState<string | null>(null);
  // the first universe until the user picks another
  const universe = universes?.find((u) => u.id === universeId) ?? universes?.[0];

  const [title, setTitle] = useState("");
  // where the narration comes from: an audio file, or the script voiced here
  const [narration, setNarration] = useState<"audio" | "script">("audio");
  const [audio, setAudio] = useState<File | null>(null);
  const [scriptText, setScriptText] = useState("");
  const [shows, setShows] = useState<string[]>([]);
  const { data: defaultLook } = useQuery({ queryKey: ["editorDefaultLook"], queryFn: api.getEditorDefaultLook });
  const { data: defaultVoice } = useQuery({ queryKey: ["editorDefaultVoice"], queryFn: api.getDefaultVoice });
  // null until the user picks one; then the saved default look decides
  const [chosenLayout, setLayout] = useState<string | null>(null);
  const layout = chosenLayout ?? defaultLook?.layout ?? "framed";

  const createMutation = useMutation({
    mutationFn: async () => {
      const project = await api.createEditorProject({ title, universe: universe?.id ?? "", script_text: scriptText || undefined, shows, layout });
      if (narration === "audio" && audio) await api.uploadEditorAudio(project.id, audio);
      return project;
    },
    onSuccess: (project) => {
      queryClient.invalidateQueries({ queryKey: ["editorProjects"] });
      // a project made from a script is voiced next
      navigate(narration === "script" ? `/editor/${project.id}?tab=voice` : `/editor/${project.id}`);
    },
  });
  const busy = createMutation.isPending;
  const ready = title.trim() !== "" && (narration === "audio" || scriptText.trim() !== "");

  async function loadScriptFile(file: File | undefined) {
    if (file) setScriptText(await file.text());
  }

  function toggleShow(show: string) {
    setShows((current) => (current.includes(show) ? current.filter((s) => s !== show) : [...current, show]));
  }

  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.4)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 100,
      }}
    >
      {/* no closing on a click outside: a pasted script would be lost */}
      <div className="card" style={{ width: "680px", maxWidth: "94vw", maxHeight: "92vh", overflowY: "auto" }}>
        <div className="card-header">
          <h3 style={{ margin: 0 }}>New video</h3>
          <button className="ghost small" onClick={onClose} disabled={busy}>
            Close
          </button>
        </div>

        <label>Title</label>
        <input autoFocus value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Why airbending is underrated" />

        <label style={{ marginTop: "0.75rem" }}>Narration</label>
        <div style={{ display: "flex", gap: "1rem", flexWrap: "wrap" }}>
          <label style={{ display: "flex", alignItems: "center", gap: "0.35rem", margin: 0 }}>
            <input type="radio" style={{ width: "auto" }} checked={narration === "audio"} onChange={() => setNarration("audio")} />
            Audio I already have
          </label>
          <label style={{ display: "flex", alignItems: "center", gap: "0.35rem", margin: 0 }}>
            <input type="radio" style={{ width: "auto" }} checked={narration === "script"} onChange={() => setNarration("script")} />
            From a script, voiced here
          </label>
        </div>

        {narration === "audio" && (
          <>
            <label style={{ marginTop: "0.75rem" }}>Narration audio</label>
            <input type="file" accept="audio/*,.mp3,.wav,.m4a,.flac,.ogg" onChange={(e) => setAudio(e.target.files?.[0] ?? null)} />
          </>
        )}

        <label style={{ marginTop: "0.75rem" }}>{narration === "script" ? "Script" : "Script (optional)"}</label>
        <p className="hint" style={{ marginTop: 0 }}>
          {narration === "script"
            ? "The narration text, read as written. Anything in square brackets is a direction to the voice and is not spoken. Paste it or load a file."
            : "The narration text, if you have it - used to get names like Aang or Ba Sing Se right. Paste it or load a file."}
        </p>
        <textarea rows={narration === "script" ? 12 : 5} value={scriptText} disabled={busy} onChange={(e) => setScriptText(e.target.value)} />
        <div style={{ display: "flex", gap: "0.75rem", alignItems: "center", marginTop: "0.4rem", flexWrap: "wrap" }}>
          <input type="file" accept=".txt,.md,text/plain" style={{ flex: 1 }} onChange={(e) => loadScriptFile(e.target.files?.[0])} />
          {scriptText.trim() && (
            <span className="hint" style={{ margin: 0 }}>
              {scriptText.trim().length.toLocaleString()} characters
            </span>
          )}
        </div>
        {narration === "script" && (
          <>
            <VoiceTextTools text={scriptText} disabled={busy} onChange={setScriptText} />
            <p className="hint">
              {defaultVoice?.voice_id
                ? `Voice: ${defaultVoice.voice_name || defaultVoice.voice_id} (the default) - it can be changed on the Voice tab before anything is ordered.`
                : "The voice is chosen on the project's Voice tab, which opens next; nothing is ordered until you ask there."}
            </p>
          </>
        )}

        <label style={{ marginTop: "0.75rem" }}>Universe</label>
        <select
          value={universe?.id ?? ""}
          onChange={(e) => {
            setUniverseId(e.target.value);
            setShows([]); // shows of one universe make no sense in another
          }}
        >
          {(universes ?? []).map((u) => (
            <option key={u.id} value={u.id}>
              {u.name}
            </option>
          ))}
        </select>

        <label style={{ marginTop: "0.75rem" }}>Footage from</label>
        <div style={{ display: "flex", gap: "1rem", flexWrap: "wrap" }}>
          {(universe?.available_shows ?? []).map((show) => (
            <label key={show} style={{ display: "flex", alignItems: "center", gap: "0.35rem", margin: 0 }}>
              <input type="checkbox" style={{ width: "auto" }} checked={shows.includes(show)} onChange={() => toggleShow(show)} />
              {show}
            </label>
          ))}
        </div>
        <p className="hint" style={{ marginTop: "0.3rem" }}>Leave all unchecked to use everything in this universe.</p>

        <label style={{ marginTop: "0.75rem" }}>Layout</label>
        <select value={layout} onChange={(e) => setLayout(e.target.value)}>
          <option value="framed">Framed - clip in a frame over a blurred background</option>
          <option value="simple">Simple - clip fills the screen</option>
        </select>

        <div style={{ display: "flex", gap: "0.5rem", marginTop: "0.9rem" }}>
          <button className="primary" onClick={() => createMutation.mutate()} disabled={!ready || busy}>
            {busy ? "Creating…" : "Create"}
          </button>
          <button className="ghost" onClick={onClose} disabled={busy}>
            Cancel
          </button>
        </div>
        {createMutation.isError && <p className="error-text">{(createMutation.error as Error).message}</p>}
      </div>
    </div>
  );
}

export default function EditorPage() {
  const { data: settings } = useQuery({ queryKey: ["editorSettings"], queryFn: api.getEditorSettings });
  const { data: projects } = useQuery({
    queryKey: ["editorProjects"],
    queryFn: api.listEditorProjects,
    enabled: !!settings?.projects_root,
  });

  const [creating, setCreating] = useState(false);

  if (!settings) return <p>Loading…</p>;

  return (
    <div>
      <h1>Videos</h1>
      <ProjectsRootCard key={settings.projects_root ?? ""} root={settings.projects_root} />

      {settings.projects_root && (
        <>
          <div className="card">
            <div className="card-header">
              <h3 style={{ margin: 0 }}>Videos</h3>
              <button className="primary" onClick={() => setCreating(true)}>
                New video
              </button>
            </div>
            {projects && projects.length === 0 && (
              <p className="hint" style={{ margin: 0 }}>
                No videos yet.
              </p>
            )}
            {projects && projects.length > 0 && (
              <table>
                <thead>
                  <tr>
                    <th>Title</th>
                    <th>Length</th>
                    <th>Stage</th>
                    <th>Created</th>
                  </tr>
                </thead>
                <tbody>
                  {projects.map((p) => (
                    <tr key={p.id}>
                      <td>
                        <Link to={`/editor/${p.id}`}>{p.title}</Link>
                      </td>
                      <td>{formatDuration(p.audio_duration)}</td>
                      <td>{projectStage(p)}</td>
                      <td>{new Date(p.created_at).toLocaleDateString()}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
          {creating && <NewProjectModal onClose={() => setCreating(false)} />}
        </>
      )}
    </div>
  );
}
