import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { CommodityPulse, DestPulse, PulseResponse, Regime } from "../api/types";
import ScenarioBar from "../components/ScenarioBar";
import { ErrorNotice, Loading, Sparkline, useTooltip } from "../components/ui";
import { useApp } from "../state/AppState";
import { day, pct, titleCase } from "../utils/format";
import "./market.css";

const RADAR_DAYS = 14;
const REGIME_TONE: Record<Regime, string> = { glut: "peri", normal: "yellow", scarcity: "red" };
const REGIME_TEXT: Record<Regime, string> = {
  glut: "Glut: prices are below the seasonal norm",
  normal: "Normal: prices are near the seasonal norm",
  scarcity: "Scarcity: prices are above the seasonal norm",
};

function ZBar({ z }: { z: number }) {
  const c = Math.max(-3, Math.min(3, z));
  const w = (Math.abs(c) / 3) * 50;
  return (
    <span className="zbar" aria-label={`z-score ${z.toFixed(2)}`}>
      <span className="zbar-mid" />
      <span className={`zbar-fill ${c >= 0 ? "pos" : "neg"}`} style={c >= 0 ? { left: "50%", width: `${w}%` } : { right: "50%", width: `${w}%` }} />
    </span>
  );
}

function Momentum({ c }: { c: CommodityPulse }) {
  const sym = c.direction === "up" ? "↑" : c.direction === "down" ? "↓" : "→";
  return <span className={`mom ${c.direction}`}>{sym} {c.momentum_pct > 0 ? "+" : ""}{c.momentum_pct.toFixed(1)}%/wk</span>;
}

function DestCard({ d }: { d: DestPulse }) {
  const tip = useTooltip();
  const radar = d.festival_radar.filter((r) => r.days_until <= RADAR_DAYS);
  return (
    <article className="card pulse-card">
      <header className="pulse-head">
        <div>
          <h2 className="h3">{d.name}</h2>
          <span className="muted" style={{ fontSize: 13 }}>{d.region} · {d.dest_id}</span>
        </div>
        <span className={`block regime ${REGIME_TONE[d.regime_flag]}`} title={REGIME_TEXT[d.regime_flag]}>{d.regime_flag}</span>
      </header>

      <div className={`radar ${radar.length ? "on" : ""}`}>
        <span className="eyebrow" style={{ margin: 0 }}>Festival radar · next {RADAR_DAYS} days</span>
        {radar.length === 0 ? (
          <span className="muted" style={{ fontSize: 13 }}>No festivals in the window.</span>
        ) : radar.map((r) => (
          <div key={r.festival + r.date} className="radar-item">
            <span className="radar-days num">{r.days_until === 0 ? "today" : `in ${r.days_until} d`}</span>
            <span><strong>{titleCase(r.festival)}</strong> <span className="muted">{day(r.date)}</span></span>
            <span className="radar-up">
              {r.uplifts.length === 0 ? "No fitted effect for this region" : r.uplifts.map((u) => (
                <span key={u.commodity_id}>{u.commodity_id}: demand {u.peak_fdi >= 0 ? "+" : ""}{pct(u.peak_fdi, 0)}, price {u.peak_pu >= 0 ? "+" : ""}{pct(u.peak_pu, 0)}</span>
              ))}
            </span>
          </div>
        ))}
      </div>

      <div className="table-wrap">
        <table className="table pulse-table">
          <thead>
            <tr><th>Commodity</th><th className="r">Price</th><th className="r">Norm</th><th>vs norm (z)</th><th>Momentum</th><th>Trend</th></tr>
          </thead>
          <tbody>
            {d.commodities.map((c) => {
              const series = [...c.history, ...c.forecast.map((f) => f.price_per_kg)];
              return (
                <tr key={c.commodity_id}>
                  <td>{c.commodity_id}</td>
                  <td className="r">₹{c.price_obs.toFixed(1)}/kg</td>
                  <td className="r muted">₹{c.norm.toFixed(1)}</td>
                  <td><span className="row" style={{ gap: 8, flexWrap: "nowrap" }}><ZBar z={c.z} /><span className="num">{c.z >= 0 ? "+" : ""}{c.z.toFixed(2)}</span></span></td>
                  <td><Momentum c={c} /></td>
                  <td
                    onMouseMove={(e) => tip.show(e, <>
                      <strong>{c.commodity_id}</strong> at {d.name}<br />
                      Observed: {c.history.map((v) => `₹${v.toFixed(0)}`).join(", ")}<br />
                      Forecast: {c.forecast.map((f) => `${day(f.date).split(" ").slice(0, 2).join(" ")} ₹${f.price_per_kg.toFixed(0)} ±${f.price_sd.toFixed(0)}`).join(", ")}
                    </>)}
                    onMouseLeave={tip.hide}
                  >
                    <Sparkline width={84} values={series} split={c.history.length} label={`${c.commodity_id} price history and forecast`} />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {tip.node}
    </article>
  );
}

export default function MarketPulse() {
  const { settings, state } = useApp();
  const [data, setData] = useState<PulseResponse | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    let live = true;
    setLoading(true);
    setErr(null);
    api.pulse({ scenario_id: settings.scenarioId, seed: settings.seed, date: state?.date })
      .then((r) => live && setData(r))
      .catch((e) => live && setErr((e as Error).message))
      .finally(() => live && setLoading(false));
    return () => { live = false; };
  }, [settings.scenarioId, settings.seed, state?.date]);

  const withFestival = data?.destinations.filter((d) => d.festival_radar.some((r) => r.days_until <= RADAR_DAYS)).length ?? 0;

  return (
    <div className="page wrap">
      <header className="page-head">
        <p className="eyebrow">Market pulse</p>
        <h1 className="h2">what each market is <em>about to do</em></h1>
        <p>
          For each destination: today's price against its seasonal norm (z-score), the 7-day momentum, festivals
          in the next {RADAR_DAYS} days and a regime flag. Prices come from the fitted market model. There is no live mandi feed.
        </p>
      </header>
      <ScenarioBar action="load" />
      {err && <div style={{ marginTop: 12 }}><ErrorNotice message={err} /></div>}
      {loading && <div style={{ marginTop: 12 }}><Loading label="Reading the markets…" /></div>}

      {data && (
        <>
          <div className="row pulse-meta">
            <span className="badge num">as of {day(data.date)}, observed through {day(data.observed_through)}</span>
            {data.scenario_regime && <span className="badge warn">scenario regime: {data.scenario_regime}</span>}
            <span className="badge">{withFestival} of {data.destinations.length} destinations have a festival ahead</span>
            <div className="spacer" />
            <div className="legend">
              <span><span className="dot" style={{ background: "var(--amber)" }} />above norm</span>
              <span><span className="dot" style={{ background: "var(--s-fefo_greedy)" }} />below norm</span>
              <span><span className="dot" style={{ background: "var(--s-freshroute)" }} />forecast (dashed)</span>
            </div>
          </div>
          <div className="pulse-grid">
            {data.destinations.map((d) => <DestCard key={d.dest_id} d={d} />)}
          </div>
        </>
      )}
    </div>
  );
}
