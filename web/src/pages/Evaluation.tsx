import { useMemo, useState } from "react";
import type { CoreMetric, EvalReport, Stat } from "../api/types";
import { CiBars, seriesColor, Tornado } from "../components/EvalCharts";
import { Loading } from "../components/ui";
import { useEvalReport } from "../state/useEvalReport";
import { inr, pct, PLANNER_LABEL, PLANNER_ORDER, pp, signed, titleCase } from "../utils/format";
import "./evaluation.css";

const METRICS: { key: CoreMetric; label: string; fmt: (v: number) => string; better: "higher" | "lower" }[] = [
  { key: "net_revenue_inr", label: "Net revenue", fmt: (v) => inr(v, true), better: "higher" },
  { key: "spoilage_pct", label: "Spoilage", fmt: (v) => pct(v), better: "lower" },
  { key: "fulfilment_pct", label: "Fulfilment", fmt: (v) => pct(v), better: "higher" },
  { key: "fulfilment_of_possible_pct", label: "Of possible demand", fmt: (v) => pct(v), better: "higher" },
  { key: "violations_total", label: "Violations", fmt: (v) => v.toFixed(0), better: "lower" },
];

function domainFor(r: EvalReport, metric: CoreMetric, planners: string[]): [number, number] {
  const vals = Object.values(r.aggregate).flatMap((sc) => planners.flatMap((p) => sc[p]?.[metric] ? [sc[p][metric]!.ci_lo, sc[p][metric]!.ci_hi] : []));
  return [Math.min(0, ...vals), Math.max(0, ...vals) * 1.02];
}

function ciText(s: Stat, fmt: (v: number) => string) {
  return `${fmt(s.ci_lo)} to ${fmt(s.ci_hi)}`;
}

export default function Evaluation() {
  const { report, isSample } = useEvalReport();
  const [metric, setMetric] = useState<CoreMetric>("net_revenue_inr");
  const [hidden, setHidden] = useState<Set<string>>(new Set());

  const planners = useMemo(() => PLANNER_ORDER.filter((p) => report?.meta.planners.includes(p)), [report]);
  const shown = planners.filter((p) => !hidden.has(p));
  const first = report ? Object.values(Object.values(report.aggregate)[0] ?? {})[0] : undefined;
  const metrics = METRICS.filter((x) => !first || first[x.key] != null);
  const newProtocol = first?.fulfilment_of_possible_pct != null;
  const m = METRICS.find((x) => x.key === metric)!;

  if (!report) return <div className="page wrap"><Loading label="Loading the evaluation report…" /></div>;

  const scenarios = report.meta.scenarios;
  const domain = domainFor(report, metric, shown);
  const baseline = report.meta.baseline;
  const sens = report.sensitivity;

  return (
    <div className="eval">
      <section className="eval-hero">
        <div className="wrap">
          <p className="eyebrow" style={{ color: "var(--pink)" }}>Evaluation</p>
          <h1 className="h2" style={{ textAlign: "left" }}>the evidence, with its <em>error bars</em></h1>
          <p className="eval-lede">
            {report.meta.n_seeds} seeds ({report.meta.seed_label}) × {scenarios.length} scenarios, compared against{" "}
            {PLANNER_LABEL[baseline] ?? baseline}. Whiskers are {Math.round(report.meta.ci_level * 100)}% bootstrap CIs
            ({report.meta.bootstrap_resamples.toLocaleString("en-IN")} resamples).
            {newProtocol && <> Each run is 14 delivery days, then a 3-day sell-down for every planner, so leftover stock ends sold or spoiled. Destination demand is 3× the hub&apos;s supply, so &ldquo;of possible demand&rdquo; shows how much of what the stock could fill was filled.</>}
          </p>
          <div className="row" style={{ gap: 6, marginTop: 14 }}>
            {isSample
              ? <span className="badge warn">Sample report (web/sample). /eval/latest has no report yet</span>
              : <span className="badge good">Live report · {report.source_file}</span>}
            {report.meta.placeholder_data && <span className="badge warn">placeholder market data</span>}
            {report.meta.hidden && <span className="badge">hidden seed set</span>}
            <span className="badge num">run {report.meta.timestamp}</span>
          </div>
        </div>
      </section>

      <div className="wrap eval-body">
        {/* ---- per-scenario results ---- */}
        <section>
          <div className="eval-filters">
            <div className="seg" role="tablist" aria-label="Metric">
              {metrics.map((x) => (
                <button key={x.key} role="tab" aria-selected={metric === x.key} className={metric === x.key ? "on" : ""} onClick={() => setMetric(x.key)}>{x.label}</button>
              ))}
            </div>
            <div className="legend" role="group" aria-label="Planners">
              {planners.map((p) => (
                <button key={p} className={`legend-toggle ${hidden.has(p) ? "off" : ""}`} aria-pressed={!hidden.has(p)}
                  onClick={() => setHidden((h) => { const n = new Set(h); if (n.has(p)) n.delete(p); else if (shown.length > 1) n.add(p); return n; })}>
                  <span className="dot" style={{ background: seriesColor(p) }} />{PLANNER_LABEL[p] ?? p}
                </button>
              ))}
            </div>
          </div>
          <h2 className="h3 eval-h">{m.label} by scenario <span className="muted">({m.better} is better)</span></h2>
          <div className="ci-grid">
            {scenarios.map((s) => (
              <CiBars key={s} title={titleCase(s)} fmt={m.fmt} domain={domain}
                rows={shown.filter((p) => report.aggregate[s]?.[p]?.[metric]).map((p) => ({ planner: p, stat: report.aggregate[s][p][metric]! }))} />
            ))}
          </div>
        </section>

        {/* ---- win rate ---- */}
        <section>
          <h2 className="h3 eval-h">win rate vs {PLANNER_LABEL[baseline] ?? baseline}</h2>
          <p className="muted eval-note">The share of seeds where a planner beat the baseline on the same instance.</p>
          <div className="card flush">
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr><th>Scenario</th>{planners.filter((p) => p !== baseline).map((p) => <th key={p} className="r">{PLANNER_LABEL[p]}</th>)}</tr>
                </thead>
                <tbody>
                  {scenarios.map((s) => (
                    <tr key={s}>
                      <td>{titleCase(s)}</td>
                      {planners.filter((p) => p !== baseline).map((p) => {
                        const w = report.aggregate[s]?.[p]?.win_rate;
                        return (
                          <td key={p} className="r">
                            {w ? <span className="win" style={{ ["--w" as string]: w.net_revenue_inr }}>
                              <span>revenue {pct(w.net_revenue_inr, 0)}</span><span className="muted">spoilage {pct(w.spoilage_pct, 0)}</span>
                            </span> : "—"}
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </section>

        {/* ---- tornado ---- */}
        <section className="two-col">
          <div>
            <h2 className="h3 eval-h">what the result is sensitive to</h2>
            <p className="muted eval-note">
              {PLANNER_LABEL[sens.planner]} minus {PLANNER_LABEL[sens.baseline]} on <strong>{titleCase(sens.scenario)}</strong>, with each
              parameter scaled by ×{1 - sens.delta} and ×{1 + sens.delta} (n = {sens.base.n} seeds). Every bar staying on the
              same side of zero means the advantage holds.
            </p>
          </div>
          <div className="card">
            <Tornado rows={sens.tornado} base={sens.base.d_net_revenue_inr} fmt={(v) => inr(v, true)}
              lowLabel={`parameter × ${1 - sens.delta}`} highLabel={`parameter × ${1 + sens.delta}`} />
          </div>
        </section>

        {/* ---- replan vs static ---- */}
        <section>
          <h2 className="h3 eval-h">replanning vs a static plan</h2>
          <p className="muted eval-note">Disruption scenarios run twice: once replanning daily as events land, and once keeping the morning plan.</p>
          <div className="card flush">
            <div className="table-wrap">
              <table className="table">
                <thead><tr><th>Scenario</th><th>Planner</th><th className="r">Net revenue, static</th><th className="r">Replan</th><th className="r">Δ (95% CI)</th><th className="r">Spoilage Δ</th><th className="r">Replan wins</th></tr></thead>
                <tbody>
                  {Object.entries(report.replan).flatMap(([s, byP]) => PLANNER_ORDER.filter((p) => byP[p]).map((p) => {
                    const e = byP[p];
                    const d = e.replan_minus_static.net_revenue_inr;
                    return (
                      <tr key={s + p}>
                        <td>{titleCase(s)}</td>
                        <td><span className="dot" style={{ background: seriesColor(p), marginRight: 6 }} />{PLANNER_LABEL[p]}</td>
                        <td className="r">{inr(e.static.net_revenue_inr.mean, true)}</td>
                        <td className="r">{inr(e.replan.net_revenue_inr.mean, true)}</td>
                        <td className="r">{signed(d.mean, (v) => inr(v, true))} <span className="muted">({ciText(d, (v) => inr(v, true))})</span></td>
                        <td className="r">{pp(e.replan_minus_static.spoilage_pct.mean)}</td>
                        <td className="r">{pct(e.win_rate_net_revenue, 0)}{e.ties ? <span className="muted"> · ties {pct(e.ties, 0)}</span> : null}</td>
                      </tr>
                    );
                  }))}
                </tbody>
              </table>
            </div>
          </div>
        </section>

        {/* ---- festival / calendar ablation ---- */}
        <section>
          <h2 className="h3 eval-h">festival metrics and the calendar ablation</h2>
          <p className="muted eval-note">
            FreshRoute compared with the same planner without the festival calendar. Forecast WAPE is on the {report.forecast_wape.holdout_year} holdout
            ({report.forecast_wape.n_days.toLocaleString("en-IN")} days, {report.forecast_wape.n_festival_window_days.toLocaleString("en-IN")} in festival windows).
          </p>
          <div className="kpis">
            <div className="block kpi plum">
              <div className="value">{pct(report.forecast_wape.full.WAPE_festival_window)}</div>
              <div><div className="label">Festival-window WAPE with the calendar</div><div className="sub">overall {pct(report.forecast_wape.full.WAPE_overall)}</div></div>
            </div>
            <div className="block kpi pink">
              <div className="value">{pct(report.forecast_wape.no_calendar.WAPE_festival_window)}</div>
              <div><div className="label">Festival-window WAPE without the calendar</div><div className="sub">overall {pct(report.forecast_wape.no_calendar.WAPE_overall)}</div></div>
            </div>
            <div className="block kpi yellow">
              <div className="value">{pp(report.forecast_wape.full.WAPE_festival_window - report.forecast_wape.no_calendar.WAPE_festival_window, 2)}</div>
              <div><div className="label">Calendar effect on festival-window error</div><div className="sub">negative means the calendar helps</div></div>
            </div>
          </div>
          <div className="card flush" style={{ marginTop: 10 }}>
            <div className="table-wrap">
              <table className="table">
                <thead><tr><th>Scenario</th><th className="r">Festival fill: FreshRoute</th><th className="r">− calendar</th><th className="r">Post-festival spoilage: FreshRoute</th><th className="r">− calendar</th><th className="r">Net revenue Δ (calendar effect)</th></tr></thead>
                <tbody>
                  {scenarios.filter((s) => report.aggregate[s]?.freshroute?.festival_fill_rate).map((s) => {
                    const a = report.aggregate[s].freshroute, b = report.aggregate[s].ours_no_calendar;
                    if (!b) return null;
                    return (
                      <tr key={s} className={s === "festival_peak" ? "selected" : ""}>
                        <td>{titleCase(s)}</td>
                        <td className="r">{pct(a.festival_fill_rate?.mean)}</td>
                        <td className="r muted">{pct(b.festival_fill_rate?.mean)}</td>
                        <td className="r">{pct(a.post_festival_spoilage?.mean)}</td>
                        <td className="r muted">{pct(b.post_festival_spoilage?.mean)}</td>
                        <td className="r">{signed(a.net_revenue_inr.mean - b.net_revenue_inr.mean, (v) => inr(v, true))}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
        </section>
      </div>
    </div>
  );
}
