import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api/client";
import type { EditorProject, LookFormat } from "../api/client";

// same order as the backend: top-left, top-right, bottom-right, bottom-left
const CORNER_NAMES = ["Top left", "Top right", "Bottom right", "Bottom left"];

type Patch = Parameters<typeof api.updateEditorProject>[1];

/** A slider that edits a local value while dragging and saves on release. */
function Slider({
  label,
  hint,
  value,
  min,
  max,
  step,
  disabled,
  onChange,
  onCommit,
}: {
  label: string;
  hint?: string;
  value: number;
  min: number;
  max: number;
  step: number;
  disabled: boolean;
  onChange: (value: number) => void;
  onCommit: () => void;
}) {
  return (
    <div>
      <label>{label}</label>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(Number(e.target.value))}
        onPointerUp={onCommit}
        onKeyUp={onCommit}
      />
      {hint && (
        <p className="hint" style={{ marginTop: "0.2rem", marginBottom: 0 }}>
          {hint}
        </p>
      )}
    </div>
  );
}

/** One group of settings; touching anything in it switches the preview to its format. */
function Section({
  title,
  subtitle,
  format,
  active,
  onFocus,
  children,
}: {
  title: string;
  subtitle: string;
  format?: LookFormat;
  active?: boolean;
  onFocus?: (format: LookFormat) => void;
  children: React.ReactNode;
}) {
  return (
    <div
      className="look-section"
      style={{ borderLeftColor: active ? "var(--accent)" : "var(--border)" }}
      onPointerDown={() => format && onFocus?.(format)}
      onFocusCapture={() => format && onFocus?.(format)}
    >
      <div style={{ display: "flex", alignItems: "baseline", gap: "0.5rem", flexWrap: "wrap" }}>
        <h4 style={{ margin: 0 }}>{title}</h4>
        <span className="hint" style={{ margin: 0 }}>
          {subtitle}
        </span>
      </div>
      <div style={{ marginTop: "0.5rem" }}>{children}</div>
    </div>
  );
}

export default function RenderSettingsCard({
  project,
  disabled,
  format,
  onFormat,
  onChange,
}: {
  project: EditorProject;
  disabled: boolean;
  // the format the preview is showing, and a way to switch it
  format: LookFormat;
  onFormat: (format: LookFormat) => void;
  onChange: (project: EditorProject) => void;
}) {
  const queryClient = useQueryClient();
  const { data: frames } = useQuery({ queryKey: ["editorFrames"], queryFn: api.listEditorFrames });
  const settings = project.settings;
  // sliders edit a local copy and save on release, not on every pixel of drag
  const [blur, setBlur] = useState(settings.blur);
  const [dim, setDim] = useState(settings.dim);
  const [radius, setRadius] = useState(settings.corner_radius);
  const [scale, setScale] = useState(settings.clip_scale);
  const [frameScale, setFrameScale] = useState(settings.frame_scale);
  const [radii, setRadii] = useState<number[] | null>(settings.corner_radii);
  const [speed, setSpeed] = useState(settings.background_speed);
  const [wideFill, setWideFill] = useState(settings.wide_fill);

  const saveMutation = useMutation({
    mutationFn: (payload: Patch) => api.updateEditorProject(project.id, payload),
    onSuccess: onChange,
  });
  const uploadMutation = useMutation({
    mutationFn: (file: File) => api.uploadEditorFrame(file),
    onSuccess: (names) => queryClient.setQueryData(["editorFrames"], names),
  });
  const defaultMutation = useMutation({
    mutationFn: () => api.saveEditorDefaultLook(project.id),
    onSuccess: (look) => queryClient.setQueryData(["editorDefaultLook"], look),
  });

  const busy = disabled || saveMutation.isPending;
  const save = (payload: Patch) => saveMutation.mutate(payload);
  const framed = settings.layout === "framed";
  const error = (saveMutation.error ?? uploadMutation.error ?? defaultMutation.error) as Error | null;

  return (
    <div className="card">
      <h3>Look</h3>

      <Section title="Whole video" subtitle="applies to every clip">
        <label>Layout</label>
        <select value={settings.layout} disabled={busy} onChange={(e) => save({ layout: e.target.value })}>
          <option value="framed">Framed - old 4:3 footage sits in a frame over a blurred background</option>
          <option value="simple">Simple - every clip fills the screen as far as it can</option>
        </select>
        <div className="field-row" style={{ marginTop: "0.5rem" }}>
          <Slider label={`Background blur: ${Math.round(blur)}`} value={blur} min={0} max={100} step={1} disabled={busy} onChange={setBlur} onCommit={() => save({ blur })} />
          <Slider
            label={`Background darkening: ${Math.round(dim * 100)}%`}
            value={dim}
            min={0}
            max={0.9}
            step={0.05}
            disabled={busy}
            onChange={setDim}
            onCommit={() => save({ dim })}
          />
        </div>
        <div className="field-row" style={{ marginTop: "0.5rem" }}>
          <Slider
            label={`Background speed: ${Math.round(speed * 100)}%`}
            hint="Slows the scenery in the background. Each shot lasts longer, so it changes less often."
            value={speed}
            min={0.25}
            max={1}
            step={0.05}
            disabled={busy}
            onChange={setSpeed}
            onCommit={() => save({ background_speed: speed })}
          />
          <div />
        </div>
      </Section>

      {framed && (
        <Section title="4:3 footage" subtitle="Avatar: The Last Airbender - shown in a frame" format="4:3" active={format === "4:3"} onFocus={onFormat}>
          <label>Frame</label>
          <select value={settings.frame} disabled={busy} onChange={(e) => save({ frame: e.target.value })}>
            <option value="border">Plain border</option>
            <option value="none">No frame</option>
            {(frames ?? []).map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
          <div className="field-row" style={{ marginTop: "0.5rem" }}>
            <Slider
              label={`Frame size: ${Math.round(frameScale * 100)}%`}
              hint="The whole frame with the clip inside it. 100% is as tall as the screen."
              value={frameScale}
              min={0.5}
              max={1.3}
              step={0.01}
              disabled={busy}
              onChange={setFrameScale}
              onCommit={() => save({ frame_scale: frameScale })}
            />
            <Slider
              label={`Clip size: ${Math.round(scale * 100)}%`}
              hint="The clip inside the frame, about its centre, to fit it to the artwork."
              value={scale}
              min={0.7}
              max={1.3}
              step={0.01}
              disabled={busy}
              onChange={setScale}
              onCommit={() => save({ clip_scale: scale })}
            />
          </div>

          <div style={{ marginTop: "0.5rem" }}>
            <label>Corner rounding: {radii ? "per corner" : radius === 0 ? "off" : `${radius}px`}</label>
            {!radii && (
              <input
                type="range"
                min={0}
                max={160}
                step={4}
                value={radius}
                disabled={busy}
                onChange={(e) => setRadius(Number(e.target.value))}
                onPointerUp={() => save({ corner_radius: radius })}
                onKeyUp={() => save({ corner_radius: radius })}
              />
            )}
            <label style={{ display: "flex", alignItems: "center", gap: "0.4rem", marginTop: "0.3rem" }}>
              <input
                type="checkbox"
                style={{ width: "auto" }}
                checked={radii !== null}
                disabled={busy}
                onChange={(e) => {
                  // start the four corners from the single value, so nothing jumps
                  const next = e.target.checked ? [radius, radius, radius, radius] : null;
                  setRadii(next);
                  save({ corner_radii: next });
                }}
              />
              Set each corner separately
            </label>
            {radii && (
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "0.3rem 0.75rem", marginTop: "0.3rem" }}>
                {/* laid out like the corners themselves: top row, then bottom row */}
                {([0, 1, 3, 2] as const).map((corner) => (
                  <Slider
                    key={corner}
                    label={`${CORNER_NAMES[corner]}: ${radii[corner]}px`}
                    value={radii[corner]}
                    min={0}
                    max={160}
                    step={4}
                    disabled={busy}
                    onChange={(value) => setRadii(radii.map((r, i) => (i === corner ? value : r)))}
                    onCommit={() => save({ corner_radii: radii })}
                  />
                ))}
              </div>
            )}
            <p className="hint" style={{ marginTop: "0.2rem", marginBottom: 0 }}>
              Rounds the clip so its corners don't stick out from under the frame.
            </p>
          </div>

          <label style={{ marginTop: "0.6rem", fontSize: "0.8rem" }}>Add a frame image</label>
          <input
            type="file"
            accept=".png,image/png"
            disabled={uploadMutation.isPending}
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) uploadMutation.mutate(file);
            }}
          />
          <p className="hint" style={{ marginTop: "0.3rem", marginBottom: 0 }}>
            A PNG with a transparent opening in the middle. The clip is placed under it, sized to the opening.
          </p>
        </Section>
      )}

      {framed && (
        <Section title="16:9 footage" subtitle="The Legend of Korra - already the shape of the screen" format="16:9" active={format === "16:9"} onFocus={onFormat}>
          <label style={{ display: "flex", alignItems: "center", gap: "0.4rem", margin: 0 }}>
            <input
              type="checkbox"
              style={{ width: "auto" }}
              checked={settings.wide_full_screen}
              disabled={busy}
              onChange={(e) => save({ wide_full_screen: e.target.checked })}
            />
            Fill the screen, without the frame
          </label>
          <p className="hint" style={{ marginTop: "0.2rem", marginBottom: 0 }}>
            Off: these clips are cropped into the frame like 4:3 footage. This also decides whether wider footage
            (below) gets the frame.
          </p>
        </Section>
      )}

      <Section title="Wider than the screen" subtitle="cinema format, 2.39:1 - the 2026 film" format="wide" active={format === "wide"} onFocus={onFormat}>
        <Slider
          label={`Zoom: ${Math.round(wideFill * 100)}%`}
          hint="0%: the whole picture, with the blurred background above and below. 100%: zoomed until it fills the screen, a quarter of the picture lost at the sides."
          value={wideFill}
          min={0}
          max={1}
          step={0.05}
          disabled={busy}
          onChange={setWideFill}
          onCommit={() => save({ wide_fill: wideFill })}
        />
      </Section>

      <p className="hint" style={{ marginTop: "0.75rem", marginBottom: 0 }}>
        Changing the look does not re-select footage - render again to apply it.
      </p>
      <div style={{ marginTop: "0.6rem", display: "flex", gap: "0.5rem", alignItems: "center" }}>
        <button className="small" onClick={() => defaultMutation.mutate()} disabled={busy || defaultMutation.isPending}>
          Save as default
        </button>
        <span className="hint" style={{ margin: 0 }}>
          {defaultMutation.isSuccess ? "Saved - new videos will start with this look." : "New videos start with the saved look. Existing ones are not changed."}
        </span>
      </div>
      {error && <p className="error-text">{error.message}</p>}
    </div>
  );
}
