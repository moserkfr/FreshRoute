import { useState } from "react";
import { api } from "../api/client";
import type { Leg, WhatIfEvent, WhatIfResponse } from "../api/types";
import ScenarioBar from "../components/ScenarioBar";
import { ErrorNotice, Loading, PillButton, ViolationsBadge } from "../components/ui";
import { useApp } from "../state/AppState";
import { inr, kg, pct, pp, signed } from "../utils/format";
import "./whatif.css";

type Kind = WhatIfEvent["type"];

const EVENTS: { kind: Kind; tone: string; title: string; blurb: string }[] = [
  { kind: "vehicle_down", tone: "plum", title: "break a vehicle", blurb: "A truck or reefer is out of service today." },
  { kind: "lane_closure", tone: "red", title: "close a lane", blurb: "The road to one destination is shut." },
  { kind: "demand_spike", tone: "yellow", title: "spike demand", blurb: "A buyer suddenly wants more." },
  { kind: "price_shock", tone: "peri", title: "crash a price", blurb: "A market's price moves sharply." },
  { kind: "new_batches", tone: "green", title: "add batches", blurb: "Extra stock arrives at the hub." },
];

const CHANGE_TONE: Record<string, string> = {
  rerouted: "warn", now_held: "bad", now_shipped: "good", new_batch: "good", qty_changed: "", discount_changed: "warn",
};

function legs(ls: Leg[], name: (id: string) => string) {
  if (ls.length === 0) return <span className="muted">held</span>;
  return ls.map((l, k) => (
    <span key={k} className="leg">
      {kg(l.qty_kg)} → {name(l.dest_id)} <span className="muted">on {l.vehicle_id}{l.discount_pct ? `, ${pct(l.discount_pct, 0)} off` : ""}</span>
    </span>
  ));
}

export default function WhatIf() {
  const { state, ours, settings, busy, generatePlan, adopt, error, clearError } = useApp();
  const [kind, setKind] = useState<Kind>("vehicle_down");
  const [p, setP] = useState({ vehicle_id: "R1", dest_id: "", commodity_id: "", mult: 1.8, duration_days: 2, qty_kg: 1000, grade: "A" as "A" | "B" | "C" });
  const [running, setRunning] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [result, setResult] = useState<WhatIfResponse | null>(null);
  const [applied, setApplied] = useState<string[]>([]);

  const destName = (id: string) => state?.destinations.find((d) => d.dest_id === id)?.name ?? id;
  const dest = p.dest_id || state?.destinations[0]?.dest_id || "";
  const commodity = p.commodity_id || state?.commodities[0]?.id || "";

  function buildEvent(): WhatIfEvent {
    switch (kind) {
      case "vehicle_down": return { type: kind, params: { vehicle_id: p.vehicle_id } };
      case "lane_closure": return { type: kind, params: { dest_id: dest } };
      case "demand_spike": return { type: kind, params: { dest_id: dest, commodity_id: p.commodity_id || null, mult: p.mult, duration_days: p.duration_days } };
      case "price_shock": return { type: kind, params: { dest_id: dest, commodity_id: p.commodity_id || null, mult: p.mult } };
      case "new_batches": return { type: kind, params: { batches: [{ commodity_id: commodity, qty_kg: p.qty_kg, grade: p.grade }] } };
    }
  }

  function describe(ev: WhatIfEvent): string {
    switch (ev.type) {
      case "vehicle_down": return `${ev.params.vehicle_id} breaks down`;
      case "lane_closure": return `Lane to ${destName(ev.params.dest_id)} closed`;
      case "demand_spike": return `Demand ×${ev.params.mult} at ${destName(ev.params.dest_id)}${ev.params.commodity_id ? ` (${ev.params.commodity_id})` : ""} for ${ev.params.duration_days} d`;
      case "price_shock": return `Price ×${ev.params.mult} at ${destName(ev.params.dest_id)}${ev.params.commodity_id ? ` (${ev.params.commodity_id})` : ""}`;
      case "new_batches": return `+${kg(ev.params.batches[0].qty_kg)} ${ev.params.batches[0].commodity_id} arrives`;
    }
  }

  async function run(ev: WhatIfEvent = buildEvent()) {
    if (!state || !ours) return;
    setRunning(true);
    setErr(null);
    try {
      const r = await api.whatif({ state, event: ev, previous_plan: ours.plan, risk_p: settings.riskP });
      setResult(r);
      setApplied((a) => [...a, describe(ev)]);
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setRunning(false);
    }
  }

  function keep() {
    if (!result) return;
    adopt(result.state, { plan: result.plan, summary: result.diff.summary_after });
    setResult(null);
  }

  const d = result?.diff;
  const delta = d?.summary_delta ?? {};
  const after = d?.summary_after;
  const vehChanges = d ? [...new Set([...Object.keys(d.vehicles_before), ...Object.keys(d.vehicles_after)])]
    .filter((v) => d.vehicles_before[v] !== d.vehicles_after[v]).sort() : [];

  return (
    <div className="page wrap">
      <header className="page-head">
        <p className="eyebrow">What-if</p>
        <h1 className="h2">break something, <em>replan in seconds</em></h1>
        <p>Apply a disruption to the current state. FreshRoute replans and shows what moved and what changed in the KPIs, measured against the plan you're looking at.</p>
      </header>
      <ScenarioBar />
      {error && <div style={{ marginTop: 12 }}><ErrorNotice message={error} onClose={clearError} /></div>}

      {!ours ? (
        <div className="card empty" style={{ marginTop: 12 }}>
          <p className="h3" style={{ color: "var(--ink)" }}>start from a plan</p>
          <p>A what-if is compared against the plan you're looking at, so generate one first.</p>
          <div style={{ display: "flex", justifyContent: "center", marginTop: 12 }}>
            <PillButton tone="amber" onClick={generatePlan} busy={busy !== null}>Generate plan</PillButton>
          </div>
        </div>
      ) : (
        <>
          {applied.length > 0 && (
            <div className="row" style={{ marginTop: 14, gap: 6 }}>
              <span className="muted" style={{ fontSize: 13 }}>Applied so far:</span>
              {applied.map((a, k) => <span key={k} className="badge">{a}</span>)}
            </div>
          )}
          <div className="event-grid">
            {EVENTS.map((e) => (
              <button key={e.kind} className={`block event ${e.tone} ${kind === e.kind ? "on" : ""}`} onClick={() => setKind(e.kind)} aria-pressed={kind === e.kind}>
                <span className="event-title">{e.title}</span>
                <span className="event-blurb">{e.blurb}</span>
              </button>
            ))}
          </div>

          <div className="card event-form">
            <div className="row" style={{ alignItems: "flex-end" }}>
              {kind === "vehicle_down" && (
                <label className="field">Vehicle
                  <select className="select" value={p.vehicle_id} onChange={(e) => setP({ ...p, vehicle_id: e.target.value })}>
                    {state?.vehicles.map((v) => <option key={v.vehicle_id} value={v.vehicle_id} disabled={v.available === false}>{v.vehicle_id} · {v.type} · {kg(v.capacity_kg)}{v.available === false ? " (down)" : ""}</option>)}
                  </select>
                </label>
              )}
              {(kind === "lane_closure" || kind === "demand_spike" || kind === "price_shock") && (
                <label className="field">Destination
                  <select className="select" value={dest} onChange={(e) => setP({ ...p, dest_id: e.target.value })}>
                    {state?.destinations.map((x) => <option key={x.dest_id} value={x.dest_id}>{x.name}</option>)}
                  </select>
                </label>
              )}
              {(kind === "demand_spike" || kind === "price_shock") && (
                <>
                  <label className="field">Commodity
                    <select className="select" value={p.commodity_id} onChange={(e) => setP({ ...p, commodity_id: e.target.value })}>
                      <option value="">All commodities</option>
                      {state?.commodities.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
                    </select>
                  </label>
                  <label className="field">Multiplier <span className="muted num">×{p.mult.toFixed(2)} ({p.mult >= 1 ? "+" : "−"}{Math.abs((p.mult - 1) * 100).toFixed(0)}%)</span>
                    <input type="range" min={kind === "price_shock" ? 0.3 : 1.1} max={kind === "price_shock" ? 1.8 : 3} step={0.05} value={p.mult} onChange={(e) => setP({ ...p, mult: Number(e.target.value) })} />
                  </label>
                </>
              )}
              {kind === "demand_spike" && (
                <label className="field" style={{ width: 120 }}>Duration (days)
                  <input className="input num" type="number" min={1} max={14} value={p.duration_days} onChange={(e) => setP({ ...p, duration_days: Math.max(1, Number(e.target.value) || 1) })} />
                </label>
              )}
              {kind === "new_batches" && (
                <>
                  <label className="field">Commodity
                    <select className="select" value={commodity} onChange={(e) => setP({ ...p, commodity_id: e.target.value })}>
                      {state?.commodities.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
                    </select>
                  </label>
                  <label className="field" style={{ width: 140 }}>Quantity (kg)
                    <input className="input num" type="number" min={1} step={100} value={p.qty_kg} onChange={(e) => setP({ ...p, qty_kg: Math.max(1, Number(e.target.value) || 1) })} />
                  </label>
                  <label className="field" style={{ width: 100 }}>Grade
                    <select className="select" value={p.grade} onChange={(e) => setP({ ...p, grade: e.target.value as "A" | "B" | "C" })}>
                      <option>A</option><option>B</option><option>C</option>
                    </select>
                  </label>
                </>
              )}
              <div className="spacer" />
              <PillButton tone="amber" onClick={() => run()} busy={running}>{running ? "Replanning…" : "Apply & replan"}</PillButton>
            </div>
          </div>

          {err && <div style={{ marginTop: 12 }}><ErrorNotice message={err} onClose={() => setErr(null)} /></div>}
          {running && <div style={{ marginTop: 12 }}><Loading label="Replanning. This takes a few seconds…" /></div>}

          {d && after && (
            <section className="whatif-result">
              <div className="row">
                <h2 className="h3">what <em>changed</em></h2>
                <ViolationsBadge n={after.violations} />
                <div className="spacer" />
                <PillButton tone="ghost" onClick={() => setResult(null)}>Discard</PillButton>
                <PillButton onClick={keep}>Keep as current plan</PillButton>
              </div>

              <div className="kpis" style={{ marginTop: 12 }}>
                {[
                  { k: "expected_net_value_inr", label: "Expected net value", v: inr(after.expected_net_value_inr, true), dv: signed(delta.expected_net_value_inr ?? 0, (x) => inr(x, true)), tone: "yellow" },
                  { k: "expected_fill_pct", label: "Expected fill", v: pct(after.expected_fill_pct), dv: pp(delta.expected_fill_pct ?? 0), tone: "brown" },
                  { k: "shipped_kg", label: "Shipped", v: kg(after.shipped_kg), dv: signed(delta.shipped_kg ?? 0, kg), tone: "peri" },
                  { k: "held_kg", label: "Held", v: kg(after.held_kg), dv: signed(delta.held_kg ?? 0, kg), tone: "pink" },
                  { k: "mean_arrival_quality", label: "Mean arrival quality", v: pct(after.mean_arrival_quality), dv: pp(delta.mean_arrival_quality ?? 0), tone: "green" },
                  { k: "violations", label: "Violations", v: String(after.violations), dv: signed(delta.violations ?? 0, String), tone: "red" },
                ].map((x) => (
                  <div key={x.k} className={`block kpi ${x.tone}`}>
                    <div className="value">{x.dv}</div>
                    <div><div className="label">{x.label}</div><div className="sub">now {x.v}</div></div>
                  </div>
                ))}
              </div>

              {vehChanges.length > 0 && (
                <div className="card" style={{ marginTop: 12 }}>
                  <p className="eyebrow">Vehicle assignments</p>
                  <div className="row" style={{ gap: 6 }}>
                    {vehChanges.map((v) => (
                      <span key={v} className="badge">{v}: {d.vehicles_before[v] ? destName(d.vehicles_before[v]) : "idle"} → {d.vehicles_after[v] ? destName(d.vehicles_after[v]) : "idle"}</span>
                    ))}
                  </div>
                </div>
              )}

              <div className="card flush" style={{ marginTop: 12 }}>
                <div className="card-title row" style={{ padding: "14px 16px 6px", justifyContent: "space-between" }}>
                  <span className="h3">batch changes</span><span className="muted num">{d.changes.length}</span>
                </div>
                {d.changes.length === 0 ? <p className="empty">The plan didn't change.</p> : (
                  <div className="table-wrap">
                    <table className="table">
                      <thead><tr><th>Batch</th><th>Change</th><th>Before</th><th>After</th></tr></thead>
                      <tbody>
                        {d.changes.map((c) => (
                          <tr key={c.batch_id}>
                            <td>{c.batch_id} <span className="muted">{c.commodity_id}</span></td>
                            <td><span className={`badge ${CHANGE_TONE[c.change] ?? ""}`}>{c.change.replace(/_/g, " ")}</span></td>
                            <td className="legs">{legs(c.before, destName)}</td>
                            <td className="legs">{legs(c.after, destName)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            </section>
          )}
        </>
      )}
    </div>
  );
}
