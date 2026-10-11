import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api/client";

/** Under a script field: has Claude rewrite the script for the synthetic voice -
 * directions like [whispers], pauses, numbers in words - replacing the text in the
 * field. The text as it was can be put back. */
export default function VoiceTextTools({
  text,
  disabled,
  onChange,
  instructions: initialInstructions = "",
}: {
  text: string;
  disabled?: boolean;
  onChange: (text: string) => void;
  // what the field starts with: the preset's "how it should sound"
  instructions?: string;
}) {
  const [instructions, setInstructions] = useState(initialInstructions);
  // the script as it was before it was prepared, while the prepared one is still in the field
  const [before, setBefore] = useState<{ original: string; prepared: string } | null>(null);

  const prepare = useMutation({
    mutationFn: () => api.prepareVoiceText(text, instructions),
    onSuccess: (result) => {
      setBefore({ original: text, prepared: result.text });
      onChange(result.text);
    },
  });
  const canUndo = before !== null && before.prepared === text;
  const changes = canUndo ? (prepare.data?.changes ?? []) : [];

  return (
    <div style={{ marginTop: "0.5rem" }}>
      <div style={{ display: "flex", gap: "0.5rem", alignItems: "center", flexWrap: "wrap" }}>
        <input
          style={{ flex: 1, minWidth: "14rem" }}
          placeholder="How it should sound (optional): calm documentary, whisper the dark parts…"
          value={instructions}
          disabled={disabled || prepare.isPending}
          onChange={(e) => setInstructions(e.target.value)}
        />
        <button
          className="small"
          disabled={disabled || prepare.isPending || !text.trim()}
          onClick={() => prepare.mutate()}
          title="One Claude call (from the subscription). Adds directions to the voice like [whispers] where the delivery changes, pauses, and spells out numbers and abbreviations - by ElevenLabs' own guidance. The words stay; the text in the field is replaced."
        >
          {prepare.isPending ? "Preparing…" : "Optimize for voice"}
        </button>
        {canUndo && (
          <button
            className="ghost small"
            disabled={disabled}
            onClick={() => {
              onChange(before.original);
              setBefore(null);
            }}
          >
            Undo
          </button>
        )}
      </div>
      {prepare.isPending && <p className="hint">Claude is reading the script - up to a minute or two for a long one.</p>}
      {prepare.isError && <p className="error-text">{(prepare.error as Error).message}</p>}
      {canUndo && changes.length === 0 && <p className="hint">Done - directions and pauses added, the wording is unchanged.</p>}
      {changes.length > 0 && (
        <div className="hint">
          The wording changed in {changes.length === 1 ? "one place" : `${changes.length} places`} - check, or undo:
          <ul style={{ margin: "0.25rem 0 0", paddingLeft: "1.2rem" }}>
            {changes.map((change, i) => (
              <li key={i}>
                “{change.was}” → “{change.now}”
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
