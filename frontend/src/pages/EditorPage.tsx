import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import type { ReactNode } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { EditorProjectSummary } from "../api/client";
import VoiceTextTools from "../components/VoiceTextTools";

function formatDuration(seconds: number | null): string {
  if (seconds == null) return "-";
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}

/** The project page's steps that every video goes through, done or not, and what
 * the video is doing now. */
function ProjectProgress({ p }: { p: EditorProjectSummary }) {
  const steps = [
    { label: "Narration", done: p.word_count > 0 },
    { label: "Footage", done: p.footage_done },
    { label: "Video", done: !!p.rendered_at },
  ];
  const next = steps.find((s) => !s.done);
  return (
    <span className="project-progress">
      {steps.map((s) => (
        <span key={s.label} className={`dot ${s.done ? "done" : ""}`} title={`${s.label}: ${s.done ? "done" : "to do"}`} />
      ))}
      <span className="hint" style={{ margin: 0 }}>
        {p.job?.status === "running" ? (
          <span className="badge accent">{p.job.stage}…</span>
        ) : p.job?.status === "error" ? (
          <span className="error-text">{p.job.stage} failed</span>
        ) : next ? (
          next.label === "Narration" && !p.has_audio ? (p.has_script ? "to voice" : "no audio") : `next: ${next.label.toLowerCase()}`
        ) : (
          "rendered"
        )}
      </span>
    </span>
  );
}

function DeleteProjectDialog({ project, onClose }: { project: EditorProjectSummary; onClose: () => void }) {
  const queryClient = useQueryClient();
  const remove = useMutation({
    mutationFn: () => api.deleteEditorProject(project.id),
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ["editorProjects"] });
      if (result.left.length === 0) onClose();
    },
  });
  const running = project.job?.status === "running";
  const left = remove.data?.left ?? [];
  return (
    <div
      style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.4)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 100 }}
      onClick={() => !remove.isPending && onClose()}
    >
      <div className="card" style={{ width: "460px", maxWidth: "calc(100vw - 32px)" }} onClick={(e) => e.stopPropagation()}>
        <h3 style={{ marginTop: 0 }}>Delete video?</h3>
        {left.length > 0 ? (
          <p style={{ marginTop: 0 }}>
            The video is deleted, but {left.length} file{left.length === 1 ? " is" : "s are"} still open somewhere and could not be removed
            - close {left.length === 1 ? "it" : "them"} and delete the folder by hand: <code style={{ wordBreak: "break-all" }}>{left[0]}</code>
          </p>
        ) : (
          <p style={{ marginTop: 0 }}>
            <b>{project.title}</b> will be deleted with its folder: narration, voice-over, quotes, rendered clips and the finished
            video. This cannot be undone. Its music stays in the music library.
          </p>
        )}
        {running && <p className="error-text">A stage is running - wait for it to finish.</p>}
        {remove.isError && <p className="error-text">{(remove.error as Error).message}</p>}
        <div style={{ display: "flex", gap: "0.5rem", justifyContent: "flex-end" }}>
          {left.length > 0 ? (
            <button className="primary" autoFocus onClick={onClose}>
              Close
            </button>
          ) : (
            <>
              <button className="ghost" autoFocus onClick={onClose} disabled={remove.isPending}>
                Cancel
              </button>
              <button className="danger" onClick={() => remove.mutate()} disabled={running || remove.isPending}>
                {remove.isPending ? "Deleting…" : "Delete"}
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
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

const NEW_VIDEO_TABS = [
  { id: "basics", label: "Basics" },
  { id: "narration", label: "Narration" },
  { id: "editing", label: "Editing" },
] as const;
type NewVideoTab = (typeof NEW_VIDEO_TABS)[number]["id"];

/** A label on the left and its field on the right, the way the form reads. */
function FormRow({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <div className="form-row">
      <label>{label}</label>
      <div>
        {children}
        {hint && <p className="hint" style={{ margin: "0.3rem 0 0" }}>{hint}</p>}
      </div>
    </div>
  );
}

function NewProjectModal({ onClose }: { onClose: () => void }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [tab, setTab] = useState<NewVideoTab>("basics");
  const { data: universes } = useQuery({ queryKey: ["universes"], queryFn: api.listUniverses });
  const { data: presets } = useQuery({ queryKey: ["editorPresets"], queryFn: api.listPresets });
  const [presetId, setPresetId] = useState("default");
  const preset = presets?.find((p) => p.id === presetId) ?? presets?.[0];
  // What the user changed here; every other field follows the preset, and picking
  // another preset puts them all back to following it.
  const [edited, setEdited] = useState<{
    universe?: string;
    shows?: string[];
    layout?: string;
    visual?: string;
    notes?: string;
    runAll?: boolean;
  }>({});
  const edit = (patch: typeof edited) => setEdited((current) => ({ ...current, ...patch }));
  // the preset's universe, else the first one, until the user picks another
  const universe = universes?.find((u) => u.id === (edited.universe ?? preset?.universe)) ?? universes?.[0];
  const shows = edited.shows ?? (universe?.id === preset?.universe ? (preset?.shows ?? []) : []);
  const layout = edited.layout ?? preset?.look.layout ?? "framed";
  const visual = edited.visual ?? preset?.visual_notes ?? "";
  const notes = edited.notes ?? preset?.footage_notes ?? "";
  const runAll = edited.runAll ?? preset?.run_all ?? true;
  const presetVoice = preset?.voice;

  const [title, setTitle] = useState("");
  // where the narration comes from: an audio file, or the script voiced here
  const [narration, setNarration] = useState<"audio" | "script">("audio");
  const [audio, setAudio] = useState<File | null>(null);
  const [scriptText, setScriptText] = useState("");

  const createMutation = useMutation({
    mutationFn: async () => {
      const project = await api.createEditorProject({
        title,
        universe: universe?.id ?? "",
        script_text: scriptText || undefined,
        footage_notes: notes.trim() || undefined,
        visual_notes: visual.trim() || undefined,
        shows,
        layout,
        preset: preset?.id ?? "default",
      });
      if (narration === "audio" && audio) {
        await api.uploadEditorAudio(project.id, audio);
        if (runAll) await api.runEditorAll(project.id);
      }
      return project;
    },
    onSuccess: (project) => {
      queryClient.invalidateQueries({ queryKey: ["editorProjects"] });
      // a project made from a script is voiced next
      navigate(narration === "script" ? `/editor/${project.id}?tab=voice` : `/editor/${project.id}`);
    },
  });
  const busy = createMutation.isPending;
  // what still stops the video from being created, if anything
  const missing = !title.trim() ? "a title (Basics)" : narration === "script" && !scriptText.trim() ? "the script (Narration)" : null;

  async function loadScriptFile(file: File | undefined) {
    if (file) setScriptText(await file.text());
  }

  const scriptField = (rows: number) => (
    <>
      <textarea rows={rows} value={scriptText} disabled={busy} onChange={(e) => setScriptText(e.target.value)} style={{ width: "100%" }} />
      <div style={{ display: "flex", gap: "0.75rem", alignItems: "center", marginTop: "0.4rem", flexWrap: "wrap" }}>
        <input type="file" accept=".txt,.md,text/plain" style={{ flex: 1 }} onChange={(e) => loadScriptFile(e.target.files?.[0])} />
        {scriptText.trim() && (
          <span className="hint" style={{ margin: 0 }}>
            {scriptText.trim().length.toLocaleString()} characters
          </span>
        )}
      </div>
    </>
  );

  function toggleShow(show: string) {
    edit({ shows: shows.includes(show) ? shows.filter((s) => s !== show) : [...shows, show] });
  }

  // a dot on a tab that has something filled in, so nothing set there goes unseen
  const filled: Record<NewVideoTab, boolean> = {
    basics: title.trim() !== "",
    narration: audio !== null || scriptText.trim() !== "",
    editing: visual.trim() !== "" || notes.trim() !== "",
  };

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
      <div className="card settings-modal new-video-modal">
        <div className="card-header">
          <h3 style={{ margin: 0 }}>New video</h3>
          <button className="ghost small" onClick={onClose} disabled={busy}>
            ✕
          </button>
        </div>

        <div className="settings-body">
          <nav className="settings-nav">
            {NEW_VIDEO_TABS.map((t) => (
              <button key={t.id} type="button" className={t.id === tab ? "active" : ""} onClick={() => setTab(t.id)}>
                {t.label}
                {filled[t.id] && <span className="tab-dot" />}
              </button>
            ))}
          </nav>

          <div className="settings-pane">
            {tab === "basics" && (
              <>
                <FormRow label="Preset" hint="Fills in everything below - change any of it for this video.">
                  <select
                    value={preset?.id ?? "default"}
                    onChange={(e) => {
                      setPresetId(e.target.value);
                      setEdited({});
                    }}
                  >
                    {(presets ?? []).map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.name}
                      </option>
                    ))}
                  </select>
                </FormRow>
                <FormRow label="Title">
                  <input autoFocus value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Why airbending is underrated" />
                </FormRow>
                <FormRow label="Universe">
                  <select
                    value={universe?.id ?? ""}
                    // shows of one universe make no sense in another
                    onChange={(e) => edit({ universe: e.target.value, shows: [] })}
                  >
                    {(universes ?? []).map((u) => (
                      <option key={u.id} value={u.id}>
                        {u.name}
                      </option>
                    ))}
                  </select>
                </FormRow>
                <FormRow label="Footage from" hint="None ticked: everything in this universe.">
                  <div className="form-checks">
                    {(universe?.available_shows ?? []).map((show) => (
                      <label key={show}>
                        <input type="checkbox" checked={shows.includes(show)} onChange={() => toggleShow(show)} />
                        {show}
                      </label>
                    ))}
                  </div>
                </FormRow>
                <FormRow label="Layout">
                  <select value={layout} onChange={(e) => edit({ layout: e.target.value })}>
                    <option value="framed">Framed - clip in a frame over a blurred background</option>
                    <option value="simple">Simple - clip fills the screen</option>
                  </select>
                </FormRow>
              </>
            )}

            {tab === "narration" && (
              <>
                <FormRow label="Source">
                  <div className="form-checks">
                    <label>
                      <input type="radio" checked={narration === "audio"} onChange={() => setNarration("audio")} />
                      Audio I already have
                    </label>
                    <label>
                      <input type="radio" checked={narration === "script"} onChange={() => setNarration("script")} />
                      From a script, voiced here
                    </label>
                  </div>
                </FormRow>
                {narration === "audio" ? (
                  <>
                    <FormRow label="Audio" hint="Can also be added later.">
                      <input type="file" accept="audio/*,.mp3,.wav,.m4a,.flac,.ogg" onChange={(e) => setAudio(e.target.files?.[0] ?? null)} />
                    </FormRow>
                    <FormRow label="Script" hint="Optional - the narration text gets names like Aang or Ba Sing Se right in the transcript.">
                      {scriptField(6)}
                    </FormRow>
                  </>
                ) : (
                  <>
                    <FormRow label="Script" hint="Read as written; anything in square brackets is a direction to the voice and is not spoken.">
                      {scriptField(12)}
                      <VoiceTextTools key={preset?.id} text={scriptText} disabled={busy} onChange={setScriptText} instructions={preset?.voice_instructions} />
                    </FormRow>
                    <p className="hint">
                      {presetVoice?.voice_id
                        ? `Voice: ${presetVoice.voice_name || presetVoice.voice_id} (from the preset) - it can be changed before anything is ordered.`
                        : "The voice is chosen on the next page; nothing is ordered until you ask."}
                    </p>
                  </>
                )}
              </>
            )}

            {tab === "editing" && (
              <>
                <FormRow label="Visual wishes" hint="How you want the video to look, in your own words. Shot queries and the background follow it.">
                  <textarea
                    rows={6}
                    value={visual}
                    disabled={busy}
                    onChange={(e) => edit({ visual: e.target.value })}
                    placeholder="More close-ups of faces. Fights over people talking. Avoid the comics unless the script is about them."
                    style={{ width: "100%" }}
                  />
                </FormRow>
                <FormRow label="Material from" hint={'Where the script\'s story comes from - e.g. "Keung and June are from the comic The Bounty Hunter and the Tea Brewer".'}>
                  <textarea rows={4} value={notes} disabled={busy} onChange={(e) => edit({ notes: e.target.value })} placeholder="Optional" style={{ width: "100%" }} />
                </FormRow>
                {narration === "audio" && (
                  <FormRow label="After creating">
                    <label className="form-inline-check" style={{ marginTop: 0 }}>
                      <input type="checkbox" checked={runAll} onChange={(e) => edit({ runAll: e.target.checked })} />
                      Run to the end: transcribe, find footage, render (free; quotes and music are left to you)
                    </label>
                  </FormRow>
                )}
              </>
            )}
          </div>
        </div>

        <div className="settings-footer">
          <button className="primary" onClick={() => createMutation.mutate()} disabled={missing !== null || busy}>
            {busy ? "Creating…" : "Create"}
          </button>
          <button className="ghost" onClick={onClose} disabled={busy}>
            Cancel
          </button>
          {missing && <span className="hint" style={{ margin: 0 }}>Needs {missing}</span>}
          {createMutation.isError && <span className="error-text">{(createMutation.error as Error).message}</span>}
        </div>
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
  const [deleting, setDeleting] = useState<EditorProjectSummary | null>(null);

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
                    <th>Progress</th>
                    <th>Created</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {projects.map((p) => (
                    <tr key={p.id}>
                      <td>
                        <Link to={`/editor/${p.id}`}>{p.title}</Link>
                      </td>
                      <td>{formatDuration(p.audio_duration)}</td>
                      <td>
                        <ProjectProgress p={p} />
                      </td>
                      <td>{new Date(p.created_at).toLocaleDateString()}</td>
                      <td style={{ textAlign: "right" }}>
                        <button className="ghost small" title="Delete this video" onClick={() => setDeleting(p)}>
                          Delete
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
          {creating && <NewProjectModal onClose={() => setCreating(false)} />}
          {deleting && <DeleteProjectDialog project={deleting} onClose={() => setDeleting(null)} />}
        </>
      )}
    </div>
  );
}
