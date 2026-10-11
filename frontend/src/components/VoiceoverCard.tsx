import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { EditorProject, LumeanVoice, VoiceFragment, VoiceSettings } from "../api/client";
import VoiceTextTools from "./VoiceTextTools";
import SaveToPreset from "./SaveToPreset";

// ElevenLabs models Lumean offers, newest first
const MODELS: { id: string; label: string }[] = [
  { id: "eleven_v4", label: "v4 - newest" },
  { id: "eleven_v4_exp", label: "v4 experimental" },
  { id: "eleven_v3", label: "v3 - expressive, follows [emotion] tags" },
  { id: "eleven_multilingual_v2", label: "Multilingual v2 - stable" },
  { id: "eleven_turbo_v2_5", label: "Turbo v2.5 - fast" },
  { id: "eleven_flash_v2_5", label: "Flash v2.5 - fastest" },
];

const STATUS_LABEL: Record<string, string> = {
  completed: "voiced",
  pending: "waiting",
  processing: "voicing",
  failed: "failed",
  policy_flagged: "rejected - reword it",
};

function seconds(ms: number | null): string {
  return ms == null ? "" : `${(ms / 1000).toFixed(1)}s`;
}

function VoicePicker({
  language,
  onPick,
  onPickOwn,
}: {
  // only voices that speak this language are listed; empty = any
  language: string;
  onPick: (voice: LumeanVoice) => void;
  // a voice from one of the account's templates comes with that template's settings
  onPickOwn: (voice: VoiceSettings) => void;
}) {
  const [typed, setTyped] = useState("");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(0);
  const { data, isFetching, error } = useQuery({
    queryKey: ["lumeanVoices", search, page, language],
    queryFn: () => api.listLumeanVoices({ search, page, language: language || undefined }),
  });
  const { data: own } = useQuery({ queryKey: ["lumeanOwnVoices"], queryFn: api.listOwnVoices });

  return (
    <div style={{ marginTop: "0.5rem" }}>
      {own && own.length > 0 && (
        <div style={{ marginBottom: "0.6rem" }}>
          <p className="hint" style={{ margin: "0 0 0.3rem" }}>
            From your Lumean templates - the voice comes with the template's model and settings:
          </p>
          <div style={{ display: "flex", gap: "0.4rem", flexWrap: "wrap" }}>
            {own.map((voice) => (
              <button key={`${voice.voice_id}-${voice.voice_name}`} className="small" onClick={() => onPickOwn(voice)} title={`${voice.model_id}, stability ${voice.stability}, speed ${voice.speed}`}>
                {voice.voice_name}
              </button>
            ))}
          </div>
        </div>
      )}
      <form
        style={{ display: "flex", gap: "0.5rem" }}
        onSubmit={(e) => {
          e.preventDefault();
          setPage(0);
          setSearch(typed.trim());
        }}
      >
        <input placeholder="Search ElevenLabs voices: narrator, deep, british…" value={typed} onChange={(e) => setTyped(e.target.value)} />
        <button className="small" type="submit">
          Search
        </button>
      </form>
      {error && <p className="error-text">{(error as Error).message}</p>}
      {isFetching && <p className="hint">Loading voices…</p>}
      {data?.voices.map((voice) => (
        <div key={voice.voice_id} style={{ display: "flex", gap: "0.6rem", alignItems: "center", padding: "0.35rem 0", borderTop: "1px solid var(--border)" }}>
          <button className="small" onClick={() => onPick(voice)}>
            Use
          </button>
          <div style={{ flex: 1, minWidth: 0 }}>
            <strong>{voice.name ?? voice.voice_id}</strong>{" "}
            <span className="hint" style={{ margin: 0 }}>
              {[voice.gender, voice.age, voice.accent, voice.use_case, voice.descriptive].filter(Boolean).join(" · ")}
            </span>
          </div>
          {voice.preview_url && <audio src={voice.preview_url} controls preload="none" style={{ height: "28px", width: "220px" }} />}
        </div>
      ))}
      {data && data.voices.length === 0 && !isFetching && <p className="hint">No voices found.</p>}
      {data && (page > 0 || data.has_more) && (
        <div style={{ display: "flex", gap: "0.5rem", marginTop: "0.4rem" }}>
          <button className="ghost small" disabled={page === 0} onClick={() => setPage((p) => p - 1)}>
            ← Previous
          </button>
          <button className="ghost small" disabled={!data.has_more} onClick={() => setPage((p) => p + 1)}>
            Next →
          </button>
        </div>
      )}
    </div>
  );
}

function FragmentRow({
  project,
  fragment,
  disabled,
  onChange,
}: {
  project: EditorProject;
  fragment: VoiceFragment;
  disabled: boolean;
  onChange: (project: EditorProject) => void;
}) {
  const [text, setText] = useState(fragment.text);
  const redo = useMutation({
    mutationFn: () => api.redoVoiceFragment(project.id, fragment.index, text),
    onSuccess: onChange,
  });
  const broken = fragment.status === "failed" || fragment.status === "policy_flagged";
  const changed = text.trim() !== fragment.text.trim();
  const busy = disabled || redo.isPending;

  return (
    <div style={{ padding: "0.6rem 0", borderTop: "1px solid var(--border)" }}>
      <div style={{ display: "flex", gap: "0.5rem", alignItems: "center", flexWrap: "wrap" }}>
        <strong>{fragment.index + 1}</strong>
        <span className={`badge ${fragment.status === "completed" ? "success" : broken ? "" : "accent"}`}>
          {STATUS_LABEL[fragment.status] ?? fragment.status}
        </span>
        <span className="hint" style={{ margin: 0 }}>
          {seconds(fragment.duration_ms)}
          {fragment.versions > 1 && ` · voiced ${fragment.versions} times`}
        </span>
        {fragment.file && (
          <audio
            key={fragment.file}
            src={api.voiceoverFileUrl(project.id, fragment.file)}
            controls
            preload="none"
            style={{ height: "28px", flex: 1, minWidth: "220px", maxWidth: "420px" }}
          />
        )}
      </div>
      <textarea
        style={{ marginTop: "0.4rem", minHeight: "3.2rem", fontSize: "0.9rem" }}
        value={text}
        disabled={busy}
        onChange={(e) => setText(e.target.value)}
      />
      <div style={{ display: "flex", gap: "0.5rem", alignItems: "center", marginTop: "0.3rem", flexWrap: "wrap" }}>
        {broken ? (
          <button
            className="small"
            disabled={busy || (fragment.status === "policy_flagged" && !changed)}
            onClick={() => redo.mutate()}
            title="Free within the length already paid for"
          >
            {changed ? "Retry with this text" : "Retry"}
          </button>
        ) : (
          <button
            className="small"
            disabled={busy || fragment.status !== "completed" || !text.trim()}
            onClick={() => redo.mutate()}
            title="Voices this fragment again. Paid again, by the length of the text."
          >
            {changed ? "Voice again with this text (paid)" : "Voice again (paid)"}
          </button>
        )}
        {changed && (
          <button className="ghost small" disabled={busy} onClick={() => setText(fragment.text)}>
            Undo edit
          </button>
        )}
        {fragment.status === "policy_flagged" && !changed && (
          <span className="hint" style={{ margin: 0 }}>
            The provider's content policy rejected this text and does not say why. Reword it, then retry.
          </span>
        )}
      </div>
      {redo.isError && <p className="error-text">{(redo.error as Error).message}</p>}
    </div>
  );
}

export default function VoiceoverCard({
  project,
  disabled,
  onChange,
  onOpenSettings,
}: {
  project: EditorProject;
  disabled: boolean;
  onChange: (project: EditorProject) => void;
  onOpenSettings?: () => void;
}) {
  const queryClient = useQueryClient();
  // the voice-over is fetched again whenever the project changes: the stage writes both
  const { data: voiceover, error } = useQuery({
    queryKey: ["editorVoiceover", project.id, project.updated_at],
    queryFn: () => api.getVoiceover(project.id),
    // the key changes with every progress report: keep showing what was there, or the card
    // collapses to "Loading…" for a moment and the page jumps to the top
    placeholderData: keepPreviousData,
  });
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["editorVoiceover", project.id] });

  const [script, setScript] = useState(project.script_text ?? "");
  const [settings, setSettings] = useState<VoiceSettings | null>(null);
  const [picking, setPicking] = useState(false);
  useEffect(() => {
    if (voiceover && settings === null) setSettings(voiceover.state.settings);
  }, [voiceover, settings]);

  const saveScript = useMutation({
    mutationFn: () => api.updateEditorProject(project.id, { script_text: script }),
    onSuccess: onChange,
  });
  const saveSettings = useMutation({
    mutationFn: (next: VoiceSettings) => api.setVoiceSettings(project.id, next),
    onSuccess: refresh,
  });
  const { data: presets } = useQuery({ queryKey: ["editorPresets"], queryFn: api.listPresets });
  const voiceInstructions = (presets?.find((p) => p.id === project.preset) ?? presets?.find((p) => p.id === "default"))?.voice_instructions;
  const estimate = useMutation({ mutationFn: () => api.estimateVoiceover(project.id) });
  const generate = useMutation({ mutationFn: () => api.generateVoiceover(project.id), onSuccess: onChange });
  const sync = useMutation({ mutationFn: () => api.syncVoiceover(project.id), onSuccess: onChange });
  const retryFailed = useMutation({ mutationFn: () => api.retryFailedVoiceover(project.id), onSuccess: onChange });
  const use = useMutation({ mutationFn: () => api.useVoiceover(project.id), onSuccess: onChange });

  if (error) return <p className="error-text">{(error as Error).message}</p>;
  if (!voiceover || !settings) return <p className="hint">Loading…</p>;

  const state = voiceover.state;
  const busy = disabled || generate.isPending || sync.isPending || retryFailed.isPending || use.isPending;
  const scriptChanged = script !== (project.script_text ?? "");
  const change = (patch: Partial<VoiceSettings>) => {
    const next = { ...settings, ...patch };
    setSettings(next);
    saveSettings.mutate(next);
  };
  const v3 = settings.model_id.startsWith("eleven_v3");
  const failed = state.fragments.filter((f) => f.status === "failed").length;
  const flagged = state.fragments.filter((f) => f.status === "policy_flagged").length;
  const inUse = state.final_version !== null && state.used_as_narration === state.final_version;
  const hasWork = project.word_count > 0;
  const actionError = (generate.error ?? sync.error ?? retryFailed.error ?? use.error ?? saveSettings.error ?? saveScript.error) as Error | null;

  return (
    <>
      {!voiceover.has_key && (
        <div className="card">
          <p className="hint" style={{ margin: 0 }}>
            The voice-over is made through Lumean and needs its API key.{" "}
            {onOpenSettings ? (
              <button className="ghost small" onClick={onOpenSettings}>
                Open settings
              </button>
            ) : (
              "Add it in Settings (the gear in the top bar)."
            )}{" "}
            The key needs the rights orders.read, orders.write, orders.download, templates.read, templates.write,
            voices.read and billing.read - the "automation" preset lacks templates.write.
          </p>
        </div>
      )}

      <div className="card" style={{ marginTop: voiceover.has_key ? 0 : "0.75rem" }}>
        <div className="card-header">
          <h3 style={{ margin: 0 }}>Script</h3>
          <span className="hint" style={{ margin: 0 }}>
            {script.trim().length.toLocaleString()} characters
          </span>
        </div>
        <textarea style={{ minHeight: "9rem", fontSize: "0.9rem" }} value={script} disabled={busy} onChange={(e) => setScript(e.target.value)} />
        <p className="hint">
          Read as written. <code>{"{{pause=1.5}}"}</code> adds silence for free; anything in square brackets is a
          direction to the voice and is not spoken - <code>[whispers]</code>, <code>[excited]</code> (v3 follows these
          best) - so don't leave footnotes like [1] in the text.
        </p>
        <VoiceTextTools key={voiceInstructions ?? ""} text={script} disabled={busy} onChange={setScript} instructions={voiceInstructions} />
        {scriptChanged && (
          <button className="small primary" disabled={busy || saveScript.isPending} onClick={() => saveScript.mutate()}>
            Save script
          </button>
        )}
      </div>

      <div className="card" style={{ marginTop: "0.75rem" }}>
        <h3>Voice</h3>
        <div style={{ display: "flex", gap: "0.6rem", alignItems: "center", flexWrap: "wrap" }}>
          <strong>{settings.voice_name || (settings.voice_id ? settings.voice_id : "No voice chosen")}</strong>
          <button className={settings.voice_id ? "small" : "small primary"} disabled={busy || !voiceover.has_key} onClick={() => setPicking((v) => !v)}>
            {picking ? "Close" : settings.voice_id ? "Change voice" : "Choose a voice"}
          </button>
        </div>
        {picking && (
          <VoicePicker
            language={settings.language_code.trim()}
            onPick={(voice) => {
              change({ voice_id: voice.voice_id, voice_name: voice.name ?? voice.voice_id, public_owner_id: voice.public_owner_id ?? "" });
              setPicking(false);
            }}
            onPickOwn={(voice) => {
              change({ ...voice, by_paragraph: settings.by_paragraph });
              setPicking(false);
            }}
          />
        )}

        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(170px, 1fr))", gap: "0.6rem", marginTop: "0.75rem" }}>
          <div>
            <label>Model</label>
            <select value={settings.model_id} disabled={busy} onChange={(e) => change({ model_id: e.target.value })}>
              {!MODELS.some((m) => m.id === settings.model_id) && <option value={settings.model_id}>{settings.model_id}</option>}
              {MODELS.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.label}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label title="Two-letter code of the language the script is in. Empty lets the model work it out, which is unreliable on short fragments.">
              Language
            </label>
            <input
              value={settings.language_code}
              disabled={busy}
              maxLength={3}
              onChange={(e) => setSettings({ ...settings, language_code: e.target.value })}
              onBlur={() => saveSettings.mutate(settings)}
            />
          </div>
          <div>
            <label title="Lower is more expressive and less predictable; higher is steadier and flatter.">
              Stability {settings.stability.toFixed(2)}
            </label>
            {v3 ? (
              <select value={settings.stability} disabled={busy} onChange={(e) => change({ stability: Number(e.target.value) })}>
                {![0, 0.5, 1].includes(settings.stability) && <option value={settings.stability}>{settings.stability}</option>}
                <option value={0}>0 - creative</option>
                <option value={0.5}>0.5 - natural</option>
                <option value={1}>1 - robust</option>
              </select>
            ) : (
              <input
                type="range" min={0} max={1} step={0.05} value={settings.stability} disabled={busy}
                onChange={(e) => setSettings({ ...settings, stability: Number(e.target.value) })}
                onMouseUp={() => saveSettings.mutate(settings)}
                onKeyUp={() => saveSettings.mutate(settings)}
              />
            )}
          </div>
          <div>
            <label title="How closely to keep to the original voice.">Similarity {settings.similarity_boost.toFixed(2)}</label>
            <input
              type="range" min={0} max={1} step={0.05} value={settings.similarity_boost} disabled={busy}
              onChange={(e) => setSettings({ ...settings, similarity_boost: Number(e.target.value) })}
              onMouseUp={() => saveSettings.mutate(settings)}
              onKeyUp={() => saveSettings.mutate(settings)}
            />
          </div>
          <div>
            <label title="Exaggerates the voice's own style. 0 is the safe choice.">Style {settings.style.toFixed(2)}</label>
            <input
              type="range" min={0} max={1} step={0.05} value={settings.style} disabled={busy}
              onChange={(e) => setSettings({ ...settings, style: Number(e.target.value) })}
              onMouseUp={() => saveSettings.mutate(settings)}
              onKeyUp={() => saveSettings.mutate(settings)}
            />
          </div>
          <div>
            <label>Speed {settings.speed.toFixed(2)}</label>
            <input
              type="range" min={0.7} max={1.2} step={0.01} value={settings.speed} disabled={busy}
              onChange={(e) => setSettings({ ...settings, speed: Number(e.target.value) })}
              onMouseUp={() => saveSettings.mutate(settings)}
              onKeyUp={() => saveSettings.mutate(settings)}
            />
          </div>
        </div>
        <label className="hint" style={{ display: "flex", alignItems: "center", gap: "0.4rem", marginTop: "0.6rem", cursor: "pointer" }}>
          <input type="checkbox" style={{ width: "auto" }} checked={settings.by_paragraph} disabled={busy} onChange={(e) => change({ by_paragraph: e.target.checked })} />
          <span title="Cuts the script at empty lines first, so a fragment never spans two paragraphs. Each paragraph is then billed at least the service's minimum, so many short paragraphs cost more.">
            One fragment per paragraph
          </span>
        </label>

        <div style={{ display: "flex", gap: "0.5rem", alignItems: "center", marginTop: "0.75rem", flexWrap: "wrap" }}>
          <button className="small" disabled={busy || scriptChanged || !voiceover.has_key || !settings.voice_id || estimate.isPending} onClick={() => estimate.mutate()}>
            {estimate.isPending ? "Estimating…" : "Estimate cost"}
          </button>
          <button
            className="small primary"
            disabled={busy || scriptChanged || !voiceover.has_key || !settings.voice_id || !script.trim()}
            onClick={() => generate.mutate()}
            title="Sends the whole script to be voiced. Paid from the Lumean subscription."
          >
            {state.order_id ? "Voice the whole script again (paid)" : "Voice the script (paid)"}
          </button>
          {scriptChanged && <span className="hint" style={{ margin: 0 }}>Save the script first.</span>}
          {!scriptChanged && voiceover.has_key && !settings.voice_id && (
            <span className="hint" style={{ margin: 0 }}>Choose a voice above first - nothing is voiced until you press the button.</span>
          )}
          {!scriptChanged && !voiceover.has_key && <span className="hint" style={{ margin: 0 }}>Add the Lumean API key first.</span>}
        </div>
        {estimate.isError && <p className="error-text">{(estimate.error as Error).message}</p>}
        {estimate.data && (
          <p className="hint">
            {estimate.data.characters.toLocaleString()} characters
            {estimate.data.fragments != null && ` in ${estimate.data.fragments} fragments`}
            {Object.entries(estimate.data.summary)
              .filter(([, value]) => typeof value === "number" || typeof value === "string")
              .map(([key, value]) => ` · ${key.replace(/_/g, " ")}: ${value}`)
              .join("")}
            {estimate.data.tokens_remaining != null && ` · ${estimate.data.tokens_remaining.toLocaleString()} tokens left in the subscription`}
            {estimate.data.blocked_fragments.length > 0 &&
              ` · fragments ${estimate.data.blocked_fragments.join(", ")} were rejected by the content policy before - reword them first`}
          </p>
        )}
        <SaveToPreset project={project} what="voice" save={api.saveVoiceToPreset} disabled={busy || !settings.voice_id || saveSettings.isPending} onChange={onChange} />
      </div>

      {state.order_id && (
        <div className="card" style={{ marginTop: "0.75rem" }}>
          <div className="card-header">
            <h3 style={{ margin: 0 }}>Voice-over</h3>
            <span className="hint" style={{ margin: 0 }}>
              {state.fragments.length} fragments
              {state.tokens_spent != null && ` · ${state.tokens_spent.toLocaleString()} tokens spent`}
              {state.order_status && ` · order ${state.order_status.replace(/_/g, " ")}`}
            </span>
          </div>
          {voiceover.script_changed && (
            <p className="hint">The script has changed since it was voiced - the audio below is of the earlier text.</p>
          )}
          {state.final_file ? (
            <>
              <audio key={state.final_file} src={api.voiceoverFileUrl(project.id, state.final_file)} controls preload="metadata" style={{ width: "100%" }} />
              <div style={{ display: "flex", gap: "0.5rem", alignItems: "center", marginTop: "0.5rem", flexWrap: "wrap" }}>
                {inUse ? (
                  <span className="badge success">this is the project's narration</span>
                ) : (
                  <button className="small primary" disabled={busy} onClick={() => use.mutate()}>
                    Use as the narration
                  </button>
                )}
                {!inUse && hasWork && (
                  <span className="hint" style={{ margin: 0 }}>
                    Replaces the current narration: the transcript, beats, clips and quotes built on it are discarded.
                  </span>
                )}
              </div>
            </>
          ) : (
            <p className="hint">The joined audio appears here once every fragment is voiced.</p>
          )}
          <div style={{ display: "flex", gap: "0.5rem", alignItems: "center", margin: "0.6rem 0 0.2rem", flexWrap: "wrap" }}>
            <button className="ghost small" disabled={busy} onClick={() => sync.mutate()} title="Checks the order again and fetches what changed. Free.">
              Refresh from Lumean
            </button>
            {failed > 0 && (
              <button className="small" disabled={busy} onClick={() => retryFailed.mutate()}>
                Retry {failed} failed
              </button>
            )}
            {flagged > 0 && (
              <span className="hint" style={{ margin: 0 }}>
                {flagged} rejected by the content policy - reword each below.
              </span>
            )}
          </div>
          {state.fragments.map((fragment) => (
            <FragmentRow key={`${fragment.root_id}-${fragment.item_id}`} project={project} fragment={fragment} disabled={busy} onChange={onChange} />
          ))}
        </div>
      )}
      {actionError && <p className="error-text">{actionError.message}</p>}
    </>
  );
}
