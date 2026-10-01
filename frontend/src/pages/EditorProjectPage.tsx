import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import type { EditorBeat, EditorProject, EditorStage } from "../api/client";
import LookPreview from "../components/LookPreview";
import QuotesCard from "../components/QuotesCard";
import RenderSettingsCard from "../components/RenderSettingsCard";
import { SlotEditor, SlotThumb } from "../components/SlotEditor";

function formatTime(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = seconds - m * 60;
  return `${m}:${s.toFixed(1).padStart(4, "0")}`;
}

function StageRow({
  label,
  description,
  state,
  action,
  onRun,
  disabled,
}: {
  label: string;
  description: string;
  state: string;
  action: string;
  onRun: () => void;
  disabled: boolean;
}) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: "0.75rem", padding: "0.35rem 0" }}>
      <div style={{ flex: 1 }}>
        <strong style={{ fontSize: "0.9rem" }}>{label}</strong>
        <span className="hint" style={{ margin: "0 0 0 0.5rem" }}>
          {description}
        </span>
      </div>
      <span className="hint" style={{ margin: 0 }}>
        {state}
      </span>
      <button className="small" onClick={onRun} disabled={disabled}>
        {action}
      </button>
    </div>
  );
}

function BeatRow({
  projectId,
  beat,
  projectShows,
  playing,
  onPlay,
  disabled,
  onChange,
}: {
  projectId: string;
  projectShows: string[];
  beat: EditorBeat;
  playing: boolean;
  onPlay: () => void;
  disabled: boolean;
  onChange: (project: EditorProject) => void;
}) {
  const [openSlot, setOpenSlot] = useState<number | null>(null);
  const slot = openSlot != null ? beat.slots[openSlot] : undefined;

  return (
    <>
      <tr style={{ background: playing ? "var(--accent-soft, rgba(99,102,241,0.12))" : undefined }}>
        <td style={{ whiteSpace: "nowrap", verticalAlign: "top" }}>
          <button className="ghost small" onClick={onPlay} title="Play this beat">
            {playing ? "■" : "▶"}
          </button>
        </td>
        <td style={{ whiteSpace: "nowrap", verticalAlign: "top" }}>
          {formatTime(beat.start)}
          <div className="hint" style={{ margin: 0 }}>
            {(beat.end - beat.start).toFixed(1)}s
          </div>
        </td>
        <td style={{ verticalAlign: "top" }}>
          {beat.summary && <div style={{ fontWeight: 600, fontSize: "0.85rem" }}>{beat.summary}</div>}
          <div style={{ color: "var(--text-secondary)" }}>{beat.text}</div>
        </td>
        <td style={{ verticalAlign: "top" }}>
          <div style={{ display: "flex", gap: "0.3rem" }}>
            {beat.slots.map((s, i) => (
              <SlotThumb key={i} slot={s} active={openSlot === i} onClick={() => setOpenSlot(openSlot === i ? null : i)} />
            ))}
            {beat.backgrounds.length > 0 && (
              <div style={{ width: "64px", marginLeft: "0.4rem", opacity: 0.75 }} title={`Background: ${beat.background_query}`}>
                <img
                  src={api.keyframeUrl(beat.backgrounds[0].scene_id, 0)}
                  alt=""
                  loading="lazy"
                  style={{ width: "100%", aspectRatio: "4 / 3", objectFit: "cover", display: "block", borderRadius: "var(--radius-sm)" }}
                />
                <div className="hint" style={{ margin: "0.1rem 0 0", fontSize: "0.7rem" }}>
                  background
                </div>
              </div>
            )}
          </div>
        </td>
      </tr>
      {slot && openSlot != null && (
        <tr>
          <td colSpan={4}>
            <SlotEditor
              // remount when the queries change so the inputs pick up the new text
              key={`${openSlot}-${slot.text_query}-${slot.visual_query}-${slot.shows.join()}`}
              projectId={projectId}
              beatId={beat.id}
              slotIndex={openSlot}
              slot={slot}
              projectShows={projectShows}
              disabled={disabled}
              onChange={onChange}
            />
          </td>
        </tr>
      )}
    </>
  );
}

const TABS = ["pipeline", "quotes", "clips", "look", "video"] as const;
type Tab = (typeof TABS)[number];

export default function EditorProjectPage() {
  const { id = "" } = useParams();
  const queryClient = useQueryClient();
  const audioRef = useRef<HTMLAudioElement>(null);
  const [playingBeat, setPlayingBeat] = useState<EditorBeat | null>(null);
  // the open tab lives in the URL so a reload or a shared link lands on the same one
  const [searchParams, setSearchParams] = useSearchParams();
  const requested = searchParams.get("tab") as Tab | null;
  const tab: Tab = requested && TABS.includes(requested) ? requested : "pipeline";
  const setTab = (next: Tab) => setSearchParams(next === "pipeline" ? {} : { tab: next }, { replace: true });

  const { data: project, error } = useQuery({
    queryKey: ["editorProject", id],
    queryFn: () => api.getEditorProject(id),
    // poll only while a stage is running
    refetchInterval: (query) => ((query.state.data as EditorProject | undefined)?.job?.status === "running" ? 1500 : false),
  });

  const { data: universes } = useQuery({ queryKey: ["universes"], queryFn: api.listUniverses });

  const setProject = (p: EditorProject) => queryClient.setQueryData(["editorProject", id], p);

  const runMutation = useMutation({
    mutationFn: (stage: EditorStage) => api.runEditorStage(id, stage),
    onSuccess: setProject,
  });
  const uploadMutation = useMutation({
    mutationFn: (file: File) => api.uploadEditorAudio(id, file),
    onSuccess: setProject,
  });

  // stop playback at the end of the beat being auditioned
  useEffect(() => {
    const audio = audioRef.current;
    if (!audio || !playingBeat) return;
    const onTime = () => {
      if (audio.currentTime >= playingBeat.end) {
        audio.pause();
        setPlayingBeat(null);
      }
    };
    audio.addEventListener("timeupdate", onTime);
    return () => audio.removeEventListener("timeupdate", onTime);
  }, [playingBeat]);

  function togglePlay(beat: EditorBeat) {
    const audio = audioRef.current;
    if (!audio) return;
    if (playingBeat?.id === beat.id) {
      audio.pause();
      setPlayingBeat(null);
      return;
    }
    audio.currentTime = beat.start;
    void audio.play();
    setPlayingBeat(beat);
  }

  if (error) return <p className="error-text">{(error as Error).message}</p>;
  if (!project) return <p>Loading…</p>;

  const running = project.job?.status === "running";
  const busy = running || runMutation.isPending || uploadMutation.isPending;
  const mutationError = (runMutation.error ?? uploadMutation.error) as Error | null;

  const projectUniverse = universes?.find((u) => u.id === project.universe) ?? universes?.find((u) => project.shows.some((s) => u.shows.includes(s))) ?? universes?.[0];
  const availableShows = project.shows.length > 0 ? project.shows : (projectUniverse?.available_shows ?? []);

  const slots = project.beats.flatMap((b) => b.slots);
  const slotsWithQueries = slots.filter((s) => s.text_query || s.visual_query).length;
  const slotsFilled = slots.filter((s) => s.selected != null).length;
  const beatsWithBackground = project.beats.filter((b) => b.backgrounds.length > 0).length;

  const approvedQuotes = project.quotes.filter((q) => q.status === "approved").length;
  const tabs: { key: Tab; label: string }[] = [
    { key: "pipeline", label: "Pipeline" },
    { key: "quotes", label: project.quotes.length > 0 ? `Quotes ${approvedQuotes}/${project.quotes.length}` : "Quotes" },
    { key: "clips", label: slots.length > 0 ? `Clips ${slotsFilled}/${slots.length}` : "Clips" },
    { key: "look", label: "Look" },
    { key: "video", label: "Video" },
  ];
  const renderButton = (
    <button className="small primary" onClick={() => runMutation.mutate("render")} disabled={busy || slotsFilled === 0}>
      {project.rendered_at ? "Re-render" : "Render"}
    </button>
  );

  return (
    <div>
      <p className="hint" style={{ marginBottom: "0.25rem" }}>
        <Link to="/editor">← Videos</Link>
      </p>
      <h1 style={{ marginTop: 0 }}>{project.title}</h1>
      <p className="hint" style={{ marginTop: "-0.5rem" }}>
        {project.settings.layout === "framed" ? "Framed layout" : "Simple layout"} ·{" "}
        {projectUniverse ? `${projectUniverse.name} universe · ` : ""}
        {project.shows.length > 0 ? project.shows.join(", ") : "all its shows"} ·{" "}
        {project.script_text ? "script attached" : "no script"}
        {project.llm_cost_usd > 0 && ` · LLM usage $${project.llm_cost_usd.toFixed(2)}`}
      </p>

      {/* beat audition on the Clips tab plays through this, whichever tab is open */}
      {project.audio_file && <audio ref={audioRef} src={api.editorAudioUrl(project.id)} preload="metadata" />}

      <div className="tabs" role="tablist">
        {tabs.map((t) => (
          <button key={t.key} role="tab" aria-selected={tab === t.key} className={tab === t.key ? "active" : ""} onClick={() => setTab(t.key)}>
            {t.label}
          </button>
        ))}
      </div>

      {/* what is running or went wrong is shown on every tab */}
      {running && (
        <p style={{ margin: "0 0 0.75rem" }}>
          <span className="badge accent">{project.job?.stage} running</span>{" "}
          <span className="hint">{project.job?.detail}</span>
        </p>
      )}
      {project.job?.status === "error" && (
        <p className="error-text" style={{ margin: "0 0 0.75rem" }}>
          {project.job.stage} failed: {project.job.error}
        </p>
      )}
      {mutationError && <p className="error-text" style={{ margin: "0 0 0.75rem" }}>{mutationError.message}</p>}

      {tab === "pipeline" && (
        <>
          <div className="card">
            <h3>Narration</h3>
            {project.audio_file ? (
              <audio controls src={api.editorAudioUrl(project.id)} style={{ width: "100%" }} />
            ) : (
              <p className="hint">No audio yet.</p>
            )}
            <div style={{ marginTop: "0.5rem" }}>
              <label style={{ fontSize: "0.8rem" }}>{project.audio_file ? "Replace audio" : "Add audio"}</label>
              <input
                type="file"
                accept="audio/*,.mp3,.wav,.m4a,.flac,.ogg"
                disabled={busy}
                onChange={(e) => {
                  const file = e.target.files?.[0];
                  if (file) uploadMutation.mutate(file);
                }}
              />
              {project.audio_file && (
                <p className="hint" style={{ marginTop: "0.3rem", marginBottom: 0 }}>
                  Replacing the audio discards the transcript, quotes and beats.
                </p>
              )}
            </div>
          </div>

          <div className="card" style={{ marginTop: "0.75rem" }}>
            <h3>Stages</h3>
            <StageRow
              label="Transcribe"
              description="Word-level timings of the narration. Free, local."
              state={project.word_count > 0 ? `${project.word_count} words` : "not run"}
              action={project.word_count > 0 ? "Re-run" : "Run"}
              onRun={() => runMutation.mutate("transcribe")}
              disabled={busy || !project.audio_file}
            />
            <StageRow
              label="Quotes"
              description="Proposes lines from the show to drop in. Two Claude calls; you choose which to use."
              state={project.quotes.length > 0 ? `${project.quotes.length} found` : "not run - optional"}
              action={project.quotes.length > 0 ? "Search again" : "Run"}
              onRun={() => runMutation.mutate("quotes")}
              disabled={busy || project.word_count === 0}
            />
            <StageRow
              label="Beats"
              description="Groups the narration into visual ideas. One Claude call."
              state={project.beats.length > 0 ? `${project.beats.length} beats` : "not run"}
              action={project.beats.length > 0 ? "Re-run" : "Run"}
              onRun={() => runMutation.mutate("beats")}
              disabled={busy || project.word_count === 0}
            />
            <StageRow
              label="Shot queries"
              description="Decides what footage each clip should show. One Claude call; keeps locked clips."
              state={slotsWithQueries > 0 ? `${slotsWithQueries} clips described` : "not run"}
              action={slotsWithQueries > 0 ? "Re-run" : "Run"}
              onRun={() => runMutation.mutate("queries")}
              disabled={busy || project.beats.length === 0}
            />
            <StageRow
              label="Find footage"
              description="Searches the index for every clip, avoiding repeats. Free, local; keeps locked clips."
              state={slotsFilled > 0 ? `${slotsFilled} / ${slots.length} clips filled` : "not run"}
              action={slotsFilled > 0 ? "Re-run" : "Run"}
              onRun={() => runMutation.mutate("select")}
              disabled={busy || slotsWithQueries === 0}
            />
            {project.settings.layout === "framed" && (
              <StageRow
                label="Background"
                description="Calm scenery behind the frame, themed per beat, cross-fading. One Claude call, then local search."
                state={beatsWithBackground > 0 ? `${beatsWithBackground} / ${project.beats.length} beats` : "not run - uses a blurred copy of each clip"}
                action={beatsWithBackground > 0 ? "Re-run" : "Run"}
                onRun={() => runMutation.mutate("background")}
                disabled={busy || project.beats.length === 0}
              />
            )}
            <StageRow
              label="Render"
              description="Builds the video. Free, local; only clips that changed are re-encoded."
              state={project.rendered_at ? `rendered ${new Date(project.rendered_at).toLocaleTimeString()}` : "not run"}
              action={project.rendered_at ? "Re-render" : "Run"}
              onRun={() => runMutation.mutate("render")}
              disabled={busy || slotsFilled === 0}
            />
          </div>
        </>
      )}

      {tab === "quotes" &&
        (project.quotes.length > 0 ? (
          <QuotesCard project={project} disabled={busy} onChange={setProject} />
        ) : (
          <div className="card">
            <p className="hint">
              No quotes yet. The search proposes lines from the show that would strengthen the narration; nothing is
              added to the video until you choose it.
            </p>
            <button className="small primary" onClick={() => runMutation.mutate("quotes")} disabled={busy || project.word_count === 0}>
              Search for quotes
            </button>
            {project.word_count === 0 && <span className="hint"> Transcribe the narration first.</span>}
          </div>
        ))}

      {tab === "clips" &&
        (project.beats.length > 0 ? (
          <div className="card">
            <div className="card-header">
              <h3 style={{ margin: 0 }}>Beats</h3>
              <span className="hint" style={{ margin: 0 }}>
                {project.beats.length} beats · avg {((project.audio_duration ?? 0) / project.beats.length).toFixed(1)}s
              </span>
            </div>
            <table>
              <tbody>
                {project.beats.map((beat) => (
                  <BeatRow
                    key={beat.id}
                    projectId={project.id}
                    beat={beat}
                    projectShows={availableShows}
                    playing={playingBeat?.id === beat.id}
                    onPlay={() => togglePlay(beat)}
                    disabled={busy}
                    onChange={setProject}
                  />
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="card">
            <p className="hint" style={{ margin: 0 }}>
              No beats yet - run Transcribe and Beats on the Pipeline tab.
            </p>
          </div>
        ))}

      {tab === "look" && (
        <>
          <div className="look-layout">
            <div>
              <RenderSettingsCard project={project} disabled={busy} onChange={setProject} />
              <div style={{ marginTop: "0.75rem" }}>{renderButton}</div>
            </div>
            <LookPreview project={project} />
          </div>
        </>
      )}

      {tab === "video" && (
        <div className="card">
          {project.output_file ? (
            <>
              <video
                key={project.rendered_at}
                src={api.editorOutputUrl(project.id, project.rendered_at)}
                controls
                style={{ width: "100%", maxWidth: "960px", display: "block" }}
              />
              <p className="hint">
                Rendered {project.rendered_at ? new Date(project.rendered_at).toLocaleString() : ""}. Changes to clips,
                quotes or the look appear after rendering again.
              </p>
            </>
          ) : (
            <p className="hint">Not rendered yet.</p>
          )}
          {renderButton}
        </div>
      )}
    </div>
  );
}
