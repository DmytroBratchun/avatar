import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import type { SourceOut } from "../api/client";

function ErrorCell({ message }: { message: string }) {
  const [expanded, setExpanded] = useState(false);
  return (
    <span
      className="error-text"
      style={{ cursor: "pointer", display: "block", wordBreak: expanded ? "break-word" : "normal" }}
      onClick={() => setExpanded((v) => !v)}
      title={expanded ? "Click to collapse" : message}
    >
      {expanded ? message : "Error ▾"}
    </span>
  );
}

function StatusBadge({ status }: { status: string }) {
  if (status === "indexed") return <span className="badge success">indexed</span>;
  if (status === "failed") return <span className="badge danger">failed</span>;
  if (status === "pending") return <span className="badge">pending</span>;
  if (status === "paused") return <span className="badge">paused</span>;
  if (status === "unqueued") return <span className="badge">not queued</span>;
  if (status === "ready_for_captioning") return <span className="badge accent">ready for captioning</span>;
  return <span className="badge accent">{status.replace(/_/g, " ")}</span>;
}

function IndexBadgeRow({
  label,
  title,
  done,
  total,
}: {
  label: string;
  title: string;
  done: number;
  total: number;
}) {
  if (total === 0) return null;
  const full = done === total;
  return (
    <span className={`badge ${full ? "success" : done > 0 ? "accent" : ""}`} title={`${title}: ${done}/${total}`}>
      {label} {done}/{total}
    </span>
  );
}

function IndexBadge({ source }: { source: SourceOut }) {
  if (source.scene_count === 0) return <span className="hint">—</span>;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "0.25rem", fontFamily: "var(--font-mono)", fontSize: "0.78rem" }}>
      <IndexBadgeRow label="T" title="Text index" done={source.text_indexed_count} total={source.scene_count} />
      <IndexBadgeRow label="V" title="Visual index" done={source.visual_indexed_count} total={source.scene_count} />
    </div>
  );
}

function ReindexCell({
  requested,
  total,
  indexedCount,
  onClick,
  onCancel,
  pending,
  cancelling,
}: {
  requested: boolean;
  total: number;
  indexedCount: number;
  onClick: () => void;
  onCancel: () => void;
  pending: boolean;
  cancelling: boolean;
}) {
  const [hovering, setHovering] = useState(false);

  if (total === 0) return <span className="hint">—</span>;
  if (requested) {
    return (
      <button
        className="ghost small"
        onMouseEnter={() => setHovering(true)}
        onMouseLeave={() => setHovering(false)}
        onClick={onCancel}
        disabled={cancelling}
      >
        {hovering ? "Cancel" : "queued…"}
      </button>
    );
  }
  return (
    <button className="ghost small" onClick={onClick} disabled={pending}>
      {indexedCount === 0 ? "Index" : "Reindex"}
    </button>
  );
}

interface ColumnSettings {
  status: boolean;
  progress: boolean;
  index: boolean;
  cost: boolean;
  editBtn: boolean;
  reindexBtn: boolean;
  reindexTextBtn: boolean;
  reindexVisualBtn: boolean;
  summaryBtn: boolean;
  deleteBtn: boolean;
}

const DEFAULT_COLUMN_SETTINGS: ColumnSettings = {
  status: true,
  progress: true,
  index: true,
  cost: true,
  editBtn: true,
  reindexBtn: true,
  reindexTextBtn: true,
  reindexVisualBtn: true,
  summaryBtn: true,
  deleteBtn: true,
};

const COLUMN_SETTINGS_KEY = "library_column_settings";

function loadColumnSettings(): ColumnSettings {
  try {
    const raw = localStorage.getItem(COLUMN_SETTINGS_KEY);
    if (raw) return { ...DEFAULT_COLUMN_SETTINGS, ...JSON.parse(raw) };
  } catch {
    // ignore - private browsing / storage blocked
  }
  return DEFAULT_COLUMN_SETTINGS;
}

function TableSettingsMenu({ settings, onChange }: { settings: ColumnSettings; onChange: (s: ColumnSettings) => void }) {
  const [open, setOpen] = useState(false);

  function toggle(key: keyof ColumnSettings) {
    const next = { ...settings, [key]: !settings[key] };
    onChange(next);
    try {
      localStorage.setItem(COLUMN_SETTINGS_KEY, JSON.stringify(next));
    } catch {
      // ignore
    }
  }

  const options: [keyof ColumnSettings, string][] = [
    ["status", "Status column"],
    ["progress", "Progress column"],
    ["index", "Index coverage column"],
    ["cost", "Cost column"],
    ["editBtn", "Edit button"],
    ["reindexBtn", "Reindex button"],
    ["reindexTextBtn", "Reindex text column"],
    ["reindexVisualBtn", "Reindex visual column"],
    ["summaryBtn", "Episode summary column"],
    ["deleteBtn", "Delete button"],
  ];

  return (
    <div style={{ position: "relative" }}>
      <button className="ghost small" onClick={() => setOpen((v) => !v)}>
        ⚙ Columns
      </button>
      {open && (
        <div
          className="card"
          style={{ position: "absolute", right: 0, top: "calc(100% + 4px)", zIndex: 20, width: "200px", boxShadow: "var(--shadow-md)" }}
          onMouseLeave={() => setOpen(false)}
        >
          {options.map(([key, label]) => (
            <label key={key} style={{ display: "flex", alignItems: "center", gap: "0.5rem", fontSize: "0.82rem", fontWeight: 400, textTransform: "none", letterSpacing: 0, padding: "0.25rem 0", cursor: "pointer" }}>
              <input type="checkbox" checked={settings[key]} onChange={() => toggle(key)} style={{ width: "auto" }} />
              {label}
            </label>
          ))}
        </div>
      )}
    </div>
  );
}

function SourceRow({ source, columns }: { source: SourceOut; columns: ColumnSettings }) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [editing, setEditing] = useState(false);
  const [showTitle, setShowTitle] = useState(source.show_title ?? "");
  const [book, setBook] = useState(source.book?.toString() ?? "");
  const [episode, setEpisode] = useState(source.episode_number?.toString() ?? "");
  const [title, setTitle] = useState(source.episode_title ?? "");

  const updateMutation = useMutation({
    mutationFn: () =>
      api.updateSource(source.id, {
        show_title: showTitle || undefined,
        book: book ? Number(book) : undefined,
        episode_number: episode ? Number(episode) : undefined,
        episode_title: title || undefined,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["sources"] });
      setEditing(false);
    },
  });

  const reindexMutation = useMutation({
    mutationFn: () => api.reindexSource(source.id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["sources"] }),
  });

  const prepareMutation = useMutation({
    mutationFn: () => api.prepareSource(source.id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["sources"] }),
  });

  const deleteMutation = useMutation({
    mutationFn: () => api.deleteSource(source.id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["sources"] }),
  });

  const reindexVisualMutation = useMutation({
    mutationFn: () => api.reindexVisual(source.id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["sources"] }),
  });

  const reindexTextMutation = useMutation({
    mutationFn: () => api.reindexText(source.id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["sources"] }),
  });

  const cancelVisualMutation = useMutation({
    mutationFn: () => api.cancelVisualReindex(source.id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["sources"] }),
  });

  const cancelTextMutation = useMutation({
    mutationFn: () => api.cancelTextReindex(source.id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["sources"] }),
  });

  const summaryMutation = useMutation({
    mutationFn: () => api.requestEpisodeSummary(source.id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["sources"] }),
  });

  const cancelSummaryMutation = useMutation({
    mutationFn: () => api.cancelEpisodeSummary(source.id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["sources"] }),
  });

  const middleColSpan =
    1 +
    (columns.status ? 1 : 0) +
    (columns.progress ? 1 : 0) +
    (columns.index ? 1 : 0) +
    (columns.cost ? 1 : 0) +
    (columns.reindexTextBtn ? 1 : 0) +
    (columns.reindexVisualBtn ? 1 : 0) +
    (columns.summaryBtn ? 1 : 0);

  if (editing) {
    return (
      <tr>
        <td colSpan={2}>
          <input style={{ width: "100%", marginBottom: "0.3rem" }} placeholder="Show" value={showTitle} onChange={(e) => setShowTitle(e.target.value)} />
          <input style={{ width: "100%" }} placeholder="Episode title" value={title} onChange={(e) => setTitle(e.target.value)} autoFocus />
        </td>
        <td colSpan={middleColSpan}>
          <div className="field-row" style={{ maxWidth: "7rem" }}>
            <input value={book} onChange={(e) => setBook(e.target.value)} placeholder="Bk" />
            <input value={episode} onChange={(e) => setEpisode(e.target.value)} placeholder="Ep" />
          </div>
        </td>
        <td>
          <div className="row-actions">
            <button className="small primary" onClick={() => updateMutation.mutate()} disabled={updateMutation.isPending}>
              Save
            </button>
            <button className="small ghost" onClick={() => setEditing(false)}>
              Cancel
            </button>
          </div>
        </td>
      </tr>
    );
  }

  return (
    <tr>
      <td style={{ fontFamily: "var(--font-mono)", color: "var(--text-secondary)" }}>
        {source.book ?? "-"}/{source.episode_number ?? "-"}
      </td>
      <td
        style={{ cursor: "pointer", textDecoration: "underline", textDecorationColor: "transparent" }}
        onClick={() => navigate(`/sources/${source.id}`)}
        onMouseEnter={(e) => (e.currentTarget.style.textDecorationColor = "currentColor")}
        onMouseLeave={(e) => (e.currentTarget.style.textDecorationColor = "transparent")}
      >
        {source.episode_title ?? source.title ?? source.file_path}
      </td>
      {columns.status && (
        <td>
          <StatusBadge status={source.status} />
        </td>
      )}
      {columns.progress && (
        <td style={{ color: "var(--text-tertiary)", fontSize: "0.8rem" }}>
          {source.current_stage_detail ?? (source.error_message ? <ErrorCell message={source.error_message} /> : "—")}
        </td>
      )}
      {columns.index && (
        <td>
          <IndexBadge source={source} />
        </td>
      )}
      {columns.reindexTextBtn && (
        <td>
          <ReindexCell
            requested={source.text_reindex_requested}
            total={source.scene_count}
            indexedCount={source.text_indexed_count}
            onClick={() => reindexTextMutation.mutate()}
            onCancel={() => cancelTextMutation.mutate()}
            pending={reindexTextMutation.isPending}
            cancelling={cancelTextMutation.isPending}
          />
        </td>
      )}
      {columns.reindexVisualBtn && (
        <td>
          <ReindexCell
            requested={source.visual_reindex_requested}
            total={source.scene_count}
            indexedCount={source.visual_indexed_count}
            onClick={() => reindexVisualMutation.mutate()}
            onCancel={() => cancelVisualMutation.mutate()}
            pending={reindexVisualMutation.isPending}
            cancelling={cancelVisualMutation.isPending}
          />
        </td>
      )}
      {columns.summaryBtn && (
        <td>
          <ReindexCell
            requested={source.episode_summary_requested}
            total={1}
            indexedCount={source.episode_summary ? 1 : 0}
            onClick={() => summaryMutation.mutate()}
            onCancel={() => cancelSummaryMutation.mutate()}
            pending={summaryMutation.isPending}
            cancelling={cancelSummaryMutation.isPending}
          />
        </td>
      )}
      {columns.cost && (
        <td style={{ fontFamily: "var(--font-mono)", color: "var(--text-secondary)" }}>
          {source.caption_cost_usd != null ? `$${source.caption_cost_usd.toFixed(2)}` : "—"}
        </td>
      )}
      <td>
        <div className="row-actions">
          {columns.editBtn && (
            <button className="small ghost" onClick={() => setEditing(true)}>
              Edit
            </button>
          )}
          {columns.reindexBtn && (
            <button
              className="small ghost"
              title="Free, local only: transcript, shots, scenes, keyframes and the visual index. No captioning, nothing sent to Claude."
              onClick={() => prepareMutation.mutate()}
              disabled={prepareMutation.isPending || source.status === "paused"}
            >
              {source.status === "paused" ? "Preparing…" : "Prepare (free)"}
            </button>
          )}
          {columns.reindexBtn && (
            <button
              className="small ghost"
              title="Full index including captioning - paid, runs when the captioning worker is started"
              onClick={() => reindexMutation.mutate()}
            >
              Reindex
            </button>
          )}
          {columns.deleteBtn && (
            <button className="small ghost" onClick={() => deleteMutation.mutate()}>
              Delete
            </button>
          )}
        </div>
      </td>
    </tr>
  );
}

function AddSourcesPanel({ onClose }: { onClose: () => void }) {
  const queryClient = useQueryClient();

  const [showTitle, setShowTitle] = useState("");
  const [folderPath, setFolderPath] = useState("");
  const [scanMessage, setScanMessage] = useState<string | null>(null);

  const scanMutation = useMutation({
    mutationFn: () => api.scanFolder(folderPath, showTitle || undefined),
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ["sources"] });
      setScanMessage(`Added ${result.added.length} new source(s), skipped ${result.skipped_existing} already known.`);
    },
  });

  const [showManualAdd, setShowManualAdd] = useState(false);
  const [manualForm, setManualForm] = useState({ file_path: "", book: "", episode_number: "", episode_title: "" });
  const createMutation = useMutation({
    mutationFn: () =>
      api.createSource({
        file_path: manualForm.file_path,
        show_title: showTitle || undefined,
        book: manualForm.book ? Number(manualForm.book) : undefined,
        episode_number: manualForm.episode_number ? Number(manualForm.episode_number) : undefined,
        episode_title: manualForm.episode_title || undefined,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["sources"] });
      setManualForm({ file_path: "", book: "", episode_number: "", episode_title: "" });
    },
  });

  return (
    <div className="card" style={{ marginTop: "0.9rem" }}>
      <div className="card-header">
        <h3 style={{ margin: 0 }}>Add episodes</h3>
        <button className="ghost small" onClick={onClose}>
          ✕
        </button>
      </div>
      <p className="hint">
        Points at a folder on disk, searched recursively. Video files are registered in place — nothing gets copied.
        Book/episode numbers are guessed from filenames like <code>S01E03</code> or folder names like{" "}
        <code>Book 1</code>; fix them below if the guess is wrong.
      </p>

      <label>Show name</label>
      <input
        placeholder="e.g. Avatar: The Last Airbender (defaults to the folder name if left blank)"
        value={showTitle}
        onChange={(e) => setShowTitle(e.target.value)}
      />

      <form
        className="inline"
        style={{ marginTop: "0.6rem" }}
        onSubmit={(e) => {
          e.preventDefault();
          setScanMessage(null);
          scanMutation.mutate();
        }}
      >
        <input
          style={{ flex: 1 }}
          placeholder="e.g. D:\Avatar"
          value={folderPath}
          onChange={(e) => setFolderPath(e.target.value)}
          required
        />
        <button className="primary" type="submit" disabled={scanMutation.isPending}>
          {scanMutation.isPending ? "Scanning…" : "Scan folder"}
        </button>
      </form>
      {scanMessage && <p style={{ marginTop: "0.6rem", marginBottom: 0 }}>{scanMessage}</p>}
      {scanMutation.isError && <p className="error-text">{(scanMutation.error as Error).message}</p>}

      <button className="ghost small" style={{ marginTop: "0.85rem" }} onClick={() => setShowManualAdd((v) => !v)}>
        {showManualAdd ? "Hide" : "+ Add a single file instead"}
      </button>

      {showManualAdd && (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            createMutation.mutate();
          }}
          style={{ marginTop: "0.85rem", maxWidth: "420px" }}
        >
          <input
            placeholder="Full path to video file"
            value={manualForm.file_path}
            onChange={(e) => setManualForm({ ...manualForm, file_path: e.target.value })}
            required
          />
          <input
            placeholder="Episode title"
            value={manualForm.episode_title}
            onChange={(e) => setManualForm({ ...manualForm, episode_title: e.target.value })}
          />
          <div className="field-row">
            <input
              placeholder="Book (1-3)"
              value={manualForm.book}
              onChange={(e) => setManualForm({ ...manualForm, book: e.target.value })}
            />
            <input
              placeholder="Episode #"
              value={manualForm.episode_number}
              onChange={(e) => setManualForm({ ...manualForm, episode_number: e.target.value })}
            />
          </div>
          <button className="primary" type="submit" disabled={createMutation.isPending} style={{ alignSelf: "flex-start" }}>
            {createMutation.isPending ? "Adding…" : "Add source"}
          </button>
          {createMutation.isError && <p className="error-text">{(createMutation.error as Error).message}</p>}
        </form>
      )}
    </div>
  );
}

const UNKNOWN_SHOW = "Unsorted";

export default function LibraryPage() {
  const { data: sources } = useQuery({
    queryKey: ["sources"],
    queryFn: api.listSources,
    refetchInterval: 3000,
  });
  const [addOpen, setAddOpen] = useState(false);
  const [searchParams, setSearchParams] = useSearchParams();
  const selectedShow = searchParams.get("show");
  const selectedBook = searchParams.get("book");

  function setSelectedShow(show: string | null) {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      if (show) next.set("show", show);
      else next.delete("show");
      next.delete("book");
      return next;
    });
  }

  function setSelectedBook(book: string | null) {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      if (book) next.set("book", book);
      else next.delete("book");
      return next;
    });
  }

  const [columns, setColumns] = useState<ColumnSettings>(loadColumnSettings);

  const tree = useMemo(() => {
    const byShow = new Map<string, Map<string, number>>();
    for (const s of sources ?? []) {
      const show = s.show_title ?? UNKNOWN_SHOW;
      const book = s.book != null ? `Book ${s.book}` : "Unsorted";
      if (!byShow.has(show)) byShow.set(show, new Map());
      const books = byShow.get(show)!;
      books.set(book, (books.get(book) ?? 0) + 1);
    }
    return Array.from(byShow.entries())
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([show, books]) => ({
        show,
        total: Array.from(books.values()).reduce((a, b) => a + b, 0),
        books: Array.from(books.entries()).sort(([a], [b]) => a.localeCompare(b)),
      }));
  }, [sources]);

  const filteredSources = useMemo(() => {
    if (!sources) return sources;
    return sources.filter((s) => {
      const show = s.show_title ?? UNKNOWN_SHOW;
      if (selectedShow && show !== selectedShow) return false;
      if (selectedBook) {
        const book = s.book != null ? `Book ${s.book}` : "Unsorted";
        if (book !== selectedBook) return false;
      }
      return true;
    });
  }, [sources, selectedShow, selectedBook]);

  const indexedCount = sources?.filter((s) => s.status === "indexed").length ?? 0;

  return (
    <div>
      <div className="card-header" style={{ alignItems: "center" }}>
        <div>
          <h1 style={{ marginBottom: "0.15rem" }}>Library</h1>
          <p style={{ margin: 0, fontSize: "0.85rem" }}>
            {sources?.length ?? 0} source{sources?.length === 1 ? "" : "s"} · {indexedCount} indexed
          </p>
        </div>
        {!addOpen && (
          <button className="ghost small" onClick={() => setAddOpen(true)}>
            + Add episodes
          </button>
        )}
      </div>
      {addOpen && <AddSourcesPanel onClose={() => setAddOpen(false)} />}

      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: "1.25rem", marginBottom: "0.6rem" }}>
        <h2 style={{ margin: 0, fontSize: "1.05rem" }}>
          {selectedShow ?? "All shows"}
          {selectedBook ? ` / ${selectedBook}` : ""}
        </h2>
        <TableSettingsMenu settings={columns} onChange={setColumns} />
      </div>

      <div className="search-layout">
        <aside className="filters">
          <div className="card">
            <h3>Shows</h3>
            <div
              className={`tag-select-option${selectedShow === null ? " active" : ""}`}
              style={{ cursor: "pointer", fontWeight: 600 }}
              onClick={() => setSelectedShow(null)}
            >
              All ({sources?.length ?? 0})
            </div>
            {tree.map(({ show, total, books }) => (
              <div key={show} style={{ marginTop: "0.4rem" }}>
                <div
                  className={`tag-select-option${selectedShow === show && !selectedBook ? " active" : ""}`}
                  style={{ cursor: "pointer", fontWeight: 600 }}
                  onClick={() => setSelectedShow(show)}
                >
                  {show} ({total})
                </div>
                {selectedShow === show &&
                  books.map(([book, count]) => (
                    <div
                      key={book}
                      className={`tag-select-option${selectedBook === book ? " active" : ""}`}
                      style={{ cursor: "pointer", paddingLeft: "1.1rem", fontSize: "0.82rem" }}
                      onClick={() => setSelectedBook(book)}
                    >
                      {book} ({count})
                    </div>
                  ))}
              </div>
            ))}
          </div>
        </aside>

        <div className="results">
          {filteredSources && filteredSources.length === 0 ? (
            <div className="card">
              <p className="empty-state" style={{ padding: 0 }}>
                No sources here yet.
              </p>
            </div>
          ) : (
            <div className="card" style={{ padding: 0, overflow: "hidden" }}>
              <table>
                <thead>
                  <tr>
                    <th>Book/Ep</th>
                    <th>Title</th>
                    {columns.status && <th>Status</th>}
                    {columns.progress && <th>Progress</th>}
                    {columns.index && <th>Index</th>}
                    {columns.reindexTextBtn && <th>Reindex T</th>}
                    {columns.reindexVisualBtn && <th>Reindex V</th>}
                    {columns.summaryBtn && <th>Summary</th>}
                    {columns.cost && <th>Cost</th>}
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {filteredSources?.map((s) => (
                    <SourceRow key={s.id} source={s} columns={columns} />
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
