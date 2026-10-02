import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api/client";
import SceneTechnical from "../components/SceneTechnical";
import TagMultiSelect from "../components/TagMultiSelect";

function formatTime(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}

export default function SceneDetailPage() {
  const videoRef = useRef<HTMLVideoElement>(null);
  // jump the player to a moment in the episode and play from there
  const seek = (time: number) => {
    const video = videoRef.current;
    if (!video) return;
    video.currentTime = time;
    void video.play();
    video.scrollIntoView({ behavior: "smooth", block: "nearest" });
  };
  const { id } = useParams();
  const sceneId = Number(id);
  const queryClient = useQueryClient();

  const { data: scene } = useQuery({
    queryKey: ["scene", sceneId],
    queryFn: () => api.getScene(sceneId),
  });
  const { data: facets } = useQuery({ queryKey: ["facets"], queryFn: api.getFacets });

  const [contextDescription, setContextDescription] = useState("");
  const [characterTags, setCharacterTags] = useState<string[]>([]);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (scene) {
      setContextDescription(scene.context_description ?? "");
      setCharacterTags(scene.character_tags);
    }
  }, [scene]);

  const saveMutation = useMutation({
    mutationFn: () =>
      api.updateScene(sceneId, {
        context_description: contextDescription,
        character_tags: characterTags,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["scene", sceneId] });
      queryClient.invalidateQueries({ queryKey: ["facets"] });
    },
  });

  function copyTimestamp() {
    if (!scene) return;
    navigator.clipboard.writeText(`${scene.source_file_path} @ ${scene.start_time.toFixed(2)}s`);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }

  if (!scene) return <p>Loading…</p>;

  return (
    <div>
      <Link to="/search" className="hint" style={{ display: "inline-block", marginBottom: "0.75rem" }}>
        ← Back to search
      </Link>
      <h1>
        Book {scene.book} · Episode {scene.episode_number}
        <span style={{ color: "var(--text-tertiary)", fontWeight: 400, fontSize: "0.9rem", marginLeft: "0.6rem" }}>
          {formatTime(scene.start_time)}–{formatTime(scene.end_time)} · {(scene.end_time - scene.start_time).toFixed(1)}s
        </span>
      </h1>

      <div className="scene-detail-layout" style={{ marginTop: "0.75rem" }}>
        <div>
          <video ref={videoRef} controls src={`${api.streamUrl(scene.source_id)}#t=${scene.start_time}`} />
          <div className="keyframes">
            {scene.keyframe_paths.map((_, i) => (
              <img key={i} src={api.keyframeUrl(scene.id, i)} alt="" />
            ))}
          </div>
          <button className="ghost" style={{ marginTop: "0.75rem" }} onClick={copyTimestamp}>
            {copied ? "Copied ✓" : "Copy timestamp + path"}
          </button>
        </div>

        <div>
          <div className="card">
            <label>Description</label>
            <textarea value={contextDescription} onChange={(e) => setContextDescription(e.target.value)} />

            <label style={{ marginTop: "0.85rem" }}>Characters</label>
            <TagMultiSelect value={characterTags} onChange={setCharacterTags} suggestions={facets?.characters ?? []} />

            <div style={{ marginTop: "0.75rem", display: "flex", gap: "0.5rem", alignItems: "center" }}>
              <button className="primary" onClick={() => saveMutation.mutate()} disabled={saveMutation.isPending}>
                {saveMutation.isPending ? "Saving…" : "Save"}
              </button>
              {saveMutation.isSuccess && <span className="badge success">saved</span>}
            </div>

            {scene.location_tags.length > 0 || scene.mood_action_tags.length > 0 ? (
              <div style={{ marginTop: "0.85rem" }}>
                {scene.location_tags.map((t) => (
                  <span className="tag" key={t}>
                    {t}
                  </span>
                ))}
                {scene.mood_action_tags.map((t) => (
                  <span className="tag" key={t}>
                    {t}
                  </span>
                ))}
              </div>
            ) : null}
          </div>

          <div className="card" style={{ marginTop: "0.75rem", maxHeight: "360px", overflowY: "auto" }}>
            <h3>Transcript</h3>
            {scene.transcript_lines.length === 0 && <p style={{ margin: 0 }}>No dialogue in this scene.</p>}
            {scene.transcript_lines.map((line) => (
              <div className="transcript-line" key={line.id}>
                <button type="button" className="ts ts-seek" title="Play from here" onClick={() => seek(line.start_time)}>
                  {formatTime(line.start_time)}
                </button>
                {line.speaker && <strong style={{ marginRight: "0.35rem" }}>{line.speaker}:</strong>}
                {line.text}
              </div>
            ))}
          </div>

          <div className="card" style={{ marginTop: "0.75rem" }}>
            <h3>Provenance</h3>
            <p style={{ margin: 0, fontSize: "0.8rem", fontFamily: "var(--font-mono)", color: "var(--text-secondary)" }}>
              scene id: {scene.id} · caption: {scene.caption_status}
              {scene.model_version && (
                <>
                  <br />
                  captioned by: {scene.model_version}
                </>
              )}
              {scene.text_embedding_model && (
                <>
                  <br />
                  text embedding: {scene.text_embedding_model}
                </>
              )}
              {scene.visual_embedding_model && (
                <>
                  <br />
                  visual embedding: {scene.visual_embedding_model}
                </>
              )}
              {scene.edited_by_user && (
                <>
                  <br />
                  edited by you
                </>
              )}
            </p>
          </div>

          <SceneTechnical scene={scene} onSeek={seek} />
        </div>
      </div>
    </div>
  );
}
