import { useEffect, useState } from "react";
import { api, API_BASE } from "../api/client";
import type { ForecastMode, Health } from "../api/types";
import { CheckIcon, PillButton, WarnIcon } from "../components/ui";
import { useApp } from "../state/AppState";
import "./settings.css";

const MODES: { id: ForecastMode; title: string; text: string; tone: string }[] = [
  { id: "full", title: "full", text: "Seasonal and festival-calendar market forecast. This is the default.", tone: "yellow" },
  { id: "no_calendar", title: "no calendar", text: "Same model without the festival calendar. This is the ablation.", tone: "peri" },
  { id: "flat", title: "flat", text: "Flat price and demand, with no seasonality or festivals.", tone: "pink" },
];

export default function Settings() {
  const { settings, updateSettings } = useApp();
  const [health, setHealth] = useState<Health | null>(null);
  const [healthErr, setHealthErr] = useState<string | null>(null);
  const [risk, setRisk] = useState(settings.riskP);

  useEffect(() => {
    api.health().then(setHealth).catch((e) => setHealthErr((e as Error).message));
  }, []);

  return (
    <div className="page wrap">
      <header className="page-head">
        <p className="eyebrow">Settings</p>
        <h1 className="h2">how cautious should the <em>planner</em> be?</h1>
        <p>These apply to the next instance you load and the next plan you generate, on every page. They're saved in this browser.</p>
      </header>

      <div className="settings-grid">
        <section className="card settings-card">
          <h2 className="h3">risk appetite</h2>
          <p className="muted">
            The chance constraint: the probability that each shipment arrives above the buyer's quality floor. A higher value
            ships less marginal stock but keeps a tighter safety margin.
          </p>
          <div className="risk-value num">{Math.round(risk * 100)}%</div>
          <input
            type="range" min={0.8} max={0.97} step={0.01} value={risk} className="risk-range"
            onChange={(e) => setRisk(Number(e.target.value))}
            onPointerUp={() => updateSettings({ riskP: risk })}
            onKeyUp={() => updateSettings({ riskP: risk })}
            aria-label="Risk appetite, chance-constraint probability"
          />
          <div className="risk-scale"><span>80%: bolder</span><span>97%: safer</span></div>
          {risk !== settings.riskP && <p className="muted" style={{ fontSize: 13 }}>Release the slider to apply it.</p>}
        </section>

        <section className="card settings-card">
          <h2 className="h3">forecast mode</h2>
          <p className="muted">Which market forecast the planner sees. Changing it reloads the instance.</p>
          <div className="mode-grid" role="radiogroup" aria-label="Forecast mode">
            {MODES.map((m) => (
              <button key={m.id} role="radio" aria-checked={settings.forecastMode === m.id}
                className={`block mode ${m.tone} ${settings.forecastMode === m.id ? "on" : ""}`}
                onClick={() => updateSettings({ forecastMode: m.id })}>
                <span className="mode-title">{m.title}</span>
                <span className="mode-text">{m.text}</span>
              </button>
            ))}
          </div>
        </section>

        <section className="card settings-card">
          <h2 className="h3">backend</h2>
          <p className="muted num">{API_BASE}</p>
          {health ? (
            <>
              <p className="badge good" style={{ width: "fit-content" }}><CheckIcon /> API online · v{health.version}</p>
              <p className="muted" style={{ fontSize: 13 }}>Planners: {health.planners.join(", ")}</p>
              {health.placeholder_data.length > 0
                ? <p className="badge warn" style={{ width: "fit-content" }}><WarnIcon /> Placeholder market data: {health.placeholder_data.join(", ")}</p>
                : <p className="muted" style={{ fontSize: 13 }}>Market model fitted on real data. No placeholder files.</p>}
            </>
          ) : healthErr ? (
            <p className="badge bad" style={{ width: "fit-content", whiteSpace: "normal" }}><WarnIcon /> {healthErr}</p>
          ) : <p className="muted">Checking…</p>}
          <div className="row">
            <PillButton tone="ghost" to="/sources">Assumptions &amp; sources</PillButton>
          </div>
        </section>
      </div>
    </div>
  );
}
