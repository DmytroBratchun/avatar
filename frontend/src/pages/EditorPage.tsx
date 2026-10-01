import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { EditorProject } from "../api/client";

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

function NewProjectCard() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { data: universes } = useQuery({ queryKey: ["universes"], queryFn: api.listUniverses });
  const [universeId, setUniverseId] = useState<string | null>(null);
  // the first universe until the user picks another
  const universe = universes?.find((u) => u.id === universeId) ?? universes?.[0];

  const [title, setTitle] = useState("");
  const [audio, setAudio] = useState<File | null>(null);
  const [scriptText, setScriptText] = useState("");
  const [shows, setShows] = useState<string[]>([]);
  const { data: defaultLook } = useQuery({ queryKey: ["editorDefaultLook"], queryFn: api.getEditorDefaultLook });
  // null until the user picks one; then the saved default look decides
  const [chosenLayout, setLayout] = useState<string | null>(null);
  const layout = chosenLayout ?? defaultLook?.layout ?? "framed";

  const createMutation = useMutation({
    mutationFn: async () => {
      const project = await api.createEditorProject({ title, universe: universe?.id ?? "", script_text: scriptText || undefined, shows, layout });
      if (audio) await api.uploadEditorAudio(project.id, audio);
      return project;
    },
    onSuccess: (project) => {
      queryClient.invalidateQueries({ queryKey: ["editorProjects"] });
      navigate(`/editor/${project.id}`);
    },
  });

  async function loadScriptFile(file: File | undefined) {
    if (file) setScriptText(await file.text());
  }

  function toggleShow(show: string) {
    setShows((current) => (current.includes(show) ? current.filter((s) => s !== show) : [...current, show]));
  }

  return (
    <div className="card" style={{ marginTop: "0.75rem" }}>
      <h3>New video</h3>

      <label>Title</label>
      <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Why airbending is underrated" />

      <label style={{ marginTop: "0.75rem" }}>Narration audio</label>
      <input type="file" accept="audio/*,.mp3,.wav,.m4a,.flac,.ogg" onChange={(e) => setAudio(e.target.files?.[0] ?? null)} />

      <label style={{ marginTop: "0.75rem" }}>Script (optional)</label>
      <p className="hint" style={{ marginTop: 0 }}>
        The narration text, if you have it - used to get names like Aang or Ba Sing Se right. Paste it or load a file.
      </p>
      <textarea rows={5} value={scriptText} onChange={(e) => setScriptText(e.target.value)} />
      <input
        type="file"
        accept=".txt,.md,text/plain"
        style={{ marginTop: "0.4rem" }}
        onChange={(e) => loadScriptFile(e.target.files?.[0])}
      />

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

      <div style={{ marginTop: "0.75rem" }}>
        <button className="primary" onClick={() => createMutation.mutate()} disabled={!title.trim() || createMutation.isPending}>
          {createMutation.isPending ? "Creating…" : "Create"}
        </button>
      </div>
      {createMutation.isError && <p className="error-text">{(createMutation.error as Error).message}</p>}
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

  if (!settings) return <p>Loading…</p>;

  return (
    <div>
      <h1>Videos</h1>
      <ProjectsRootCard key={settings.projects_root ?? ""} root={settings.projects_root} />

      {settings.projects_root && (
        <>
          <div className="card">
            <h3>Videos</h3>
            {projects && projects.length === 0 && (
              <p className="hint" style={{ margin: 0 }}>
                No videos yet - create one below.
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
          <NewProjectCard />
        </>
      )}
    </div>
  );
}
