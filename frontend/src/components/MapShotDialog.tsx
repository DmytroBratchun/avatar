import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api/client";
import type { EditorMapShot, EditorProject, MapPlace } from "../api/client";

/** A map shot for a slot: a journey from one place to another drawn on the map of
 * the project's world, along one of the known journeys or straight, with pictures
 * of the places in medallions. */
export default function MapShotDialog({
  projectId,
  beatId,
  slotIndex,
  mapName,
  current,
  onChange,
  onClose,
}: {
  projectId: string;
  beatId: string;
  slotIndex: number;
  mapName: string;
  // the slot's map shot, if it has one: the dialog starts from it
  current?: EditorMapShot | null;
  onChange: (project: EditorProject) => void;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const { data: map, error: loadError } = useQuery({ queryKey: ["map", mapName], queryFn: () => api.getMap(mapName) });
  const [from, setFrom] = useState(current?.places[0] ?? "");
  const [to, setTo] = useState(current?.places[current.places.length - 1] ?? "");
  const [route, setRoute] = useState(current?.route ?? "");
  const [pictures, setPictures] = useState<string[]>(current?.pictures ?? []);
  const [describing, setDescribing] = useState<string | null>(null); // place whose picture is being asked for
  const [what, setWhat] = useState("");

  const save = useMutation({
    mutationFn: () => api.setEditorSlotMap(projectId, beatId, slotIndex, { places: [from, to], route, pictures: pictures.filter((p) => p === from || p === to) }),
    onSuccess: (project) => {
      onChange(project);
      onClose();
    },
  });
  const draw = useMutation({
    mutationFn: (placeId: string) => api.makePlacePicture(mapName, placeId, what),
    onSuccess: (place) => {
      setDescribing(null);
      setWhat("");
      setPictures((current) => (current.includes(place.id) ? current : [...current, place.id]));
      queryClient.invalidateQueries({ queryKey: ["map", mapName] });
    },
  });

  const byId = new Map((map?.places ?? []).map((p) => [p.id, p]));
  const sorted = [...(map?.places ?? [])].sort((a, b) => a.name.localeCompare(b.name));
  const error = (loadError ?? save.error ?? draw.error) as Error | null;

  // called as a function, not a component: a component defined in here would be
  // remounted on every render and its input would lose focus at each key
  function placeRow(label: string, place: MapPlace | undefined) {
    if (!place) return null;
    const has = !!place.picture;
    return (
      <div style={{ display: "flex", gap: "0.6rem", alignItems: "center", marginTop: "0.4rem" }}>
        {has ? (
          <img src={api.placePictureUrl(mapName, place.id, place.picture ?? "")} alt="" style={{ width: "56px", height: "56px", objectFit: "cover", borderRadius: "50%" }} />
        ) : (
          <div style={{ width: "56px", height: "56px", borderRadius: "50%", background: "var(--bg-hover)" }} />
        )}
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: "0.85rem" }}>
            {label}: {place.name} <span className="hint" style={{ margin: 0 }}>· {place.side === "evil" ? "the enemy" : "the free peoples"}</span>
          </div>
          {has ? (
            <label className="form-inline-check" style={{ margin: 0 }}>
              <input
                type="checkbox"
                checked={pictures.includes(place.id)}
                onChange={() => setPictures(pictures.includes(place.id) ? pictures.filter((p) => p !== place.id) : [...pictures, place.id])}
              />
              Show its picture in a medallion
            </label>
          ) : describing === place.id ? (
            <div style={{ display: "flex", gap: "0.4rem", marginTop: "0.2rem" }}>
              <input
                autoFocus
                style={{ flex: 1 }}
                placeholder={`What ${place.name} looks like (optional)`}
                value={what}
                onChange={(e) => setWhat(e.target.value)}
              />
              <button className="small" disabled={draw.isPending} onClick={() => draw.mutate(place.id)}>
                {draw.isPending ? "Drawing…" : "Draw"}
              </button>
              <button className="ghost small" disabled={draw.isPending} onClick={() => setDescribing(null)}>
                Cancel
              </button>
            </div>
          ) : (
            <button className="ghost small" onClick={() => setDescribing(place.id)} title="One GPT Image picture in the map's style - paid, about $0.2">
              Draw a picture…
            </button>
          )}
        </div>
      </div>
    );
  }

  return (
    <div
      style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.5)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 100 }}
      onClick={onClose}
    >
      <div className="card" style={{ width: "min(620px, 94vw)" }} onClick={(e) => e.stopPropagation()}>
        <div className="card-header">
          <h2 style={{ margin: 0, fontSize: "1rem" }}>Map{map ? ` of ${map.title}` : ""}</h2>
          <button className="ghost small" onClick={onClose}>
            Close
          </button>
        </div>
        {!map ? (
          <p className="hint">{loadError ? "" : "Loading the map…"}</p>
        ) : (
          <>
            <p className="hint" style={{ marginTop: 0 }}>
              The way from one place to another is drawn across the map while the camera follows it.
            </p>
            <div style={{ display: "flex", gap: "0.6rem", flexWrap: "wrap" }}>
              <div style={{ flex: 1, minWidth: "12rem" }}>
                <label style={{ fontSize: "0.8rem" }}>From</label>
                <select value={from} onChange={(e) => setFrom(e.target.value)}>
                  <option value="">Choose a place…</option>
                  {sorted.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name}
                    </option>
                  ))}
                </select>
              </div>
              <div style={{ flex: 1, minWidth: "12rem" }}>
                <label style={{ fontSize: "0.8rem" }}>To</label>
                <select value={to} onChange={(e) => setTo(e.target.value)}>
                  <option value="">Choose a place…</option>
                  {sorted.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name}
                    </option>
                  ))}
                </select>
              </div>
            </div>
            <label style={{ fontSize: "0.8rem", marginTop: "0.5rem" }}>Way</label>
            <select value={route} onChange={(e) => setRoute(e.target.value)}>
              <option value="">Straight from one to the other</option>
              {map.routes.map((r) => (
                <option key={r.id} value={r.id}>
                  Along {r.label}
                </option>
              ))}
            </select>
            {placeRow("From", byId.get(from))}
            {to !== from && placeRow("To", byId.get(to))}
            <div style={{ marginTop: "0.9rem", display: "flex", gap: "0.5rem", alignItems: "center" }}>
              <button onClick={() => save.mutate()} disabled={!from || !to || from === to || save.isPending}>
                {save.isPending ? "Saving…" : "Use this map"}
              </button>
              <span className="hint" style={{ margin: 0 }}>
                Replaces the slot's clip and locks it.
              </span>
            </div>
            {map.credit && (
              <p className="hint" style={{ fontSize: "0.7rem", marginBottom: 0 }}>
                {map.credit}
              </p>
            )}
          </>
        )}
        {error && <p className="error-text">{error.message}</p>}
      </div>
    </div>
  );
}
