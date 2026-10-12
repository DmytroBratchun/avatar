import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api/client";
import type { EditorProject, LookFormat } from "../api/client";
import Help from "./Help";
import SaveToPreset from "./SaveToPreset";

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
      <label>
        {label}
        {hint && <Help text={hint} />}
      </label>
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
    </div>
  );
}

/** One group of settings, folded until its title is clicked; opening it or touching
anything in it switches the preview to its format. */
function Section({
  title,
  subtitle,
  format,
  active,
  onFocus,
  defaultOpen = false,
  children,
}: {
  title: string;
  subtitle: string;
  format?: LookFormat;
  active?: boolean;
  onFocus?: (format: LookFormat) => void;
  defaultOpen?: boolean;
  children: React.ReactNode;
}) {
  return (
    <details
      className="look-section"
      open={defaultOpen}
      style={{ borderLeftColor: active ? "var(--accent)" : "var(--border)" }}
      onToggle={(e) => (e.currentTarget as HTMLDetailsElement).open && format && onFocus?.(format)}
      onPointerDown={() => format && onFocus?.(format)}
      onFocusCapture={() => format && onFocus?.(format)}
    >
      <summary style={{ cursor: "pointer", display: "flex", alignItems: "baseline", gap: "0.5rem", flexWrap: "wrap" }}>
        <h4 style={{ margin: 0, display: "inline" }}>{title}</h4>
        <span className="hint" style={{ margin: 0 }}>
          {subtitle}
        </span>
      </summary>
      <div style={{ marginTop: "0.5rem" }}>{children}</div>
    </details>
  );
}

export default function RenderSettingsCard({
  project,
  disabled,
  format,
  onFormat,
  onChange,
  hasMap = false,
}: {
  project: EditorProject;
  disabled: boolean;
  // the format the preview is showing, and a way to switch it
  format: LookFormat;
  onFormat: (format: LookFormat) => void;
  onChange: (project: EditorProject) => void;
  // the project's world has a map, so map shots can be made
  hasMap?: boolean;
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
  const [tilt, setTilt] = useState(settings.map_tilt);

  const saveMutation = useMutation({
    mutationFn: (payload: Patch) => api.updateEditorProject(project.id, payload),
    onSuccess: onChange,
  });
  const uploadMutation = useMutation({
    mutationFn: (file: File) => api.uploadEditorFrame(file),
    onSuccess: (names) => queryClient.setQueryData(["editorFrames"], names),
  });

  const busy = disabled || saveMutation.isPending;
  const save = (payload: Patch) => saveMutation.mutate(payload);
  const framed = settings.layout === "framed";
  const error = (saveMutation.error ?? uploadMutation.error) as Error | null;

  return (
    <div className="card">
      <h3>
        Look
        <Help text="Changing the look does not re-select footage - render again to apply it." />
      </h3>

      <Section title="Whole video" subtitle="applies to every clip" defaultOpen>
        <label>Layout</label>
        <select value={settings.layout} disabled={busy} onChange={(e) => save({ layout: e.target.value })}>
          <option value="framed">Framed - old 4:3 footage sits in a frame over a blurred background</option>
          <option value="simple">Simple - every clip fills the screen as far as it can</option>
        </select>
        <label style={{ marginTop: "0.5rem" }}>Frame rate</label>
        <select style={{ width: "auto" }} value={settings.frame_rate} disabled={busy} onChange={(e) => save({ frame_rate: Number(e.target.value) })}>
          <option value={24}>24 fps - the footage's own rate</option>
          <option value={48}>48 fps - smoother comic scrolling and photo pans; footage looks the same</option>
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
        {framed && project.features.background && (
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
        )}
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
            <label>
              Corner rounding: {radii ? "per corner" : radius === 0 ? "off" : `${radius}px`}
              <Help text="Rounds the clip so its corners don't stick out from under the frame." />
            </label>
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
          </div>

          <label style={{ marginTop: "0.6rem", fontSize: "0.8rem" }}>
            Add a frame image
            <Help text="A PNG with a transparent opening in the middle. The clip is placed under it, sized to the opening." />
          </label>
          <input
            type="file"
            accept=".png,image/png"
            disabled={uploadMutation.isPending}
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) uploadMutation.mutate(file);
            }}
          />
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
            <Help text="Off: these clips are cropped into the frame like 4:3 footage. This also decides whether wider footage (below) gets the frame." />
          </label>
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

      {project.features.comics && (
      <Section
        title="Comic pages"
        subtitle="a page scrolling down or up the screen, in place of a clip"
        format="comic"
        active={format === "comic"}
        onFocus={onFormat}
      >
        <label>
          Page width
          <Help text="How wide a comic page is shown, as a share of the screen. Wider: more of the page to scroll through, but a softer picture - the scans are small. At 100% the page covers the screen, so no background is drawn behind it and the clip renders faster." />
        </label>
        <select
          style={{ width: "auto" }}
          value={settings.comic_width}
          disabled={busy}
          onChange={(e) => save({ comic_width: Number(e.target.value) })}
        >
          {[0.4, 0.45, 0.5, 0.55, 0.6, 0.7, 0.8, 0.9, 1].map((n) => (
            <option key={n} value={n}>
              {n === 1 ? "100% - fills the screen" : `${Math.round(n * 100)}% of the screen`}
            </option>
          ))}
        </select>

        <label style={{ marginTop: "0.6rem" }}>
          Fastest scroll
          <Help text="How fast a page may scroll. A page shown only briefly then shows just the part it has time for, from its top (or its bottom, when it scrolls up), instead of racing to the end." />
        </label>
        <select
          style={{ width: "auto" }}
          value={settings.comic_max_speed}
          disabled={busy}
          onChange={(e) => save({ comic_max_speed: Number(e.target.value) })}
        >
          {[0, 100, 150, 200, 300, 400].map((n) => (
            <option key={n} value={n}>
              {n === 0 ? "no limit - always the whole page" : `${n} px a second`}
            </option>
          ))}
        </select>
      </Section>
      )}

      {hasMap && project.features.maps && (
        <Section title="Map shots" subtitle="a journey drawn on the map of the world">
          <Slider
            label={`Camera tilt: ${Math.round(tilt)}°`}
            hint="How far the camera leans from looking straight down at the map on the table. 0° is a flat map; more shows it in perspective, the far side smaller, and on a long journey the table beyond the map's far edge. Applies to every map shot that doesn't set its own."
            value={tilt}
            min={0}
            max={70}
            step={5}
            disabled={busy}
            onChange={setTilt}
            onCommit={() => save({ map_tilt: tilt })}
          />
        </Section>
      )}

      <div style={{ marginTop: "0.75rem" }}>
        <SaveToPreset project={project} what="look" save={api.saveLookToPreset} disabled={busy} />
      </div>
      {error && <p className="error-text">{error.message}</p>}
    </div>
  );
}
