import { Link } from "react-router-dom";
import type { SceneDetailOut } from "../api/client";

function clock(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = seconds - m * 60;
  return `${m}:${s.toFixed(2).padStart(5, "0")}`;
}

/** Collapsible "how this scene was built" panel: shots, keyframes, why it ends where it does. */
export default function SceneTechnical({ scene, onSeek }: { scene: SceneDetailOut; onSeek: (time: number) => void }) {
  const at = (time: number) => (
    <button className="ghost small" style={{ padding: "0 0.25rem", fontFamily: "var(--font-mono)" }} title="Play from here" onClick={() => onSeek(time)}>
      {clock(time)}
    </button>
  );
  const t = scene.technical;
  const length = scene.end_time - scene.start_time;
  const pct = (time: number) => `${((time - scene.start_time) / length) * 100}%`;

  return (
    <details className="card" style={{ marginTop: "0.75rem" }}>
      <summary style={{ cursor: "pointer", fontWeight: 600 }}>
        Technical details
        <span className="hint" style={{ margin: "0 0 0 0.5rem", fontWeight: 400 }}>
          {t.shots.length} shot{t.shots.length === 1 ? "" : "s"} · {length.toFixed(1)}s · {scene.region ?? "no region"}
        </span>
      </summary>

      <h4 style={{ margin: "0.75rem 0 0.3rem" }}>Shots</h4>
      <p className="hint" style={{ marginTop: 0 }}>
        A shot runs between two camera cuts; a scene is a run of consecutive shots. Markers show where the keyframes
        were taken - the first (●) is what the visual index searches.
      </p>
      <div style={{ position: "relative", height: "28px", display: "flex", borderRadius: "var(--radius-sm)", overflow: "hidden" }}>
        {t.shots.map((shot, i) => (
          <div
            key={shot.shot_index}
            onClick={() => onSeek(shot.start_time)}
            title={`Shot ${shot.shot_index}: ${clock(shot.start_time)}–${clock(shot.end_time)} (${(shot.end_time - shot.start_time).toFixed(2)}s)`}
            style={{
              width: `${((shot.end_time - shot.start_time) / length) * 100}%`,
              background: i % 2 ? "var(--accent-soft, rgba(99,102,241,0.25))" : "var(--bg-hover)",
              borderRight: "1px solid var(--border)",
              cursor: "pointer",
            }}
          />
        ))}
        {t.keyframe_times.map((time, i) => (
          <span
            key={i}
            title={`Keyframe ${i + 1} at ${clock(time)}${i === 0 ? " - used by the visual index" : ""}`}
            onClick={() => onSeek(time)}
            style={{ position: "absolute", left: pct(time), top: "4px", transform: "translateX(-50%)", fontSize: "0.8rem", cursor: "pointer" }}
          >
            {i === 0 ? "●" : "○"}
          </span>
        ))}
      </div>
      <table style={{ marginTop: "0.5rem", fontSize: "0.8rem" }}>
        <thead>
          <tr>
            <th>Shot</th>
            <th>Start</th>
            <th>End</th>
            <th>Length</th>
          </tr>
        </thead>
        <tbody>
          {t.shots.map((shot) => (
            <tr key={shot.shot_index}>
              <td>{shot.shot_index}</td>
              <td>{at(shot.start_time)}</td>
              <td>{at(shot.end_time)}</td>
              <td>
                {(shot.end_time - shot.start_time).toFixed(2)}s
                {shot.end_time - shot.start_time < 0.8 && <span className="hint"> · very short</span>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <h4 style={{ margin: "0.75rem 0 0.3rem" }}>Boundaries</h4>
      <p style={{ margin: 0, fontSize: "0.85rem" }}>
        Ends here because: {t.end_reason}
        {t.dialogue_gap_after != null && <span className="hint"> (gap to the next line: {t.dialogue_gap_after}s)</span>}
      </p>
      <p style={{ margin: "0.3rem 0 0", fontSize: "0.85rem", display: "flex", gap: "1rem" }}>
        {t.previous_scene_id ? <Link to={`/scenes/${t.previous_scene_id}`}>← previous scene</Link> : <span className="hint">first scene</span>}
        {t.next_scene_id ? <Link to={`/scenes/${t.next_scene_id}`}>next scene →</Link> : <span className="hint">last scene</span>}
      </p>

      <h4 style={{ margin: "0.75rem 0 0.3rem" }}>Record</h4>
      <p style={{ margin: 0, fontSize: "0.8rem", fontFamily: "var(--font-mono)", color: "var(--text-secondary)" }}>
        region: {scene.region ?? "—"}
        <br />
        keyframes: {t.keyframe_times.length ? t.keyframe_times.map((time, i) => <span key={i}>{at(time)}</span>) : "none"}
        <br />
        transcript lines linked: {scene.transcript_lines.length}
        <br />
        caption attempts that failed: {t.caption_attempts}
        {t.caption_error && (
          <>
            <br />
            last caption error: {t.caption_error}
          </>
        )}
        <br />
        file: {scene.source_file_path}
        <br />
        created: {t.created_at ? new Date(t.created_at).toLocaleString() : "—"} · updated:{" "}
        {t.updated_at ? new Date(t.updated_at).toLocaleString() : "—"}
      </p>
    </details>
  );
}
