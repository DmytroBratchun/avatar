import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import type { EditorBeat, EditorJob, EditorProject, EditorStage, LookFormat } from "../api/client";
import LookPreview from "../components/LookPreview";
import FootageNotesCard from "../components/FootageNotesCard";
import MusicCard from "../components/MusicCard";
import QuoteBankCard from "../components/QuoteBankCard";
import QuotesCard, { AddQuoteCard } from "../components/QuotesCard";
import RenderSettingsCard from "../components/RenderSettingsCard";
import SoundEffectsCard from "../components/SoundEffectsCard";
import { SlotEditor, SlotThumb } from "../components/SlotEditor";
import VoiceoverCard from "../components/VoiceoverCard";

function formatTime(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = seconds - m * 60;
  return `${m}:${s.toFixed(1).padStart(4, "0")}`;
}

function elapsed(since: string, now: number): string {
  const seconds = Math.max(0, Math.round((now - new Date(since).getTime()) / 1000));
  return seconds < 60 ? `${seconds}s` : `${Math.floor(seconds / 60)}m ${(seconds % 60).toString().padStart(2, "0")}s`;
}

/** When the running stage's current counted step ("rendering clips 95/147") should
 * finish, as a timestamp - or null while that can't be told. Measured from how fast
 * the count has moved since this page first saw the step, not from when the stage
 * started: a stage can spend time on other steps before the one it counts. */
function useEta(job: EditorJob | null): number | null {
  const running = job?.status === "running";
  // the step, whatever its numbers are: "render:rendering clips #/#"
  const step = running ? `${job.stage}:${job.detail.replace(/\d+(\.\d+)?/g, "#")}` : "";
  const progress = running ? job.progress : null;
  const first = useRef<{ step: string; at: number; progress: number } | null>(null);
  const [eta, setEta] = useState<number | null>(null);
  useEffect(() => {
    const at = Date.now();
    if (progress == null) {
      first.current = null;
      setEta(null);
    } else if (first.current?.step !== step || progress < first.current.progress) {
      first.current = { step, at, progress };
      setEta(null);
    } else if (progress > first.current.progress) {
      setEta(at + ((1 - progress) * (at - first.current.at)) / (progress - first.current.progress));
    }
  }, [step, progress]);
  return eta;
}

/** "≈ 1m 20s left, ready at 18:42", counting down. */
function Eta({ at }: { at: number }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  const seconds = Math.max(0, Math.round((at - now) / 1000));
  if (seconds === 0) return <>almost ready</>;
  const left = seconds < 60 ? `${seconds}s` : `${Math.floor(seconds / 60)}m ${(seconds % 60).toString().padStart(2, "0")}s`;
  const ready = new Date(at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  return (
    <>
      ≈ {left} left, ready at {ready}
    </>
  );
}

// Asked before Shot queries / Find footage when some clips are locked: keep them, or
// let the stage pick those again too.
function UnlockDialog({ stage, locked, onAnswer }: { stage: EditorStage; locked: number; onAnswer: (unlock: boolean | null) => void }) {
  return (
    <div
      style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.4)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 100 }}
      onClick={() => onAnswer(null)}
    >
      <div className="card" style={{ width: "460px", maxWidth: "90vw" }} onClick={(e) => e.stopPropagation()}>
        <h3 style={{ marginTop: 0 }}>{stage === "queries" ? "Shot queries" : "Find footage"}</h3>
        <p style={{ marginTop: 0 }}>
          {locked} clip{locked === 1 ? " is" : "s are"} locked - picked or re-searched by you. Keep {locked === 1 ? "it" : "them"}, or
          let this run pick {locked === 1 ? "it" : "them"} again too?
        </p>
        <div style={{ display: "flex", gap: "0.5rem", justifyContent: "flex-end", flexWrap: "wrap" }}>
          <button className="ghost" onClick={() => onAnswer(null)}>
            Cancel
          </button>
          <button onClick={() => onAnswer(true)}>Re-pick all</button>
          <button className="primary" autoFocus onClick={() => onAnswer(false)}>
            Keep locked
          </button>
        </div>
      </div>
    </div>
  );
}

function StageRow({
  label,
  description,
  state,
  done,
  job,
  eta,
  queuePosition,
  canRun,
  somethingRunning,
  onRun,
  onUnqueue,
}: {
  label: string;
  description: string;
  state: string;
  done: boolean;
  // this stage's job, when it is the one running or the one that last failed
  job: EditorJob | null;
  // when the running step should finish (see useEta), if known
  eta: number | null;
  // 1-based place in the queue, or 0 when not queued
  queuePosition: number;
  canRun: boolean;
  somethingRunning: boolean;
  onRun: () => void;
  onUnqueue: () => void;
}) {
  const running = job?.status === "running";
  // re-render once a second while running, for the elapsed time
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!running) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [running]);

  return (
    <div style={{ padding: "0.4rem 0", borderTop: "1px solid var(--border)" }}>
      {/* name, status, then the button straight after the status: the status column has
          a fixed width, so the buttons of all stages line up. While the stage runs, how far
          along it is goes after the loader, next to it */}
      <div style={{ display: "grid", gridTemplateColumns: "7.5rem 19rem 5.5rem 1.2rem auto", alignItems: "center", gap: "0.6rem", justifyContent: "start" }}>
        <strong style={{ fontSize: "0.9rem", cursor: "help" }} title={description}>
          {label}
        </strong>
        <span className="hint" style={{ margin: 0 }}>
          {running ? (
            <span className="badge accent">running</span>
          ) : queuePosition > 0 ? (
            <span className="badge">queued #{queuePosition}</span>
          ) : (
            state
          )}
        </span>
        {queuePosition > 0 ? (
          <button className="ghost small" onClick={onUnqueue} title="Remove from the queue">
            × cancel
          </button>
        ) : (
          <button className="small" onClick={onRun} disabled={running || !canRun}>
            {somethingRunning ? "Queue" : done ? "Re-run" : "Run"}
          </button>
        )}
        {/* a ring that fills when the stage can say how far along it is, a spinner when it can't */}
        {running &&
          (job?.progress != null ? (
            <svg width="18" height="18" viewBox="0 0 18 18" role="img" aria-label={`${Math.round(job.progress * 100)}%`}>
              <title>{Math.round(job.progress * 100)}%</title>
              <circle cx="9" cy="9" r="7" fill="none" stroke="var(--bg-hover)" strokeWidth="2.5" />
              <circle
                cx="9"
                cy="9"
                r="7"
                fill="none"
                stroke="var(--accent)"
                strokeWidth="2.5"
                strokeLinecap="round"
                // circumference of r=7 is ~44; the dash covers the done share of it
                strokeDasharray={`${job.progress * 43.98} 43.98`}
                transform="rotate(-90 9 9)"
                style={{ transition: "stroke-dasharray 0.4s" }}
              />
            </svg>
          ) : (
            <span className="spinner" title="Working…" />
          ))}
        {running && (
          <span className="hint" style={{ margin: 0 }}>
            {job?.detail} · {elapsed(job?.started_at ?? "", now)}
            {eta != null && (
              <>
                {" · "}
                <Eta at={eta} />
              </>
            )}
          </span>
        )}
      </div>
      {job?.status === "error" && (
        <p className="error-text" style={{ margin: "0.3rem 0 0" }}>
          Failed: {job.error}
        </p>
      )}
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
              <SlotThumb key={i} projectId={projectId} slot={s} active={openSlot === i} onClick={() => setOpenSlot(openSlot === i ? null : i)} />
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

const TABS = ["pipeline", "voice", "quotes", "clips", "music", "look", "video"] as const;
type Tab = (typeof TABS)[number];

export default function EditorProjectPage() {
  const { id = "" } = useParams();
  const queryClient = useQueryClient();
  const audioRef = useRef<HTMLAudioElement>(null);
  const [playingBeat, setPlayingBeat] = useState<EditorBeat | null>(null);
  // which kind of footage the Look preview shows; the settings sections switch it
  const [lookFormat, setLookFormat] = useState<LookFormat>("4:3");
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
    mutationFn: ({ stage, unlock = false }: { stage: EditorStage; unlock?: boolean }) => api.runEditorStage(id, stage, unlock),
    onSuccess: setProject,
  });
  // a stage that keeps locked clips, waiting for the answer to "keep them?"
  const [askUnlock, setAskUnlock] = useState<EditorStage | null>(null);
  const settingsMutation = useMutation({
    mutationFn: (payload: Parameters<typeof api.updateEditorProject>[1]) => api.updateEditorProject(id, payload),
    onSuccess: setProject,
  });
  const unqueueMutation = useMutation({
    mutationFn: (stage: EditorStage) => api.unqueueEditorStage(id, stage),
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

  const eta = useEta(project?.job ?? null);

  if (error) return <p className="error-text">{(error as Error).message}</p>;
  if (!project) return <p>Loading…</p>;

  const running = project.job?.status === "running";
  const busy = running || runMutation.isPending || uploadMutation.isPending;
  // attributions of the photos the video shows, once each, in order of appearance
  const photoCredits = Array.from(
    new Set(
      project.beats.flatMap((beat) =>
        beat.slots.flatMap((slot) => {
          const c = slot.selected != null ? slot.candidates[slot.selected] : null;
          return c?.kind === "photo" && c.credit ? [c.credit] : [];
        }),
      ),
    ),
  );
  const lockedCount = project.beats.reduce((n, beat) => n + beat.slots.filter((slot) => slot.locked).length, 0);
  const mutationError = (runMutation.error ?? uploadMutation.error) as Error | null;

  const projectUniverse = universes?.find((u) => u.id === project.universe) ?? universes?.find((u) => project.shows.some((s) => u.shows.includes(s))) ?? universes?.[0];
  const availableShows = project.shows.length > 0 ? project.shows : (projectUniverse?.available_shows ?? []);

  const slots = project.beats.flatMap((b) => b.slots);
  const slotsWithQueries = slots.filter((s) => s.text_query || s.visual_query).length;
  const slotsFilled = slots.filter((s) => s.selected != null).length;
  const beatsWithBackground = project.beats.filter((b) => b.backgrounds.length > 0).length;

  const approvedQuotes = project.quotes.filter((q) => q.status === "approved").length;
  // music is planned over whole beats; beats built again leave it pointing at times that are gone
  const musicStale =
    project.music.length > 0 &&
    project.music.some((block) => {
      const first = project.beats.find((b) => b.id === block.beat_first);
      const last = project.beats.find((b) => b.id === block.beat_last);
      return !first || !last || Math.abs(first.start - block.start) > 0.01 || Math.abs(last.end - block.end) > 0.01;
    });
  // a block is filled when its tracks, overlapping by 3s, last as long as it does
  const musicFilled = project.music.filter((block) => {
    const tracks = block.tracks.filter((t) => t.enabled && t.end - t.start > 6);
    return tracks.reduce((sum, t) => sum + (t.end - t.start), 0) - 3 * Math.max(0, tracks.length - 1) >= block.end - block.start + 3;
  }).length;
  // a beat whose number of clips no longer matches the limit was split under an older setting
  // (a beat told from a comic has as many pages as its story needs, whatever the limit)
  const clipsStale = project.beats.some(
    (b) =>
      b.slots.length > 0 &&
      !b.slots.every((s) => s.selected != null && s.candidates[s.selected]?.kind === "comic") &&
      b.slots.length !== Math.max(1, Math.ceil((b.end - b.start) / project.settings.max_clip_seconds - 1e-6))
  );
  const stages: { key: EditorStage; label: string; description: string; state: string; done: boolean; ready: boolean }[] = [
    {
      key: "transcribe",
      label: "Transcribe",
      description: "Word-level timings of the narration. Free, local.",
      state: project.word_count > 0 ? `${project.word_count} words` : "not run",
      done: project.word_count > 0,
      ready: !!project.audio_file,
    },
    {
      key: "quotes",
      label: "Quotes",
      description: "Proposes lines from the show to drop in, by reading the episodes. Several Claude calls; you choose which to use. Optional.",
      state: project.quotes.length > 0 ? `${project.quotes.length} found` : "not run - optional",
      done: project.quotes.length > 0,
      ready: project.word_count > 0,
    },
    {
      key: "beats",
      label: "Beats",
      description: "Groups the narration into visual ideas. One Claude call.",
      state: project.beats.length > 0 ? `${project.beats.length} beats` : "not run",
      done: project.beats.length > 0,
      ready: project.word_count > 0,
    },
    {
      key: "queries",
      label: "Shot queries",
      description: "Decides what footage each clip should show. One Claude call per 40 beats; keeps locked clips.",
      state: slotsWithQueries > 0 ? `${slotsWithQueries} clips described` : "not run",
      done: slotsWithQueries > 0,
      ready: project.beats.length > 0,
    },
    {
      key: "select",
      label: "Find footage",
      description: "Searches the index for every clip, avoiding repeats; keeps locked clips. Free and local, except shots that want a photo: those are searched on Wikimedia Commons and checked by Claude (one call per photo, cached).",
      state: slotsFilled > 0 ? `${slotsFilled} / ${slots.length} clips filled` : "not run",
      done: slotsFilled > 0,
      ready: slotsWithQueries > 0,
    },
    ...(project.settings.layout === "framed"
      ? [
          {
            key: "background" as const,
            label: "Background",
            description: "Calm scenery behind the frame, themed per beat, cross-fading. One Claude call, then local search.",
            state: beatsWithBackground > 0 ? `${beatsWithBackground} / ${project.beats.length} beats` : "not run - uses a blurred copy of each clip",
            done: beatsWithBackground > 0,
            ready: project.beats.length > 0,
          },
        ]
      : []),
    {
      key: "music",
      label: "Music",
      description: "Music under the narration: one Claude call divides it into moods, then tracks are composed for each through Lumean. Optional.",
      state: musicStale
        ? "planned for other beats - run again"
        : project.music.length > 0
          ? `${musicFilled} / ${project.music.length} blocks`
          : "not run - optional",
      done: project.music.length > 0 && !musicStale && musicFilled === project.music.length,
      ready: project.beats.length > 0,
    },
    {
      key: "render",
      label: "Render",
      description: "Builds the video. Free, local; only clips that changed are re-encoded.",
      state: project.rendered_at ? `rendered ${new Date(project.rendered_at).toLocaleTimeString()}` : "not run",
      done: !!project.rendered_at,
      ready: slotsFilled > 0,
    },
  ];
  const tabs: { key: Tab; label: string }[] = [
    { key: "pipeline", label: "Pipeline" },
    { key: "voice", label: "Voice" },
    { key: "quotes", label: project.quotes.length > 0 ? `Quotes ${approvedQuotes}/${project.quotes.length}` : "Quotes" },
    { key: "clips", label: slots.length > 0 ? `Clips ${slotsFilled}/${slots.length}` : "Clips" },
    { key: "music", label: "Music" },
    { key: "look", label: "Look" },
    { key: "video", label: "Video" },
  ];
  const renderButton = (
    <button
      className="small primary"
      onClick={() => runMutation.mutate({ stage: "render" })}
      disabled={project.job?.stage === "render" && running ? true : project.queue.includes("render") || (slotsFilled === 0 && !running)}
    >
      {project.queue.includes("render") ? "Render queued" : running ? "Queue render" : project.rendered_at ? "Re-render" : "Render"}
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
      {(tab !== "pipeline" || project.job?.stage === "voiceover") && running && (
        <p style={{ margin: "0 0 0.75rem", display: "flex", alignItems: "center", gap: "0.45rem", flexWrap: "wrap" }}>
          <span className="spinner" />
          <span className="badge accent">{project.job?.stage} running</span>
          <span className="hint" style={{ margin: 0 }}>
            {project.job?.detail}
            {eta != null && (
              <>
                {" · "}
                <Eta at={eta} />
              </>
            )}
            {project.queue.length > 0 && ` · then ${project.queue.join(", ")}`}
          </span>
        </p>
      )}
      {(tab !== "pipeline" || project.job?.stage === "voiceover") && project.job?.status === "error" && (
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

          <FootageNotesCard key={project.id} project={project} onChange={setProject} />

          <div className="card" style={{ marginTop: "0.75rem" }}>
            <h3>Stages</h3>
            <p className="hint" style={{ marginTop: 0 }}>
              Hover a name to see what the stage does. Running a stage while another is working puts it in the queue.
            </p>
            <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", marginBottom: "0.5rem" }}>
              <label style={{ margin: 0 }} title="A beat longer than this is split into equal clips. Longer clips: fewer cuts and less flicker, but more footage around the moment that was searched for.">
                Longest clip
              </label>
              <select
                style={{ width: "auto" }}
                value={project.settings.max_clip_seconds}
                disabled={busy}
                onChange={(e) => settingsMutation.mutate({ max_clip_seconds: Number(e.target.value) })}
              >
                {[4, 5, 6, 7, 8, 10].map((n) => (
                  <option key={n} value={n}>
                    {n} seconds
                  </option>
                ))}
              </select>
              {clipsStale && (
                <span className="hint" style={{ margin: 0 }}>
                  Clips were made with a different limit - run Shot queries, then Find footage, to apply it.
                </span>
              )}
            </div>
            {stages.map((stage) => (
              <StageRow
                key={stage.key}
                label={stage.label}
                description={stage.description}
                state={stage.state}
                done={stage.done}
                job={project.job?.stage === stage.key ? project.job : null}
                eta={project.job?.stage === stage.key ? eta : null}
                queuePosition={project.queue.indexOf(stage.key) + 1}
                // when something is running or queued, what this stage needs may be on its way
                canRun={!!project.audio_file && (stage.ready || running || project.queue.length > 0)}
                somethingRunning={running}
                onRun={() =>
                  (stage.key === "queries" || stage.key === "select") && lockedCount > 0
                    ? setAskUnlock(stage.key)
                    : runMutation.mutate({ stage: stage.key })
                }
                onUnqueue={() => unqueueMutation.mutate(stage.key)}
              />
            ))}
          </div>
        </>
      )}

      {tab === "music" && (
        <>
          <MusicCard project={project} disabled={busy} stale={musicStale} onChange={setProject} onRun={() => runMutation.mutate({ stage: "music" })} />
          <SoundEffectsCard project={project} disabled={busy} onChange={setProject} />
        </>
      )}

      {askUnlock && (
        <UnlockDialog
          stage={askUnlock}
          locked={lockedCount}
          onAnswer={(unlock) => {
            if (unlock !== null) runMutation.mutate({ stage: askUnlock, unlock });
            setAskUnlock(null);
          }}
        />
      )}

      {tab === "voice" && <VoiceoverCard project={project} disabled={busy} onChange={setProject} />}

      {tab === "quotes" && (
        <>
          <label className="hint" style={{ display: "flex", alignItems: "center", gap: "0.4rem", margin: "0 0 0.75rem", cursor: "pointer" }}>
            <input
              type="checkbox"
              style={{ width: "auto" }}
              checked={project.settings.quote_cuts}
              disabled={busy}
              onChange={(e) => settingsMutation.mutate({ quote_cuts: e.target.checked })}
            />
            <span title="A quote may be put together from two or three pieces of one moment, played back to back with what was said between them cut out. Applies to the next quote search.">
              Cut quotes together from pieces (experimental)
            </span>
          </label>
          {project.quotes.length > 0 ? (
          <QuotesCard project={project} disabled={busy} onChange={setProject} />
        ) : (
          <div className="card">
            <p className="hint">
              No quotes yet. The search proposes lines from the show that would strengthen the narration; nothing is
              added to the video until you choose it.
            </p>
            <button className="small primary" onClick={() => runMutation.mutate({ stage: "quotes" })} disabled={busy || project.word_count === 0}>
              Search for quotes
            </button>
            {project.word_count === 0 && <span className="hint"> Transcribe the narration first.</span>}
          </div>)}
          <QuoteBankCard project={project} shows={availableShows} disabled={busy} onChange={setProject} />
          <AddQuoteCard project={project} shows={availableShows} disabled={busy} onChange={setProject} />
        </>
      )}

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
              <RenderSettingsCard project={project} disabled={busy} format={lookFormat} onFormat={setLookFormat} onChange={setProject} />
              <div style={{ marginTop: "0.75rem" }}>{renderButton}</div>
            </div>
            <LookPreview project={project} format={lookFormat} onFormat={setLookFormat} />
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
          {photoCredits.length > 0 && (
            <div style={{ marginTop: "1rem" }}>
              <h3>Photo credits</h3>
              <p className="hint" style={{ marginTop: 0 }}>
                The photos in this video are from Wikimedia Commons under licences that ask to name their authors - paste this
                into the video's description.
              </p>
              <textarea readOnly value={photoCredits.join("\n")} rows={Math.min(10, photoCredits.length + 1)} style={{ width: "100%" }} />
            </div>
          )}
        </div>
      )}
    </div>
  );
}
