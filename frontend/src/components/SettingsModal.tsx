import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api } from "../api/client";
import PresetsPane from "./PresetsPane";

const MODELS = [
  { value: "sonnet", label: "Sonnet — recommended" },
  { value: "opus", label: "Opus — ~2.5x the quota" },
  { value: "haiku", label: "Haiku — cheapest" },
  { value: "auto", label: "Auto — Claude Code picks" },
];

/** The video editor's Claude calls, in the order a project meets them. */
const TASKS = [
  { value: "voice_text", label: "Optimize for voice", description: "Adds voice directions and pauses to a script" },
  { value: "beats", label: "Beats", description: "Splits the narration into beats" },
  { value: "quotes", label: "Quotes", description: "Picks lines from the episodes, finds them and reviews each in place" },
  { value: "queries", label: "Shot queries", description: "Writes the search queries for every shot, and new ones where nothing was found" },
  { value: "moments", label: "Moments", description: "An agent that searches the episodes for a particular event" },
  { value: "comic_pages", label: "Comic pages", description: "Reads a comic and picks the page for each shot" },
  { value: "photos", label: "Photos", description: "Looks at downloaded photos and keeps the ones that fit" },
  { value: "background", label: "Background", description: "Describes the scenery behind each beat" },
  { value: "music", label: "Music plan", description: "Divides the video into moods and writes the music prompts" },
];

function shortLabel(value: string) {
  return MODELS.find((m) => m.value === value)?.label.split(" — ")[0] ?? value;
}

/** One task's model: "" = the default model; null = not Claude (shown disabled). */
function TaskModel({ label, description, value, defaultModel, onChange }: {
  label: string;
  description: string;
  value: string | null;
  defaultModel: string;
  onChange: (value: string) => void;
}) {
  return (
    <div className="settings-task">
      <span title={description}>{label}</span>
      <select value={value ?? ""} disabled={value === null} onChange={(e) => onChange(e.target.value)}>
        {value === null ? (
          <option value="">Ollama (below)</option>
        ) : (
          <>
            <option value="">Default ({shortLabel(defaultModel)})</option>
            {MODELS.map((m) => (
              <option key={m.value} value={m.value}>{shortLabel(m.value)}</option>
            ))}
            {value && !MODELS.some((m) => m.value === value) && <option value={value}>{value}</option>}
          </>
        )}
      </select>
    </div>
  );
}

const SECTIONS = [
  { id: "models", label: "Models" },
  { id: "captioning", label: "Captioning" },
  { id: "presets", label: "Presets" },
  { id: "keys", label: "API keys" },
  { id: "usage", label: "Usage" },
] as const;
type Section = (typeof SECTIONS)[number]["id"];
const SECTION_KEY = "settings-section";

export default function SettingsModal({
  onClose,
  section: openOn,
  projectId,
}: {
  onClose: () => void;
  section?: string; // the section to open on; without it, the one open last time
  projectId?: string; // the video whose page it was opened on: Presets shows its preset
}) {
  const queryClient = useQueryClient();
  const { data: settings, isLoading } = useQuery({ queryKey: ["settings"], queryFn: api.getSettings });

  const [provider, setProvider] = useState<"claude_code" | "api" | "local">("claude_code");
  const [captionModel, setCaptionModel] = useState("");
  const [localModel, setLocalModel] = useState("qwen3-vl:8b");
  const [apiKey, setApiKey] = useState("");
  const [showKey, setShowKey] = useState(false);
  const [lumeanKey, setLumeanKey] = useState("");
  const [showLumeanKey, setShowLumeanKey] = useState(false);
  const [openaiKey, setOpenaiKey] = useState("");
  const [showOpenaiKey, setShowOpenaiKey] = useState(false);
  const [defaultModel, setDefaultModel] = useState("sonnet");
  const [taskModels, setTaskModels] = useState<Record<string, string>>({});
  const [section, setSection] = useState<Section>(() => {
    if (SECTIONS.some((s) => s.id === openOn)) return openOn as Section;
    try {
      const stored = localStorage.getItem(SECTION_KEY);
      return SECTIONS.some((s) => s.id === stored) ? (stored as Section) : "models";
    } catch {
      return "models";
    }
  });
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (settings) {
      setProvider(settings.caption_provider as "claude_code" | "api" | "local");
      const caption = settings.caption_model_override;
      setCaptionModel(caption.startsWith("qwen") ? "" : caption);
      if (caption.startsWith("qwen")) setLocalModel(caption);
      setApiKey(settings.anthropic_api_key_from_env ? "" : settings.anthropic_api_key);
      setLumeanKey(settings.lumean_api_key);
      setOpenaiKey(settings.openai_api_key);
      setDefaultModel(settings.editor_llm_model);
      setTaskModels(settings.editor_llm_models);
    }
  }, [settings]);

  function openSection(next: Section) {
    setSection(next);
    try {
      localStorage.setItem(SECTION_KEY, next);
    } catch {
      // the tab is just not remembered
    }
  }

  const changed = Object.keys(taskModels).length + (provider !== "local" && captionModel ? 1 : 0);

  const saveMutation = useMutation({
    mutationFn: () =>
      api.updateSettings({ caption_provider: provider, caption_model: provider === "local" ? localModel : captionModel, anthropic_api_key: apiKey, lumean_api_key: lumeanKey.trim(), openai_api_key: openaiKey.trim(), editor_llm_model: defaultModel, editor_llm_models: taskModels }),
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
      <div className="card settings-modal" onClick={(e) => e.stopPropagation()}>
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
            <div className="settings-body">
              <nav className="settings-nav">
                {SECTIONS.map((s) => (
                  <button key={s.id} type="button" className={s.id === section ? "active" : ""} onClick={() => openSection(s.id)}>
                    {s.label}
                  </button>
                ))}
              </nav>
              <div className="settings-pane">
                {section === "models" && (
                  <>
                  <label>Default model</label>
                  <select value={defaultModel} onChange={(e) => setDefaultModel(e.target.value)}>
                    {MODELS.map((m) => (
                      <option key={m.value} value={m.value}>{m.label}</option>
                    ))}
                    {!MODELS.some((m) => m.value === defaultModel) && <option value={defaultModel}>{defaultModel}</option>}
                  </select>
                  <label style={{ marginTop: "1rem" }}>Per-task models{changed > 0 && ` · ${changed} changed`}</label>
                  <div className="settings-tasks">
                    <TaskModel
                      label="Captions"
                      description="Describes every scene and comic page for the index, and writes episode summaries"
                      value={provider === "local" ? null : captionModel}
                      defaultModel={defaultModel}
                      onChange={setCaptionModel}
                    />
                    {TASKS.map((task) => (
                      <TaskModel
                        key={task.value}
                        label={task.label}
                        description={task.description}
                        value={taskModels[task.value] ?? ""}
                        defaultModel={defaultModel}
                        onChange={(value) => {
                          const next = { ...taskModels };
                          if (value) next[task.value] = value;
                          else delete next[task.value];
                          setTaskModels(next);
                        }}
                      />
                    ))}
                    <p className="hint" style={{ margin: 0 }}>
                      Quotes start on Opus: Sonnet misremembers which line is spoken where.
                    </p>
                  </div>
                  </>
                )}
                {section === "captioning" && (
                  <>
                  <label>Provider</label>
                  <select value={provider} onChange={(e) => setProvider(e.target.value as typeof provider)}>
                    <option value="claude_code">Claude Code CLI (your Claude subscription)</option>
                    <option value="api">Anthropic API key (metered billing)</option>
                    <option value="local">Local model via Ollama (free, lower quality)</option>
                  </select>
                  {provider === "local" && (
                    <>
                      <label style={{ marginTop: "0.75rem" }}>Ollama model</label>
                      <select value={localModel} onChange={(e) => setLocalModel(e.target.value)}>
                        <option value="qwen3-vl:8b">Qwen3-VL 8B — recommended, ~6GB VRAM</option>
                        <option value="qwen3-vl:4b">Qwen3-VL 4B — smaller/faster, less accurate</option>
                      </select>
                      <p className="hint" style={{ marginTop: "0.4rem" }}>
                        Needs <code>ollama serve</code> and <code>ollama pull {localModel}</code>. Free, but less accurate at
                        characters and plot.
                      </p>
                    </>
                  )}
                  </>
                )}
                {section === "keys" && (
                  <>
                  <label>Anthropic API key</label>
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
                    For the "Anthropic API key" captioning provider. Blank: from <code>.env</code>.
                  </p>

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
                    For pictures of places on the map (GPT Image, paid). Blank: <code>OPENAI_API_KEY</code>.
                  </p>

                  <p className="hint" style={{ margin: "0.75rem 0 0" }}>
                    Keys are kept in <code>index/settings.json</code>.
                  </p>
                  </>
                )}
                {section === "presets" && <PresetsPane projectId={projectId} />}
                {section === "usage" && (
                  <>
                    <p className="hint" style={{ marginTop: 0 }}>Where the money and quota go.</p>
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
                  </>
                )}
              </div>
            </div>

            <div className="settings-footer" style={section === "presets" ? { display: "none" } : undefined}>
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
