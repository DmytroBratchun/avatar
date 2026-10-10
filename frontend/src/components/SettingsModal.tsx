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
  const [lumeanKey, setLumeanKey] = useState("");
  const [showLumeanKey, setShowLumeanKey] = useState(false);
  const [openaiKey, setOpenaiKey] = useState("");
  const [showOpenaiKey, setShowOpenaiKey] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (settings) {
      setProvider(settings.caption_provider as "claude_code" | "api" | "local");
      setModel(settings.caption_model);
      setApiKey(settings.anthropic_api_key_from_env ? "" : settings.anthropic_api_key);
      setLumeanKey(settings.lumean_api_key);
      setOpenaiKey(settings.openai_api_key);
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
    mutationFn: () =>
      api.updateSettings({ caption_provider: provider, caption_model: model, anthropic_api_key: apiKey, lumean_api_key: lumeanKey.trim(), openai_api_key: openaiKey.trim() }),
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
                    placeholder={settings?.anthropic_api_key_from_env ? "used from .env: ANTHROPIC_API_KEY" : "sk-ant-…"}
                    value={apiKey}
                    onChange={(e) => setApiKey(e.target.value)}
                    spellCheck={false}
                  />
                  <button type="button" className="ghost small" onClick={() => setShowKey((v) => !v)}>
                    {showKey ? "hide" : "show"}
                  </button>
                </div>
                <p className="hint" style={{ marginTop: "0.4rem" }}>
                  Only for the "Anthropic API key" provider. Blank: from <code>.env</code>.
                </p>
              </>
            )}

            <label style={{ marginTop: "0.75rem" }}>Lumean API key</label>
            <div style={{ display: "flex", gap: "0.4rem" }}>
              <input
                type={showLumeanKey ? "text" : "password"}
                style={{ flex: 1 }}
                placeholder={settings?.lumean_api_key_from_env ? "used from environment: LUMEAN_KEY" : "…"}
                value={lumeanKey}
                onChange={(e) => setLumeanKey(e.target.value)}
                spellCheck={false}
              />
              <button type="button" className="ghost small" onClick={() => setShowLumeanKey((v) => !v)}>
                {showLumeanKey ? "hide" : "show"}
              </button>
            </div>
            <p className="hint" style={{ marginTop: "0.4rem" }}>
              For voice-over and music. Needs orders, templates, voices and billing rights. Blank: <code>LUMEAN_KEY</code>.
            </p>

            <label style={{ marginTop: "0.75rem" }}>OpenAI API key</label>
            <div style={{ display: "flex", gap: "0.4rem" }}>
              <input
                type={showOpenaiKey ? "text" : "password"}
                style={{ flex: 1 }}
                placeholder={settings?.openai_api_key_from_env ? "used from environment: OPENAI_API_KEY" : "sk-…"}
                value={openaiKey}
                onChange={(e) => setOpenaiKey(e.target.value)}
                spellCheck={false}
              />
              <button type="button" className="ghost small" onClick={() => setShowOpenaiKey((v) => !v)}>
                {showOpenaiKey ? "hide" : "show"}
              </button>
            </div>
            <p className="hint" style={{ marginTop: "0.4rem" }}>
              Not used yet (planned: characters for the editor). Blank: <code>OPENAI_API_KEY</code>.
            </p>

            <p className="hint" style={{ margin: "0.75rem 0 0" }}>
              Keys are kept in <code>backend/data/settings.json</code>.
            </p>

            <label style={{ marginTop: "0.75rem" }}>Usage dashboards</label>
            <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap" }}>
              <button type="button" className="ghost small" onClick={() => window.open("https://claude.ai/settings/usage", "_blank", "noreferrer")}>
                Claude subscription ↗
              </button>
              <button type="button" className="ghost small" onClick={() => window.open("https://console.anthropic.com/settings/usage", "_blank", "noreferrer")}>
                Anthropic API ↗
              </button>
              <button type="button" className="ghost small" onClick={() => window.open("https://platform.openai.com/usage", "_blank", "noreferrer")}>
                OpenAI ↗
              </button>
            </div>

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
