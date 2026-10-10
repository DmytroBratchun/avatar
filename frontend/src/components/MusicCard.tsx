import { keepPreviousData, useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api/client";
import type { EditorMusicBlock, EditorProject, MusicLibraryUsage } from "../api/client";

// seconds two neighbouring tracks play together (the backend's OVERLAP)
const OVERLAP = 3;

function clock(seconds: number): string {
  return `${Math.floor(seconds / 60)}:${Math.floor(seconds % 60).toString().padStart(2, "0")}`;
}

/** Seconds the block's ticked tracks fill when laid end to end with overlaps. */
function covered(block: EditorMusicBlock): number {
  const tracks = block.tracks.filter((t) => t.enabled && t.end - t.start > OVERLAP * 2);
  return tracks.reduce((sum, t) => sum + (t.end - t.start), 0) - OVERLAP * Math.max(0, tracks.length - 1);
}

function BlockRow({
  project,
  block,
  number,
  disabled,
  library,
  onChange,
}: {
  project: EditorProject;
  block: EditorMusicBlock;
  number: number;
  disabled: boolean;
  library: MusicLibraryUsage["tracks"];
  onChange: (project: EditorProject) => void;
}) {
  const [prompt, setPrompt] = useState(block.prompt);
  const save = useMutation({ mutationFn: () => api.updateMusicBlock(project.id, block.id, { prompt }), onSuccess: onChange });
  const compose = useMutation({ mutationFn: () => api.composeMusicBlock(project.id, block.id), onSuccess: onChange });
  const toggle = useMutation({
    mutationFn: (v: { trackId: string; enabled: boolean }) => api.setMusicTrackEnabled(project.id, block.id, v.trackId, v.enabled),
    onSuccess: onChange,
  });
  const busy = disabled || save.isPending || compose.isPending || toggle.isPending;
  const changed = prompt.trim() !== block.prompt.trim();
  const length = block.end - block.start;
  const filled = covered(block) >= length + OVERLAP;
  const error = (save.error ?? compose.error ?? toggle.error) as Error | null;

  return (
    <div style={{ padding: "0.7rem 0", borderTop: "1px solid var(--border)" }}>
      <div style={{ display: "flex", gap: "0.5rem", alignItems: "baseline", flexWrap: "wrap" }}>
        <strong>
          {number}. {block.mood || "music"}
        </strong>
        <span className="hint" style={{ margin: 0 }}>
          {clock(block.start)}-{clock(block.end)} · {Math.round(length)}s · {block.tracks.length} tracks
        </span>
        <span className={`badge ${filled ? "success" : "accent"}`}>
          {filled ? "filled" : block.tracks.length === 0 ? "no music yet" : `${Math.max(0, Math.round(covered(block)))}s of ${Math.round(length)}s`}
        </span>
      </div>
      <textarea
        style={{ marginTop: "0.4rem", minHeight: "3.4rem", fontSize: "0.9rem" }}
        value={prompt}
        disabled={busy}
        onChange={(e) => setPrompt(e.target.value)}
      />
      <div style={{ display: "flex", gap: "0.5rem", alignItems: "center", marginTop: "0.3rem", flexWrap: "wrap" }}>
        {changed && (
          <button className="small primary" disabled={busy || !prompt.trim()} onClick={() => save.mutate()}>
            Save prompt
          </button>
        )}
        <button
          className="small"
          disabled={busy || changed}
          onClick={() => compose.mutate()}
          title="Throws this block's tracks away and composes new ones from the prompt."
        >
          {block.tracks.length > 0 ? "Compose this block again" : "Compose this block"}
        </button>
        {changed && <span className="hint" style={{ margin: 0 }}>Save the prompt, then compose again to hear it.</span>}
      </div>
      {block.tracks.map((track, i) => (
        <div key={track.id} style={{ display: "flex", gap: "0.6rem", alignItems: "center", marginTop: "0.35rem", flexWrap: "wrap" }}>
          <label style={{ display: "flex", alignItems: "center", gap: "0.35rem", margin: 0, cursor: "pointer" }} title="Untick to leave this track out of the video">
            <input
              type="checkbox"
              style={{ width: "auto" }}
              checked={track.enabled}
              disabled={busy}
              onChange={(e) => toggle.mutate({ trackId: track.id, enabled: e.target.checked })}
            />
            Track {i + 1}
          </label>
          <audio src={api.musicFileUrl(project.id, track.file)} controls preload="none" style={{ height: "28px", flex: 1, minWidth: "220px", maxWidth: "380px" }} />
          <span className="hint" style={{ margin: 0 }} title="The part that is played: generated tracks fade out towards the end, so the next one comes in before that.">
            plays {track.start.toFixed(1)}-{track.end.toFixed(1)}s of {track.duration.toFixed(0)}s
          </span>
          {library[track.id] && (library[track.id].from_library || library[track.id].used_in.length > 0) && (
            <span
              className="badge"
              title={
                (library[track.id].from_library ? "Taken from the music library." : "Composed for this video.") +
                (library[track.id].used_in.length > 0 ? ` Also plays in: ${library[track.id].used_in.join(", ")}.` : " In no other video.")
              }
            >
              {library[track.id].from_library ? "library" : "reused"}
            </span>
          )}
        </div>
      ))}
      {error && <p className="error-text">{error.message}</p>}
    </div>
  );
}

export default function MusicCard({
  project,
  disabled,
  stale,
  onChange,
  onRun,
}: {
  project: EditorProject;
  disabled: boolean;
  // the blocks were planned for other beats than the project has now
  stale: boolean;
  onChange: (project: EditorProject) => void;
  // runs the music stage: plans if needed, then composes what is missing
  onRun: () => void;
}) {
  const [level, setLevel] = useState(project.settings.music_level);
  const settings = useMutation({
    mutationFn: (payload: { music_enabled?: boolean; music_level?: number; music_library_only?: boolean }) =>
      api.updateEditorProject(project.id, payload),
    onSuccess: onChange,
  });
  const replan = useMutation({ mutationFn: () => api.replanMusic(project.id), onSuccess: onChange });
  // which tracks are from the library and where else they play - asked again when the music changes
  const { data: library } = useQuery({
    queryKey: ["editorMusicLibrary", project.id, project.music.map((b) => b.tracks.map((t) => t.id).join()).join("|")],
    queryFn: () => api.getMusicLibraryUsage(project.id),
    placeholderData: keepPreviousData,
  });
  const busy = disabled || replan.isPending;
  const unfilled = project.music.filter((b) => covered(b) < b.end - b.start + OVERLAP).length;
  const error = (settings.error ?? replan.error) as Error | null;

  return (
    <div className="card">
      <div className="card-header">
        <h3 style={{ margin: 0 }}>Music</h3>
        <span className="hint" style={{ margin: 0 }}>
          {project.music.length > 0
            ? `${project.music.length} blocks · ${project.music.reduce((n, b) => n + b.tracks.length, 0)} tracks`
            : "not composed yet"}
        </span>
      </div>
      <p className="hint">
        The narration is divided into blocks of one mood and each gets its own music, composed through Lumean: several
        different tracks per block, one fading into the next, none played twice. All of it plays at one level under the
        voice, and stops for quotes, which keep the show's own sound. Render again to hear a change.
      </p>

      <div style={{ display: "flex", gap: "1rem", alignItems: "center", flexWrap: "wrap", marginBottom: "0.5rem" }}>
        <label style={{ display: "flex", alignItems: "center", gap: "0.4rem", margin: 0, cursor: "pointer" }}>
          <input
            type="checkbox"
            style={{ width: "auto" }}
            checked={project.settings.music_enabled}
            disabled={busy}
            onChange={(e) => settings.mutate({ music_enabled: e.target.checked })}
          />
          Music in the video
        </label>
        {library?.enabled && (
          <label
            style={{ display: "flex", alignItems: "center", gap: "0.4rem", margin: 0, cursor: "pointer" }}
            title="Composes nothing new: every block is filled with the closest tracks already in the music library. Without it a block takes a library track only when one was made from almost the same prompt, and the rest is composed."
          >
            <input
              type="checkbox"
              style={{ width: "auto" }}
              checked={project.settings.music_library_only}
              disabled={busy}
              onChange={(e) => settings.mutate({ music_library_only: e.target.checked })}
            />
            Only from the library
          </label>
        )}
        <label style={{ display: "flex", alignItems: "center", gap: "0.5rem", margin: 0 }} title="How much quieter than the narrator's voice the music plays, in loudness units. Higher is quieter.">
          Quieter than the voice by {level.toFixed(0)}
          <input
            type="range" min={8} max={32} step={1} style={{ width: "160px" }} value={level} disabled={busy}
            onChange={(e) => setLevel(Number(e.target.value))}
            onMouseUp={() => settings.mutate({ music_level: level })}
            onKeyUp={() => settings.mutate({ music_level: level })}
          />
        </label>
      </div>

      <div style={{ display: "flex", gap: "0.5rem", alignItems: "center", flexWrap: "wrap" }}>
        {(project.music.length === 0 || stale || unfilled > 0) && (
          <button className="small primary" disabled={busy || project.beats.length === 0} onClick={onRun}>
            {project.music.length === 0 || stale ? "Compose music" : `Compose the ${unfilled} unfilled`}
          </button>
        )}
        {project.music.length > 0 && (
          <button className="ghost small" disabled={busy} onClick={() => replan.mutate()} title="Divides the narration into blocks again and composes all the music anew.">
            Plan and compose everything again
          </button>
        )}
        {project.beats.length === 0 && <span className="hint" style={{ margin: 0 }}>Build beats first.</span>}
      </div>
      {stale && project.music.length > 0 && (
        <p className="hint">The beats have changed since this music was planned - it is not used until it is composed again.</p>
      )}

      {project.music.map((block, i) => (
        <BlockRow key={`${block.id}-${block.prompt}`} project={project} block={block} number={i + 1} disabled={busy} library={library?.tracks ?? {}} onChange={onChange} />
      ))}
      {error && <p className="error-text">{error.message}</p>}
    </div>
  );
}
