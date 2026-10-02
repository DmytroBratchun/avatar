import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { SourceOut } from "../api/client";

const LOCAL_TERMINAL = new Set(["indexed", "failed", "ready_for_captioning", "unqueued"]);

interface Task {
  source: SourceOut;
  label: string;
  kind: string;
  active: boolean;
  detail?: string | null;
}

function buildTaskList(
  sources: SourceOut[] | undefined,
  localCurrentId: number | null | undefined,
  captionCurrentId: number | null | undefined
): Record<string, Task[]> {
  const groups: Record<string, Task[]> = {
    "Local processing (free)": [],
    "Captioning pipeline (paid)": [],
    "Episode summary (paid, small)": [],
    "Text reindex (free)": [],
    "Visual reindex (free)": [],
  };
  for (const source of sources ?? []) {
    const label = `${source.show_title ?? ""} Book ${source.book ?? "?"} Ep ${source.episode_number ?? "?"}`;
    // once the local worker starts a source its status is no longer "paused"
    // (transcribing, detecting_shots, ...) - keep it in this group, not the paid one
    if (source.status === "paused" || (source.id === localCurrentId && !LOCAL_TERMINAL.has(source.status))) {
      groups["Local processing (free)"].push({
        source,
        label,
        kind: "local",
        active: source.id === localCurrentId,
        detail: source.id === localCurrentId ? source.current_stage_detail : null,
      });
    } else if (!LOCAL_TERMINAL.has(source.status)) {
      groups["Captioning pipeline (paid)"].push({
        source,
        label,
        kind: "captioning",
        active: source.id === captionCurrentId,
        detail: source.id === captionCurrentId ? source.current_stage_detail : null,
      });
    }
    if (source.episode_summary_requested) {
      groups["Episode summary (paid, small)"].push({
        source,
        label,
        kind: "summary",
        active: source.id === captionCurrentId,
        detail: source.id === captionCurrentId ? source.current_stage_detail : null,
      });
    }
    if (source.text_reindex_requested) {
      groups["Text reindex (free)"].push({
        source,
        label,
        kind: "text",
        active: source.id === localCurrentId,
        detail: source.id === localCurrentId ? source.current_stage_detail : null,
      });
    }
    if (source.visual_reindex_requested) {
      groups["Visual reindex (free)"].push({
        source,
        label,
        kind: "visual",
        active: source.id === localCurrentId,
        detail: source.id === localCurrentId ? source.current_stage_detail : null,
      });
    }
  }
  return groups;
}

const CANCELABLE_KINDS = new Set(["local", "captioning", "text", "visual", "summary"]);

function TaskGroup({
  title,
  tasks,
  onDelete,
  deleting,
}: {
  title: string;
  tasks: Task[];
  onDelete: (t: Task) => void;
  deleting: (t: Task) => boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  if (tasks.length === 0) return null;
  const shown = expanded ? tasks : tasks.slice(0, 5);

  return (
    <div style={{ marginBottom: "0.75rem" }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: "0.5rem" }}>
        <strong style={{ fontSize: "0.85rem" }}>{title}</strong>
        <span className="hint" style={{ margin: 0 }}>
          {tasks.length} queued
        </span>
      </div>
      <div style={{ marginTop: "0.3rem", display: "flex", flexDirection: "column", gap: "0.2rem" }}>
        {shown.map((t) => (
          <div
            key={`${t.kind}-${t.source.id}`}
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              gap: "0.5rem",
              fontSize: "0.8rem",
              padding: "0.2rem 0.4rem",
              borderRadius: "var(--radius-sm)",
              background: t.active ? "var(--accent-soft, rgba(99,102,241,0.12))" : "transparent",
            }}
          >
            <Link to="/" style={{ color: t.active ? "var(--text-primary)" : "var(--text-secondary)" }}>
              {t.active ? "▶ " : ""}
              {t.label}
            </Link>
            <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
              {t.active && t.detail && <span className="hint" style={{ margin: 0 }}>{t.detail}</span>}
              {CANCELABLE_KINDS.has(t.kind) && (
                <button
                  className="ghost small"
                  title="Remove from queue"
                  onClick={() => onDelete(t)}
                  disabled={deleting(t)}
                >
                  ×
                </button>
              )}
            </div>
          </div>
        ))}
      </div>
      {tasks.length > 5 && (
        <button className="ghost small" style={{ marginTop: "0.3rem" }} onClick={() => setExpanded((v) => !v)}>
          {expanded ? "Show less" : `+${tasks.length - 5} more`}
        </button>
      )}
    </div>
  );
}

function Stat({ label, value, hint }: { label: string; value: string | number; hint?: string }) {
  return (
    <div style={{ minWidth: "120px" }}>
      <div style={{ fontSize: "1.4rem", fontWeight: 600, color: "var(--text-primary)" }}>{value}</div>
      <div style={{ fontSize: "0.75rem", color: "var(--text-tertiary)", textTransform: "uppercase", letterSpacing: "0.03em" }}>
        {label}
      </div>
      {hint && <div className="hint" style={{ marginTop: "0.15rem", marginBottom: 0 }}>{hint}</div>}
    </div>
  );
}

function WorkerCard({
  title,
  description,
  status,
  actions,
}: {
  title: string;
  description: string;
  status?: { running: boolean; reason: string | null; status?: string; current_episode?: string | null; detail?: string | null };
  actions?: React.ReactNode;
}) {
  return (
    <div className="card">
      <div className="card-header">
        <h3 style={{ margin: 0 }}>{title}</h3>
        {status && (
          <span className={`badge ${status.running ? "success" : "danger"}`}>{status.running ? "running" : "stopped"}</span>
        )}
      </div>
      <p className="hint" style={{ marginBottom: "0.5rem" }}>{description}</p>
      {status?.running && status.status === "processing" && (
        <p style={{ margin: 0, fontSize: "0.85rem" }}>Processing: {status.current_episode}</p>
      )}
      {status?.running && status.status === "idle" && <p style={{ margin: 0, fontSize: "0.85rem" }}>Idle - queue empty</p>}
      {status?.running && status.status === "waiting" && (
        <p className="error-text" style={{ margin: 0 }}>
          Paused on {status.current_episode}: {status.detail}
        </p>
      )}
      {!status?.running && status?.reason && <p className="error-text" style={{ marginTop: "0.3rem" }}>{status.reason}</p>}
      {actions && <div style={{ marginTop: "0.6rem" }}>{actions}</div>}
    </div>
  );
}

export default function PipelinePage() {
  const queryClient = useQueryClient();
  const [workerError, setWorkerError] = useState<string | null>(null);
  const { data: config } = useQuery({ queryKey: ["config"], queryFn: api.getConfig });
  const { data: workerStatus } = useQuery({ queryKey: ["workerStatus"], queryFn: api.getWorkerStatus, refetchInterval: 5000 });
  const { data: localWorkerStatus } = useQuery({
    queryKey: ["localWorkerStatus"],
    queryFn: api.getLocalWorkerStatus,
    refetchInterval: 5000,
  });
  const { data: stats } = useQuery({ queryKey: ["indexStats"], queryFn: api.getIndexStats, refetchInterval: 5000 });
  const { data: sources } = useQuery({ queryKey: ["sources"], queryFn: api.listSources, refetchInterval: 5000 });

  const taskGroups = buildTaskList(sources, localWorkerStatus?.current_source_id, workerStatus?.current_source_id);
  const totalQueued = Object.values(taskGroups).reduce((sum, tasks) => sum + tasks.length, 0);

  const startMutation = useMutation({
    mutationFn: api.startWorker,
    onSuccess: () => {
      setWorkerError(null);
      queryClient.invalidateQueries({ queryKey: ["workerStatus"] });
    },
    onError: (err: Error) => setWorkerError(err.message),
  });
  const stopMutation = useMutation({
    mutationFn: api.stopWorker,
    onSuccess: () => {
      setWorkerError(null);
      queryClient.invalidateQueries({ queryKey: ["workerStatus"] });
    },
    onError: (err: Error) => setWorkerError(err.message),
  });

  const [cancelingKey, setCancelingKey] = useState<string | null>(null);
  const cancelMutation = useMutation({
    mutationFn: (t: Task) => {
      switch (t.kind) {
        case "local":
        case "captioning":
          return api.dequeueSource(t.source.id);
        case "text":
          return api.cancelTextReindex(t.source.id);
        case "visual":
          return api.cancelVisualReindex(t.source.id);
        case "summary":
          return api.cancelEpisodeSummary(t.source.id);
        default:
          return Promise.reject(new Error(`Cannot cancel task kind: ${t.kind}`));
      }
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["sources"] }),
    onSettled: () => setCancelingKey(null),
  });

  return (
    <div>
      <h1>Pipeline</h1>
      <p className="hint" style={{ marginTop: "-0.5rem" }}>
        What's actually running right now, and how much of the library each index covers.
      </p>

      <div className="field-row" style={{ marginTop: "0.75rem" }}>
        <WorkerCard
          title="Local worker"
          description="Free, local-only: transcribe / detect shots / group scenes / keyframes. No API calls."
          status={localWorkerStatus}
        />
        <WorkerCard
          title="Captioning worker"
          description="Runs captioning + embeddings. Calls the LLM - the only paid/subscription-metered stage."
          status={workerStatus}
          actions={
            <>
              {!workerStatus?.running ? (
                <button className="small primary" onClick={() => startMutation.mutate()} disabled={startMutation.isPending}>
                  {startMutation.isPending ? "Starting…" : "Start captioning worker"}
                </button>
              ) : (
                <button className="small ghost" onClick={() => stopMutation.mutate()} disabled={stopMutation.isPending}>
                  {stopMutation.isPending ? "Stopping…" : "Stop"}
                </button>
              )}
              {!workerStatus?.running && (
                <p className="hint" style={{ marginTop: "0.3rem", marginBottom: 0 }}>
                  Spends real money/subscription usage - runs captioning on every source with status "pending".
                </p>
              )}
              {workerError && <p className="error-text" style={{ marginTop: "0.3rem" }}>{workerError}</p>}
            </>
          }
        />
      </div>

      <div className="card" style={{ marginTop: "0.75rem" }}>
        <div className="card-header">
          <h3 style={{ margin: 0 }}>Task queue</h3>
          <span className="hint" style={{ margin: 0 }}>{totalQueued} total</span>
        </div>
        <p className="hint">
          Everything waiting or currently running, by queue. ▶ marks what each worker is on right now; click a name
          to jump to it in Library.
        </p>
        {totalQueued === 0 && <p className="hint" style={{ margin: 0 }}>Nothing queued - both workers idle.</p>}
        {Object.entries(taskGroups).map(([title, tasks]) => (
          <TaskGroup
            key={title}
            title={title}
            tasks={tasks}
            onDelete={(t) => {
              setCancelingKey(`${t.kind}-${t.source.id}`);
              cancelMutation.mutate(t);
            }}
            deleting={(t) => cancelingKey === `${t.kind}-${t.source.id}`}
          />
        ))}
      </div>

      <div className="card" style={{ marginTop: "0.75rem" }}>
        <h3>Captioning config</h3>
        {config && (
          <p style={{ margin: 0 }}>
            <span className="badge accent">
              {config.caption_provider === "claude_code"
                ? "Claude Code CLI (subscription)"
                : config.caption_provider === "local"
                  ? "Local model via Ollama (free)"
                  : "Anthropic API (metered)"}{" "}
              · {config.caption_model}
            </span>
          </p>
        )}
      </div>

      <div className="card" style={{ marginTop: "0.75rem" }}>
        <h3>Index coverage</h3>
        <p className="hint">
          Two independent indexes, built at different stages. Transcript keyword search needs neither. Semantic search
          needs the matching index below to be populated for a scene.
        </p>
        {stats && (
          <div style={{ display: "flex", gap: "2rem", flexWrap: "wrap" }}>
            <Stat label="Total scenes" value={stats.total_scenes} />
            <Stat
              label="Captioned"
              value={`${stats.captioned} / ${stats.total_scenes}`}
              hint={stats.caption_error > 0 ? `${stats.caption_error} errored` : undefined}
            />
            <Stat
              label="Text index (BGE)"
              value={`${stats.text_indexed} / ${stats.total_scenes}`}
              hint="powers mode=text and mode=hybrid"
            />
            <Stat
              label="Visual index (CLIP)"
              value={`${stats.visual_indexed} / ${stats.total_scenes}`}
              hint="powers mode=visual and mode=hybrid"
            />
            <Stat label="Transcript lines" value={stats.transcript_lines} hint="powers keyword search, always free" />
            <Stat
              label="Text reindex queue"
              value={stats.text_reindex_queue}
              hint="from Library's 'Reindex T' button - drained by the local worker, no captioning needed"
            />
            <Stat
              label="Visual reindex queue"
              value={stats.visual_reindex_queue}
              hint="from Library's 'Reindex V' button - drained by the local worker, no captioning needed"
            />
          </div>
        )}
      </div>

      <div className="card" style={{ marginTop: "0.75rem" }}>
        <h3>Episode summaries</h3>
        <p className="hint">
          A short written summary of an episode - one small text-only LLM call, done by the Captioning worker.
          Optional: useful for shows the model does not already know.
        </p>
        {stats && (
          <div style={{ display: "flex", gap: "2rem", flexWrap: "wrap" }}>
            <Stat
              label="Episodes summarized"
              value={`${stats.episodes_summarized} / ${stats.total_sources}`}
              hint="one small text-only LLM call per episode"
            />
            <Stat
              label="Summary queue"
              value={stats.episode_summary_queue}
              hint="drained by the Captioning worker (costs a small amount)"
            />
          </div>
        )}
      </div>
    </div>
  );
}
