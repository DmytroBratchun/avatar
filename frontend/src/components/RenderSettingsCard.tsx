import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api/client";
import type { EditorProject } from "../api/client";

// same order as the backend: top-left, top-right, bottom-right, bottom-left
const CORNER_NAMES = ["Top left", "Top right", "Bottom right", "Bottom left"];

export default function RenderSettingsCard({
  project,
  disabled,
  onChange,
}: {
  project: EditorProject;
  disabled: boolean;
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

  const saveMutation = useMutation({
    mutationFn: (payload: Parameters<typeof api.updateEditorProject>[1]) => api.updateEditorProject(project.id, payload),
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
  const framed = settings.layout === "framed";
  const error = (saveMutation.error ?? uploadMutation.error ?? defaultMutation.error) as Error | null;

  return (
    <div className="card">
      <h3>Look</h3>
      <div className="field-row">
        <div>
          <label>Layout</label>
          <select value={settings.layout} disabled={busy} onChange={(e) => saveMutation.mutate({ layout: e.target.value })}>
            <option value="framed">Framed - clip in a frame over a blurred background</option>
            <option value="simple">Simple - clip fills the screen</option>
          </select>
        </div>
        {framed && (
          <div>
            <label>Frame</label>
            <select value={settings.frame} disabled={busy} onChange={(e) => saveMutation.mutate({ frame: e.target.value })}>
              <option value="border">Plain border</option>
              <option value="none">No frame</option>
              {(frames ?? []).map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>
          </div>
        )}
      </div>

      {framed && (
        <>
          <div className="field-row" style={{ marginTop: "0.5rem" }}>
            <div>
              <label>Background blur: {Math.round(blur)}</label>
              <input
                type="range"
                min={0}
                max={100}
                value={blur}
                disabled={busy}
                onChange={(e) => setBlur(Number(e.target.value))}
                onPointerUp={() => saveMutation.mutate({ blur })}
                onKeyUp={() => saveMutation.mutate({ blur })}
              />
            </div>
            <div>
              <label>Background darkening: {Math.round(dim * 100)}%</label>
              <input
                type="range"
                min={0}
                max={0.9}
                step={0.05}
                value={dim}
                disabled={busy}
                onChange={(e) => setDim(Number(e.target.value))}
                onPointerUp={() => saveMutation.mutate({ dim })}
                onKeyUp={() => saveMutation.mutate({ dim })}
              />
            </div>
          </div>
          <div className="field-row" style={{ marginTop: "0.5rem" }}>
            <div>
              <label>Clip size: {Math.round(scale * 100)}%</label>
              <input
                type="range"
                min={0.7}
                max={1.3}
                step={0.01}
                value={scale}
                disabled={busy}
                onChange={(e) => setScale(Number(e.target.value))}
                onPointerUp={() => saveMutation.mutate({ clip_scale: scale })}
                onKeyUp={() => saveMutation.mutate({ clip_scale: scale })}
              />
              <p className="hint" style={{ marginTop: "0.2rem", marginBottom: 0 }}>
                Grows or shrinks the clip inside the frame, about its centre, to fit it to the artwork.
              </p>
            </div>
            <div>
              <label>Frame size: {Math.round(frameScale * 100)}%</label>
              <input
                type="range"
                min={0.5}
                max={1.3}
                step={0.01}
                value={frameScale}
                disabled={busy}
                onChange={(e) => setFrameScale(Number(e.target.value))}
                onPointerUp={() => saveMutation.mutate({ frame_scale: frameScale })}
                onKeyUp={() => saveMutation.mutate({ frame_scale: frameScale })}
              />
              <p className="hint" style={{ marginTop: "0.2rem", marginBottom: 0 }}>
                Resizes the whole frame with the clip inside it. 100% is as tall as the screen.
              </p>
            </div>
          </div>
          <div className="field-row" style={{ marginTop: "0.5rem" }}>
            <div>
              <label>Clip corner rounding: {radii ? "per corner" : radius === 0 ? "off" : `${radius}px`}</label>
              {!radii && (
                <input
                  type="range"
                  min={0}
                  max={160}
                  step={4}
                  value={radius}
                  disabled={busy}
                  onChange={(e) => setRadius(Number(e.target.value))}
                  onPointerUp={() => saveMutation.mutate({ corner_radius: radius })}
                  onKeyUp={() => saveMutation.mutate({ corner_radius: radius })}
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
                    saveMutation.mutate({ corner_radii: next });
                  }}
                />
                Set each corner separately
              </label>
              {radii && (
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "0.3rem 0.75rem", marginTop: "0.3rem" }}>
                  {/* laid out like the corners themselves: top row, then bottom row */}
                  {([0, 1, 3, 2] as const).map((corner) => (
                    <div key={corner}>
                      <label style={{ fontSize: "0.75rem" }}>
                        {CORNER_NAMES[corner]}: {radii[corner]}px
                      </label>
                      <input
                        type="range"
                        min={0}
                        max={160}
                        step={4}
                        value={radii[corner]}
                        disabled={busy}
                        onChange={(e) => setRadii(radii.map((r, i) => (i === corner ? Number(e.target.value) : r)))}
                        onPointerUp={() => saveMutation.mutate({ corner_radii: radii })}
                        onKeyUp={() => saveMutation.mutate({ corner_radii: radii })}
                      />
                    </div>
                  ))}
                </div>
              )}
              <p className="hint" style={{ marginTop: "0.2rem", marginBottom: 0 }}>
                Rounds the clip so its corners don't stick out from under the frame.
              </p>
            </div>
            <div>
              <label>Background speed: {Math.round(speed * 100)}%</label>
              <input
                type="range"
                min={0.25}
                max={1}
                step={0.05}
                value={speed}
                disabled={busy}
                onChange={(e) => setSpeed(Number(e.target.value))}
                onPointerUp={() => saveMutation.mutate({ background_speed: speed })}
                onKeyUp={() => saveMutation.mutate({ background_speed: speed })}
              />
              <p className="hint" style={{ marginTop: "0.2rem", marginBottom: 0 }}>
                Slows the scenery behind the frame. Each shot lasts longer, so the background changes less often.
              </p>
            </div>
          </div>
          <label style={{ display: "flex", alignItems: "center", gap: "0.4rem", marginTop: "0.6rem" }}>
            <input
              type="checkbox"
              style={{ width: "auto" }}
              checked={settings.wide_full_screen}
              disabled={busy}
              onChange={(e) => saveMutation.mutate({ wide_full_screen: e.target.checked })}
            />
            Widescreen clips fill the screen, without the frame
          </label>
          <p className="hint" style={{ marginTop: "0.2rem" }}>
            For footage that is already 16:9 (The Legend of Korra). Off: every clip is cropped into the frame.
          </p>
          <label style={{ marginTop: "0.5rem", fontSize: "0.8rem" }}>Add a frame image</label>
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
        </>
      )}
      <p className="hint" style={{ marginTop: "0.5rem", marginBottom: 0 }}>
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
