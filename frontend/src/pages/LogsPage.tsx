import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";

// terminal colour codes (the Vite log has them) are noise in a web page
// eslint-disable-next-line no-control-regex
const ANSI = /\u001b\[[0-9;]*m/g;

function lineColor(line: string): string | undefined {
  if (/\[ERROR\]|Traceback|Error:|error=(?!None)|status=failed/.test(line)) return "var(--danger, #dc2626)";
  if (/\[WARNING\]/.test(line)) return "var(--warning, #d97706)";
  return undefined;
}

export default function LogsPage() {
  const [name, setName] = useState("captioning");
  const [onlyProblems, setOnlyProblems] = useState(false);
  const [follow, setFollow] = useState(true);
  const boxRef = useRef<HTMLDivElement>(null);

  const { data: logs } = useQuery({ queryKey: ["logs"], queryFn: api.listLogs, refetchInterval: 5000 });
  const { data: log } = useQuery({
    queryKey: ["log", name],
    queryFn: () => api.tailLog(name, 1000),
    refetchInterval: 3000,
  });

  const lines = (log?.lines ?? []).map((l) => l.replace(ANSI, ""));
  const shown = onlyProblems ? lines.filter((l) => lineColor(l) !== undefined) : lines;

  // keep the newest line in view unless the user has turned following off to read back
  useEffect(() => {
    if (follow && boxRef.current) boxRef.current.scrollTop = boxRef.current.scrollHeight;
  }, [shown.length, follow, name]);

  return (
    <div>
      <h1>Logs</h1>
      <div style={{ display: "flex", gap: "0.4rem", flexWrap: "wrap", alignItems: "center" }}>
        {(logs ?? []).map((l) => (
          <button key={l.name} className={l.name === name ? "primary small" : "small"} onClick={() => setName(l.name)}>
            {l.label}
            {l.active ? " ●" : ""}
          </button>
        ))}
        <div style={{ flex: 1 }} />
        <label style={{ display: "flex", alignItems: "center", gap: "0.35rem", margin: 0 }}>
          <input type="checkbox" style={{ width: "auto" }} checked={onlyProblems} onChange={(e) => setOnlyProblems(e.target.checked)} />
          Only errors and warnings
        </label>
        <label style={{ display: "flex", alignItems: "center", gap: "0.35rem", margin: 0 }}>
          <input type="checkbox" style={{ width: "auto" }} checked={follow} onChange={(e) => setFollow(e.target.checked)} />
          Follow
        </label>
      </div>
      <p className="hint" style={{ marginTop: "0.4rem" }}>
        ● = written to in the last minute. Shows the last {lines.length} lines, refreshed every 3 seconds.
      </p>

      <div
        ref={boxRef}
        className="card"
        style={{
          height: "70vh",
          overflow: "auto",
          fontFamily: "ui-monospace, Consolas, monospace",
          fontSize: "0.75rem",
          lineHeight: 1.5,
          whiteSpace: "pre-wrap",
          wordBreak: "break-word",
        }}
      >
        {shown.length === 0 && (
          <span className="hint">{onlyProblems ? "No errors or warnings in the last lines." : "This log is empty."}</span>
        )}
        {shown.map((line, i) => (
          <div key={i} style={{ color: lineColor(line) }}>
            {line}
          </div>
        ))}
      </div>
    </div>
  );
}
