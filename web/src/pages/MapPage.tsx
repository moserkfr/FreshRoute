import { Fragment, useEffect, useMemo, useState } from "react";
import { CircleMarker, LayerGroup, LayersControl, MapContainer, Marker, Polyline, TileLayer, Tooltip } from "react-leaflet";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import network from "../data/network.json";
import ScenarioBar from "../components/ScenarioBar";
import { ErrorNotice, Loading, ViolationsBadge } from "../components/ui";
import { useApp } from "../state/AppState";
import { kg, pct } from "../utils/format";
import "./map.css";

const HUB: [number, number] = [network.hub.lat, network.hub.lon];
const BANDS = [0.5, 0.6, 0.7, 0.8]; // arrival-quality band edges → --q-0 … --q-4

function useThemeColors() {
  const read = () => {
    const cs = getComputedStyle(document.documentElement);
    return {
      q: [0, 1, 2, 3, 4].map((k) => cs.getPropertyValue(`--q-${k}`).trim()),
      idle: cs.getPropertyValue("--muted").trim(),
      hub: cs.getPropertyValue("--red").trim(),
    };
  };
  const [c, setC] = useState(read);
  useEffect(() => {
    const mo = new MutationObserver(() => setC(read()));
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const on = () => setC(read());
    mq.addEventListener("change", on);
    return () => { mo.disconnect(); mq.removeEventListener("change", on); };
  }, []);
  return c;
}

const band = (q: number) => BANDS.filter((e) => q >= e).length;

function vehicleIcon(ids: string[], reefer: boolean) {
  return L.divIcon({
    className: "veh-icon-wrap",
    html: `<div class="veh-icon ${reefer ? "reefer" : ""}"><svg width="14" height="12" viewBox="0 0 14 12" aria-hidden="true"><path d="M1 2h7v6H1zM8 4h3l2 2.5V8H8z" fill="currentColor"/><circle cx="3.5" cy="9.5" r="1.5" fill="currentColor"/><circle cx="10.5" cy="9.5" r="1.5" fill="currentColor"/></svg>${ids.join(" · ")}</div>`,
    iconSize: undefined,
    iconAnchor: [0, 0],
  });
}

export default function MapPage() {
  const { state, ours, busy, error, clearError } = useApp();
  const colors = useThemeColors();

  const dests = state?.destinations.map((d) => ({ id: d.dest_id, name: d.name, lat: d.lat, lon: d.lon, type: d.type }))
    ?? network.destinations;

  const flows = useMemo(() => {
    const out = new Map<string, { kg: number; qkg: number; vehicles: Set<string>; reefer: boolean }>();
    const veh = new Map(state?.vehicles.map((v) => [v.vehicle_id, v]) ?? []);
    for (const a of ours?.plan.allocations ?? []) {
      const f = out.get(a.dest_id) ?? { kg: 0, qkg: 0, vehicles: new Set<string>(), reefer: false };
      f.kg += a.qty_kg;
      f.qkg += a.qty_kg * a.arrival_quality_mean;
      f.vehicles.add(a.vehicle_id);
      if (veh.get(a.vehicle_id)?.type === "reefer") f.reefer = true;
      out.set(a.dest_id, f);
    }
    return out;
  }, [ours, state]);

  const maxKg = Math.max(1, ...[...flows.values()].map((f) => f.kg));
  const closed = new Set(state?.lanes.filter((l) => l.open === false).map((l) => l.dest_id) ?? []);

  return (
    <div className="page wrap">
      <header className="page-head">
        <p className="eyebrow">Map</p>
        <h1 className="h2">every lane from the <em>Kolar hub</em></h1>
        <p>Line thickness shows the kg shipped to each destination. Colour shows the kg-weighted mean arrival quality. Truck labels show which vehicles run each lane.</p>
      </header>
      <ScenarioBar />
      {error && <div style={{ marginTop: 12 }}><ErrorNotice message={error} onClose={clearError} /></div>}
      {busy === "plan" && <div style={{ marginTop: 12 }}><Loading label="Planning…" /></div>}

      <div className="map-layout">
        <div className="map-frame card flush">
          <MapContainer bounds={L.latLngBounds([HUB, ...dests.map((d) => [d.lat, d.lon] as [number, number])])} boundsOptions={{ padding: [40, 40] }} scrollWheelZoom={false} className="map">
            {/* Keyless basemaps: OpenStreetMap's standard map, or Esri imagery with place labels. */}
            <LayersControl position="topright">
              <LayersControl.BaseLayer checked name="Map">
                <TileLayer url="https://tile.openstreetmap.org/{z}/{x}/{y}.png" maxZoom={19} attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors' />
              </LayersControl.BaseLayer>
              <LayersControl.BaseLayer name="Satellite">
                <LayerGroup>
                  <TileLayer url="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}" maxZoom={18} attribution='Imagery &copy; <a href="https://www.esri.com">Esri</a>, Maxar, Earthstar Geographics' />
                  <TileLayer url="https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}" maxZoom={18} />
                </LayerGroup>
              </LayersControl.BaseLayer>
            </LayersControl>
            {dests.map((d) => {
              const f = flows.get(d.id);
              const isClosed = closed.has(d.id);
              const q = f ? f.qkg / f.kg : null;
              const color = isClosed ? colors.hub : q == null ? colors.idle : colors.q[band(q)];
              const weight = f ? 3 + (f.kg / maxKg) * 13 : 1.5;
              const mid: [number, number] = [(HUB[0] + d.lat) / 2, (HUB[1] + d.lon) / 2];
              return (
                <Fragment key={d.id}>
                  <Polyline positions={[HUB, [d.lat, d.lon]]} pathOptions={{ color, weight, opacity: f ? 0.95 : 0.5, dashArray: isClosed ? "6 8" : f ? undefined : "4 6", lineCap: "round" }}>
                    <Tooltip sticky>
                      <strong>{d.name}</strong><br />
                      {isClosed ? "Lane closed" : f ? <>{kg(f.kg)} shipped · arrival quality {pct(q, 0)}<br />vehicles {[...f.vehicles].join(", ")}</> : "Nothing shipped"}
                    </Tooltip>
                  </Polyline>
                  {f && <Marker position={mid} icon={vehicleIcon([...f.vehicles], f.reefer)} interactive={false} />}
                  <CircleMarker center={[d.lat, d.lon]} radius={7} pathOptions={{ color: "#fff", weight: 2, fillColor: color, fillOpacity: 1 }}>
                    <Tooltip direction="top" offset={[0, -6]}>{d.name}</Tooltip>
                  </CircleMarker>
                </Fragment>
              );
            })}
            <CircleMarker center={HUB} radius={10} pathOptions={{ color: "#fff", weight: 3, fillColor: colors.hub, fillOpacity: 1 }}>
              <Tooltip direction="top" offset={[0, -8]} permanent>Kolar hub</Tooltip>
            </CircleMarker>
          </MapContainer>
        </div>

        <aside className="grid">
          <div className="card">
            <p className="eyebrow">Arrival quality</p>
            <div className="q-legend">
              {colors.q.map((c, k) => (
                <span key={k}><i style={{ background: c }} />{k === 0 ? `< ${pct(BANDS[0], 0)}` : k === 4 ? `≥ ${pct(BANDS[3], 0)}` : `${pct(BANDS[k - 1], 0)}–${pct(BANDS[k], 0)}`}</span>
              ))}
            </div>
            <p className="muted" style={{ fontSize: 12.5, margin: "10px 0 0" }}>Line thickness scales with kg shipped, up to {kg(maxKg)}. A dashed grey line means nothing was shipped. A dashed red line means the lane is closed.</p>
          </div>
          <div className="card flush">
            <div className="table-wrap">
              <table className="table">
                <thead><tr><th>Destination</th><th className="r">Shipped</th><th className="r">Quality</th><th>Vehicles</th></tr></thead>
                <tbody>
                  {dests.map((d) => {
                    const f = flows.get(d.id);
                    return (
                      <tr key={d.id}>
                        <td style={{ whiteSpace: "normal" }}>{d.name}{closed.has(d.id) && <span className="badge bad" style={{ marginLeft: 6 }}>closed</span>}</td>
                        <td className="r">{f ? kg(f.kg) : "—"}</td>
                        <td className="r">{f ? pct(f.qkg / f.kg, 0) : "—"}</td>
                        <td>{f ? [...f.vehicles].join(", ") : "—"}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
          {ours ? <div><ViolationsBadge n={ours.summary.violations} /></div> : <p className="muted">Generate a plan to see the flows.</p>}
        </aside>
      </div>
    </div>
  );
}
