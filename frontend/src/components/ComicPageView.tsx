import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { SceneDetailOut } from "../api/client";

// A page of a comic on the scene page: the page as it was indexed, or the PDF itself
// opened at this page, with links to the pages before and after.
export default function ComicPageView({ scene }: { scene: SceneDetailOut }) {
  const [view, setView] = useState<"page" | "pdf">("page");
  const [copied, setCopied] = useState(false);
  const page = Math.round(scene.start_time) + 1;
  const { previous_scene_id: previous, next_scene_id: next } = scene.technical;

  function copyPath() {
    navigator.clipboard.writeText(`${scene.source_file_path} @ page ${page}`);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }

  return (
    <div>
      <div style={{ display: "flex", gap: "0.4rem", alignItems: "center", marginBottom: "0.5rem", flexWrap: "wrap" }}>
        <button className={view === "page" ? "primary" : "ghost"} onClick={() => setView("page")}>
          Page
        </button>
        <button
          className={view === "pdf" ? "primary" : "ghost"}
          onClick={() => setView("pdf")}
          disabled={!scene.source_file_exists}
          title={scene.source_file_exists ? "The whole PDF, opened at this page" : "The PDF is no longer at the path it was added from"}
        >
          PDF
        </button>
        <span style={{ flex: 1 }} />
        {previous != null ? (
          <Link className="ghost" to={`/scenes/${previous}`}>
            ← page {page - 1}
          </Link>
        ) : (
          <span />
        )}
        {next != null && (
          <Link className="ghost" to={`/scenes/${next}`} style={{ marginLeft: "0.6rem" }}>
            page {page + 1} →
          </Link>
        )}
      </div>

      {view === "page" ? (
        scene.keyframe_paths.length > 0 ? (
          <img
            key={scene.id}
            src={api.keyframeUrl(scene.id, 0)}
            alt={`page ${page}`}
            style={{ width: "100%", display: "block", borderRadius: "6px", background: "var(--bg-hover)" }}
          />
        ) : (
          <p className="hint">This page has not been rendered yet.</p>
        )
      ) : (
        // the browser's own PDF viewer, which takes the page to open from #page=
        <iframe
          key={scene.id}
          title="PDF"
          src={`${api.streamUrl(scene.source_id)}#page=${page}`}
          style={{ width: "100%", height: "80vh", border: 0, borderRadius: "6px" }}
        />
      )}

      <button className="ghost" style={{ marginTop: "0.75rem" }} onClick={copyPath}>
        {copied ? "Copied ✓" : "Copy page + path"}
      </button>
    </div>
  );
}
