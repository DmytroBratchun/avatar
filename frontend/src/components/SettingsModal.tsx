import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api } from "../api/client";

export default function SettingsModal({ onClose }: { onClose: () => void }) {
  const queryClient = useQueryClient();
  const { data: settings, isLoading } = useQuery({ queryKey: ["settings"], queryFn: api.getSettings });

  const [provider, setProvider] = useState<"claude_code" | "api" | "local">("claude_code");
  const [model, setModel] = useState("claude-sonnet-5");
  const [apiKey, setApiKey] = useState("");
  const [showKey, setShowKey] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (settings) {
      setProvider(settings.caption_provider as "claude_code" | "api" | "local");
      setModel(settings.caption_model);
      setApiKey(settings.anthropic_api_key_from_env ? "" : settings.anthropic_api_key);
    }
  }, [settings]);

  function handleProviderChange(next: "claude_code" | "api" | "local") {
    setProvider(next);
    if (next === "local" && !model.startsWith("qwen")) {
      setModel("qwen3-vl:8b");
    } else if (next !== "local" && model.startsWith("qwen")) {
      setModel("claude-sonnet-5");
    }
  }

  const saveMutation = useMutation({
    mutationFn: () => api.updateSettings({ caption_provider: provider, caption_model: model, anthropic_api_key: apiKey }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["settings"] });
      queryClient.invalidateQueries({ queryKey: ["config"] });
      setSaved(true);
      setTimeout(() => setSaved(false), 1500);
    },
  });

  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.4)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 100,
      }}
      onClick={onClose}
    >
      <div className="card" style={{ width: "440px", maxWidth: "90vw" }} onClick={(e) => e.stopPropagation()}>
        <div className="card-header">
          <h2 style={{ margin: 0, fontSize: "1rem" }}>Settings</h2>
          <button className="ghost small" onClick={onClose}>
            ✕
          </button>
        </div>

        {isLoading ? (
          <p>Loading…</p>
        ) : (
          <>
            <label>Captioning provider</label>
            <select value={provider} onChange={(e) => handleProviderChange(e.target.value as typeof provider)}>
              <option value="claude_code">Claude Code CLI (your Claude subscription)</option>
              <option value="api">Anthropic API key (metered billing)</option>
              <option value="local">Local model via Ollama (free, lower quality)</option>
            </select>

            {provider === "local" ? (
              <>
                <label style={{ marginTop: "0.75rem" }}>Model</label>
                <select value={model} onChange={(e) => setModel(e.target.value)}>
                  <option value="qwen3-vl:8b">Qwen3-VL 8B — recommended, ~6GB VRAM, free</option>
                  <option value="qwen3-vl:4b">Qwen3-VL 4B — smaller/faster, less accurate</option>
                </select>
                <p className="hint" style={{ marginTop: "0.4rem" }}>
                  Requires Ollama running locally (<code>ollama serve</code>) with the model pulled (
                  <code>ollama pull {model}</code>). No cost, no API key, but noticeably less accurate at
                  recognizing specific characters/plot than Claude - expect more generic descriptions.
                </p>
              </>
            ) : (
              <>
                <label style={{ marginTop: "0.75rem" }}>Model</label>
                <select value={model} onChange={(e) => setModel(e.target.value)}>
                  <option value="claude-haiku-4-5">Haiku 4.5 — cheapest, ~$0.02/scene, occasional misses on short/ambiguous clips</option>
                  <option value="claude-sonnet-5">Sonnet 5 — recommended, ~$0.05/scene, good accuracy</option>
                  <option value="claude-opus-5">Opus 5 — most accurate, ~$0.12/scene</option>
                </select>

                <label style={{ marginTop: "0.75rem" }}>Anthropic API key</label>
                <div style={{ display: "flex", gap: "0.4rem" }}>
                  <input
                    type={showKey ? "text" : "password"}
                    style={{ flex: 1 }}
                    placeholder={settings?.anthropic_api_key_from_env ? "using ANTHROPIC_API_KEY from .env" : "sk-ant-…"}
                    value={apiKey}
                    onChange={(e) => setApiKey(e.target.value)}
                    spellCheck={false}
                  />
                  <button type="button" className="ghost small" onClick={() => setShowKey((v) => !v)}>
                    {showKey ? "hide" : "show"}
                  </button>
                </div>
                <p className="hint" style={{ marginTop: "0.4rem" }}>
                  Only used when provider is set to "Anthropic API key" above. Stored locally in{" "}
                  <code>backend/data/settings.json</code>, never sent anywhere except Anthropic's API. Leave blank to
                  keep using the value from <code>.env</code>.
                </p>
              </>
            )}

            <div style={{ marginTop: "0.75rem", display: "flex", gap: "0.5rem", alignItems: "center" }}>
              <button className="primary" onClick={() => saveMutation.mutate()} disabled={saveMutation.isPending}>
                {saveMutation.isPending ? "Saving…" : "Save"}
              </button>
              {saved && <span className="badge success">saved</span>}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
