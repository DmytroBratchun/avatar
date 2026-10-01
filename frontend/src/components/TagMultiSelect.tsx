import { useMemo, useRef, useState } from "react";

interface Props {
  value: string[];
  onChange: (tags: string[]) => void;
  suggestions: string[];
  placeholder?: string;
}

export default function TagMultiSelect({ value, onChange, suggestions, placeholder }: Props) {
  const [input, setInput] = useState("");
  const [open, setOpen] = useState(false);
  const [highlight, setHighlight] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  const filtered = useMemo(() => {
    const q = input.trim().toLowerCase();
    return suggestions
      .filter((s) => !value.includes(s))
      .filter((s) => (q ? s.toLowerCase().includes(q) : true))
      .slice(0, 8);
  }, [input, suggestions, value]);

  function addTag(tag: string) {
    const clean = tag.trim();
    if (!clean || value.includes(clean)) return;
    onChange([...value, clean]);
    setInput("");
    setHighlight(0);
  }

  function removeTag(tag: string) {
    onChange(value.filter((t) => t !== tag));
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Enter") {
      e.preventDefault();
      if (open && filtered[highlight]) addTag(filtered[highlight]);
      else if (input.trim()) addTag(input);
    } else if (e.key === "Backspace" && input === "" && value.length > 0) {
      removeTag(value[value.length - 1]);
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      setHighlight((h) => Math.min(h + 1, filtered.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setHighlight((h) => Math.max(h - 1, 0));
    } else if (e.key === "Escape") {
      setOpen(false);
      inputRef.current?.blur();
    }
  }

  return (
    <div className="tag-select" onBlur={() => setTimeout(() => setOpen(false), 120)}>
      <input
        ref={inputRef}
        value={input}
        placeholder={placeholder}
        onChange={(e) => {
          setInput(e.target.value);
          setOpen(true);
          setHighlight(0);
        }}
        onFocus={() => setOpen(true)}
        onKeyDown={handleKeyDown}
      />
      {open && filtered.length > 0 && (
        <div className="tag-select-dropdown">
          {filtered.map((s, i) => (
            <div
              key={s}
              className={`tag-select-option${i === highlight ? " active" : ""}`}
              onMouseDown={(e) => {
                e.preventDefault();
                addTag(s);
              }}
              onMouseEnter={() => setHighlight(i)}
            >
              {s}
            </div>
          ))}
        </div>
      )}
      {value.length > 0 && (
        <div className="tag-chips-below">
          {value.map((tag) => (
            <span className="chip" key={tag}>
              {tag}
              <button
                type="button"
                className="chip-remove"
                onClick={() => removeTag(tag)}
                aria-label={`Remove ${tag}`}
              >
                ×
              </button>
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
