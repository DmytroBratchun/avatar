import { useState } from "react";

export type View = "rows" | "grid";

/** How a list of scenes is shown - rows or a grid of pictures - remembered per browser. */
export function useView(key: string): [View, (view: View) => void] {
  const [view, setView] = useState<View>(() => {
    try {
      return localStorage.getItem(`view.${key}`) === "grid" ? "grid" : "rows";
    } catch {
      return "rows";
    }
  });
  const choose = (next: View) => {
    setView(next);
    try {
      localStorage.setItem(`view.${key}`, next);
    } catch {
      // not remembered: no storage
    }
  };
  return [view, choose];
}

export default function ViewToggle({ view, onChange }: { view: View; onChange: (view: View) => void }) {
  return (
    <span style={{ display: "inline-flex", gap: "0.25rem" }}>
      {(["rows", "grid"] as const).map((v) => (
        <button
          key={v}
          type="button"
          className={view === v ? "small" : "ghost small"}
          aria-pressed={view === v}
          title={v === "rows" ? "Rows: picture and the whole description" : "Grid: pictures first"}
          onClick={() => onChange(v)}
        >
          {v === "rows" ? "☰ Rows" : "▦ Grid"}
        </button>
      ))}
    </span>
  );
}
