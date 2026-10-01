import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { SceneOut } from "../api/client";

function formatTime(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}

export default function SceneCard({ scene }: { scene: SceneOut }) {
  const allTags = [...scene.character_tags, ...scene.location_tags, ...scene.mood_action_tags];
  const shownTags = allTags.slice(0, 8);
  const remaining = allTags.length - shownTags.length;

  return (
    <Link to={`/scenes/${scene.id}`} className="scene-card">
      {scene.keyframe_paths.length > 0 && <img src={api.keyframeUrl(scene.id, 0)} alt="" />}
      <div style={{ minWidth: 0 }}>
        <div className="scene-meta">
          Book {scene.book} · Ep {scene.episode_number} · {formatTime(scene.start_time)}–{formatTime(scene.end_time)}
        </div>
        <p>{scene.context_description ?? scene.visual_description ?? "(not captioned yet)"}</p>
        <div>
          {shownTags.map((tag) => (
            <span className="tag" key={tag}>
              {tag}
            </span>
          ))}
          {remaining > 0 && <span className="tag">+{remaining}</span>}
        </div>
      </div>
    </Link>
  );
}
