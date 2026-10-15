import { useMemo, useState } from "react";
import ScenarioBar from "../components/ScenarioBar";
import { ErrorNotice, Kpi, Loading, PillButton, ViolationsBadge } from "../components/ui";
import type { PlanSummary } from "../api/types";
import type { Reason } from "../types/freshroute";
import { useApp } from "../state/AppState";
import { clock, inr, kg, pct, pp, signed } from "../utils/format";
import "./today-plan.css";

function driverValue(d: { value: number; unit: string }) {
  if (d.unit === "fraction") return pct(d.value, 0);
  if (d.unit === "₹") return inr(d.value);
  if (d.unit.startsWith("₹/")) return `₹${d.value.toFixed(2)}/${d.unit.slice(2)}`;
  return `${d.value.toLocaleString("en-IN", { maximumFractionDigits: 2 })} ${d.unit}`;
}

export function WhyCard({ batchId, reason, onClose }: { batchId: string; reason?: Reason; onClose?: () => void }) {
  if (!reason) return <div className="block plum why"><p className="eyebrow">why</p><p>No reason recorded for {batchId}.</p></div>;
  return (
    <div className="block plum why">
      <div className="row">
        <p className="eyebrow" style={{ color: "var(--pink)", margin: 0 }}>why · {batchId}</p>
        <div className="spacer" />
        {onClose && <button className="why-close" onClick={onClose} aria-label="Close">×</button>}
      </div>
      <p className="why-decision">{reason.decision_text}</p>
      {reason.runner_up_text && (
        <div className="why-runner">
          <span className="eyebrow" style={{ color: "rgb(255 255 255 / 0.6)" }}>runner-up</span>
          <span>{reason.runner_up_text.replace(/^\+₹-/, "−₹")}</span>
          {reason.delta_value_inr != null && (
            <span className="num why-delta">
              Chosen option is worth {inr(Math.abs(reason.delta_value_inr))} {reason.delta_value_inr <= 0 ? "more" : "less"} than the runner-up
            </span>
          )}
        </div>
      )}
      <div className="why-drivers">
        {(reason.drivers ?? []).map((d) => (
          <div key={d.name} className="why-driver">
            <span className="why-driver-name">{d.name.replace(/_/g, " ")}</span>
            <span className="num why-driver-val">{driverValue(d)}</span>
            <span className="why-driver-text">{d.text}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function Compare({ ours, base }: { ours: PlanSummary; base: PlanSummary }) {
  const rows: [string, (s: PlanSummary) => number | null, (v: number) => string, (d: number) => string][] = [
    ["Expected net value", (s) => s.expected_net_value_inr, (v) => inr(v), (d) => signed(d, (x) => inr(x))],
    ["Expected fill", (s) => s.expected_fill_pct, (v) => pct(v), (d) => pp(d)],
    ["Shipped", (s) => s.shipped_kg, kg, (d) => signed(d, kg)],
    ["Held", (s) => s.held_kg, kg, (d) => signed(d, kg)],
    ["Mean arrival quality", (s) => s.mean_arrival_quality, (v) => pct(v), (d) => pp(d)],
    ["Freight", (s) => s.freight_inr, (v) => inr(v), (d) => signed(d, (x) => inr(x))],
    ["Trips", (s) => s.trips, (v) => String(v), (d) => signed(d, String)],
    ["Violations", (s) => s.violations, (v) => String(v), (d) => signed(d, String)],
  ];
  return (
    <div className="card flush">
      <div className="table-wrap">
        <table className="table">
          <thead><tr><th>vs today's practice</th><th className="r">FreshRoute</th><th className="r">B1 · FIFO nearest</th><th className="r">Δ</th></tr></thead>
          <tbody>
            {rows.map(([label, get, fmt, dfmt]) => {
              const a = get(ours), b = get(base);
              return (
                <tr key={label}>
                  <td>{label}</td>
                  <td className="r">{a == null ? "—" : fmt(a)}</td>
                  <td className="r muted">{b == null ? "—" : fmt(b)}</td>
                  <td className="r">{a == null || b == null ? "—" : dfmt(a - b)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export default function TodayPlan() {
  const { state, ours, baseline, busy, error, clearError } = useApp();
  const [selected, setSelected] = useState<string | null>(null);

  const lookup = useMemo(() => ({
    batch: new Map(state?.batches.map((b) => [b.batch_id, b]) ?? []),
    dest: new Map(state?.destinations.map((d) => [d.dest_id, d.name]) ?? []),
    vehicle: new Map(state?.vehicles.map((v) => [v.vehicle_id, v]) ?? []),
  }), [state]);

  const plan = ours?.plan;
  const s = ours?.summary;
  const b = baseline?.summary;
  const reasons = plan?.reasons ?? {};
  const pick = selected ?? plan?.allocations?.[0]?.batch_id ?? null;

  return (
    <div className="page wrap">
      <header className="page-head">
        <p className="eyebrow">Today's plan</p>
        <h1 className="h2">which batch goes <em>where</em>, today</h1>
        <p>Load a day, generate the plan and compare it with B1, today's FIFO-to-nearest practice, on the same state. Click a row to see why the planner chose it.</p>
      </header>

      <ScenarioBar />
      {error && <div style={{ marginTop: 12 }}><ErrorNotice message={error} onClose={clearError} /></div>}
      {busy === "plan" && <div style={{ marginTop: 12 }}><Loading label="Planning with FreshRoute and B1. The optimiser takes a few seconds…" /></div>}

      {!ours && !busy && (
        <div className="card empty" style={{ marginTop: 12 }}>
          <p className="h3" style={{ color: "var(--ink)" }}>no plan <em>yet</em></p>
          <p>Pick a scenario and seed, then generate the plan. For the demo, use Festival Peak with seed 51.</p>
        </div>
      )}

      {ours && s && (
        <>
          <div className="row plan-badges">
            <ViolationsBadge n={s.violations} />
            {s.fallback_used && <span className="badge warn">Fallback heuristic used</span>}
            <span className="badge num">solver: {plan?.solver_meta?.status ?? "—"}{plan?.solver_meta?.gap != null ? ` · gap ${pct(plan.solver_meta.gap)}` : ""} · {s.solve_time_s.toFixed(1)} s</span>
            {plan?.vehicle_setpoints && Object.keys(plan.vehicle_setpoints).length > 0 && (
              <span className="badge">reefers: {Object.entries(plan.vehicle_setpoints).map(([v, g]) => `${v} ${g}`).join(", ")}</span>
            )}
          </div>

          <div className="kpis" style={{ marginTop: 10 }}>
            <Kpi tone="yellow" label="Expected net value" value={inr(s.expected_net_value_inr, true)} sub={b ? `${signed(s.expected_net_value_inr - b.expected_net_value_inr, (x) => inr(x, true))} vs B1` : undefined} />
            <Kpi tone="brown" label="Expected fill" value={pct(s.expected_fill_pct)} sub={b ? `${pp(s.expected_fill_pct - b.expected_fill_pct)} vs B1` : undefined} />
            <Kpi tone="peri" label="Shipped" value={kg(s.shipped_kg)} sub={`${s.trips} trips · ${inr(s.freight_inr, true)} freight`} />
            <Kpi tone="pink" label="Held at hub" value={kg(s.held_kg)} sub={`of ${kg(s.stock_kg)} stock`} />
            <Kpi tone="green" label="Mean arrival quality" value={pct(s.mean_arrival_quality)} sub={b?.mean_arrival_quality != null && s.mean_arrival_quality != null ? `${pp(s.mean_arrival_quality - b.mean_arrival_quality)} vs B1` : undefined} />
            <Kpi tone="red" label="Constraint violations" value={s.violations} sub={b ? `B1: ${b.violations}` : undefined} />
          </div>

          <div className="plan-layout">
            <div className="grid">
              <div className="card flush">
                <div className="card-title row"><span className="h3">allocations</span><span className="muted num">{plan?.allocations?.length ?? 0} legs</span></div>
                <div className="table-wrap">
                  <table className="table">
                    <thead>
                      <tr><th>Batch</th><th>Commodity</th><th>Destination</th><th>Vehicle</th><th className="r">Qty</th><th className="r">Discount</th><th className="r">Arrival quality</th><th>ETA</th><th className="r">Exp. net value</th></tr>
                    </thead>
                    <tbody>
                      {plan?.allocations?.map((a, n) => {
                        const bt = lookup.batch.get(a.batch_id);
                        const veh = lookup.vehicle.get(a.vehicle_id);
                        return (
                          <tr key={`${a.batch_id}-${n}`} className={`clickable ${pick === a.batch_id ? "selected" : ""}`} onClick={() => setSelected(a.batch_id)}
                            tabIndex={0} onKeyDown={(e) => e.key === "Enter" && setSelected(a.batch_id)}>
                            <td>{a.batch_id}</td>
                            <td>{bt?.commodity_id ?? "—"} <span className="muted">{bt?.grade}</span></td>
                            <td>{lookup.dest.get(a.dest_id) ?? a.dest_id}</td>
                            <td>{a.vehicle_id} <span className="muted">{veh?.type === "reefer" ? "reefer" : ""}</span></td>
                            <td className="r">{kg(a.qty_kg)}</td>
                            <td className="r">{a.discount_pct ? pct(a.discount_pct, 0) : "—"}</td>
                            <td className="r">{pct(a.arrival_quality_mean, 0)} <span className="muted">p10 {pct(a.arrival_quality_p10, 0)}</span></td>
                            <td>{clock(a.eta_ts)}</td>
                            <td className="r">{inr(a.expected_net_value)}</td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </div>

              {plan?.holds && plan.holds.length > 0 && (
                <div className="card flush">
                  <div className="card-title row"><span className="h3">held at hub</span><span className="muted num">{plan.holds.length} batches</span></div>
                  <div className="table-wrap">
                    <table className="table">
                      <thead><tr><th>Batch</th><th>Commodity</th><th className="r">Qty</th><th>Reason</th></tr></thead>
                      <tbody>
                        {plan.holds.map((h) => (
                          <tr key={h.batch_id} className={`clickable ${pick === h.batch_id ? "selected" : ""}`} onClick={() => setSelected(h.batch_id)}>
                            <td>{h.batch_id}</td>
                            <td>{lookup.batch.get(h.batch_id)?.commodity_id ?? "—"}</td>
                            <td className="r">{kg(h.qty_kg)}</td>
                            <td style={{ whiteSpace: "normal" }}>{h.reason}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}

              {b && <Compare ours={s} base={b} />}
            </div>

            <aside className="plan-side">
              {pick && <WhyCard batchId={pick} reason={reasons[pick]} />}
              <div className="row" style={{ marginTop: 10 }}>
                <PillButton tone="ghost" to="/map">See it on the map</PillButton>
                <PillButton tone="ghost" to="/whatif">Try a what-if</PillButton>
              </div>
            </aside>
          </div>
        </>
      )}
    </div>
  );
}
