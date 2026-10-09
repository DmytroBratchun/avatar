import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api/client";
import type { ClaudeUsageWindow } from "../api/client";

/** "in 2h 15m (9:20 PM)" / "Sat 1:00 AM, in 3 days" */
function resets(window: ClaudeUsageWindow | null | undefined): string {
  if (!window?.resets_at) return "";
  const at = new Date(window.resets_at);
  const minutes = Math.max(0, Math.round((at.getTime() - Date.now()) / 60000));
  const clock = at.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  if (minutes < 24 * 60) {
    const span = minutes >= 60 ? `${Math.floor(minutes / 60)}h ${minutes % 60}m` : `${minutes}m`;
    return `in ${span} (${clock})`;
  }
  const days = Math.round(minutes / (24 * 60));
  return `${at.toLocaleDateString([], { weekday: "short" })} ${clock}, in ${days} day${days === 1 ? "" : "s"}`;
}

function Row({ name, left, color, window }: { name: string; left: number; color: string; window: ClaudeUsageWindow | null | undefined }) {
  return (
    <div style={{ marginTop: "0.55rem" }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: "1.5rem" }}>
        <span>{name}</span>
        <strong>{Math.round(left)}% left</strong>
      </div>
      <div style={{ height: "4px", borderRadius: "2px", background: "var(--bg-hover)", margin: "0.25rem 0" }}>
        <div style={{ height: "100%", width: `${left}%`, borderRadius: "2px", background: color }} />
      </div>
      <div style={{ color: "var(--text-tertiary)" }}>Refills {resets(window)}</div>
    </div>
  );
}

// How much of the Claude subscription is left, as a battery: its charge is what
// remains of the current 5-hour session, and its outline turns red when little of
// the week is left. Captioning and every editor stage that calls Claude spend both.
export default function ClaudeUsage() {
  const { data } = useQuery({ queryKey: ["claudeUsage"], queryFn: api.claudeUsage, refetchInterval: 60_000 });
  const [open, setOpen] = useState(false);
  if (!data?.available || data.session?.percent == null) return null;

  const sessionLeft = Math.max(0, Math.min(100, 100 - data.session.percent));
  const weekLeft = data.week?.percent != null ? Math.max(0, Math.min(100, 100 - data.week.percent)) : null;
  const level = sessionLeft <= 10 ? "var(--danger)" : sessionLeft <= 30 ? "#d97706" : "#73ad21";
  const weekLow = weekLeft != null && weekLeft <= 20;
  const outline = weekLow ? "var(--danger)" : "var(--text-tertiary)";

  return (
    <span
      style={{ position: "relative", display: "flex", alignItems: "center", marginRight: "0.5rem", padding: "0.4rem 0" }}
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
    >
      <span style={{ border: `1px solid ${outline}`, borderRadius: "3px", padding: "1px", width: "25px", height: "9px", boxSizing: "content-box" }}>
        <span
          style={{
            display: "block",
            height: "9px",
            width: `${sessionLeft}%`,
            minWidth: sessionLeft > 0 ? "2px" : 0,
            borderRadius: "2px",
            background: level,
            transition: "width 0.4s",
          }}
        />
      </span>
      <span style={{ background: outline, borderRadius: "2px", margin: "2px", width: "1px", height: "3px" }} />
      {open && (
        <div
          className="card"
          style={{ position: "absolute", top: "100%", right: 0, zIndex: 30, width: "250px", fontSize: "0.78rem", boxShadow: "var(--shadow-md)" }}
        >
          <strong>Claude subscription</strong>
          <Row name="This session (5 hours)" left={sessionLeft} color={level} window={data.session} />
          {weekLeft != null && (
            <Row name="This week" left={weekLeft} color={weekLow ? "var(--danger)" : "#73ad21"} window={data.week} />
          )}
          <div style={{ color: "var(--text-tertiary)", marginTop: "0.55rem" }}>
            The battery shows the session; its outline turns red when the week is nearly used up.
          </div>
        </div>
      )}
    </span>
  );
}
