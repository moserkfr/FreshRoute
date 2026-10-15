import { useApp } from "../state/AppState";
import { day, kg, titleCase } from "../utils/format";
import { PillButton } from "./ui";

/** Scenario + seed picker with the load / plan actions — shared by the planning pages. */
export default function ScenarioBar({ action = "plan" }: { action?: "plan" | "load" }) {
  const { settings, updateSettings, scenarios, instance, state, busy, loadInstance, generatePlan, source, stock } = useApp();
  const val = scenarios?.seeds.validation;
  const hiddenMin = scenarios?.seeds.hidden_min ?? 1000;
  const current = scenarios?.scenarios.find((s) => s.id === settings.scenarioId);

  if (source === "stock" && stock) {
    const planned = stock.rows.filter((r) => r.planned);
    return (
      <div className="card scenario-bar">
        <div className="row" style={{ alignItems: "center" }}>
          <span className="badge good">Your stock</span>
          <span className="num" style={{ fontSize: 14 }}>
            {day(stock.plan_date)} · {planned.length} batch{planned.length === 1 ? "" : "es"} · {kg(planned.reduce((n, r) => n + r.qty_kg, 0))}
          </span>
          <div className="spacer" />
          <PillButton tone="ghost" to="/stock">Edit stock</PillButton>
          <PillButton tone="ghost" onClick={loadInstance} busy={busy === "instance"}>Back to scenarios</PillButton>
          {action === "plan" && (
            <PillButton tone="amber" onClick={generatePlan} busy={busy !== null}>{busy === "plan" ? "Planning…" : "Replan"}</PillButton>
          )}
        </div>
        <p className="muted" style={{ margin: "10px 0 0", fontSize: 13 }}>
          Trucks, markets and price forecasts come from the {stock.base}.
          {stock.excluded_kg > 0 && <> {kg(stock.excluded_kg)} already past saleable is left out.</>}
        </p>
      </div>
    );
  }

  return (
    <div className="card scenario-bar">
      <div className="row" style={{ alignItems: "flex-end" }}>
        <label className="field" style={{ minWidth: 200, flex: "1 1 200px" }}>
          Scenario
          <select className="select" value={settings.scenarioId} onChange={(e) => updateSettings({ scenarioId: e.target.value })}>
            {(scenarios?.scenarios ?? [{ id: settings.scenarioId }]).map((s) => (
              <option key={s.id} value={s.id}>{titleCase(s.id)}</option>
            ))}
          </select>
        </label>
        <label className="field" style={{ width: 120 }}>
          Seed
          <input
            className="input num" type="number" min={0} max={hiddenMin - 1} value={settings.seed}
            onChange={(e) => {
              const n = Number(e.target.value);
              if (Number.isFinite(n) && n >= 0 && n < hiddenMin) updateSettings({ seed: Math.round(n) });
            }}
          />
        </label>
        <div className="spacer" />
        {action === "plan" ? (
          <PillButton tone="amber" onClick={generatePlan} busy={busy !== null}>
            {busy === "plan" ? "Planning…" : busy === "instance" ? "Loading day 0…" : "Generate plan"}
          </PillButton>
        ) : (
          <PillButton tone="amber" onClick={loadInstance} busy={busy === "instance"}>
            {state ? "Reload day 0" : "Load day 0"}
          </PillButton>
        )}
      </div>
      <p className="muted" style={{ margin: "10px 0 0", fontSize: 13 }}>
        {current?.description ?? "Pick a scenario."}{" "}
        {val && <>Validation seeds {val[0]}–{val[1]}; seeds ≥ {hiddenMin} are locked.</>}
        {instance && state && (
          <> · Loaded <span className="num">{day(state.date)}</span>, {state.batches.length} batches,{" "}
            {state.vehicles.filter((v) => v.available !== false).length}/{state.vehicles.length} vehicles available,{" "}
            ambient {state.ambient_temp_c.toFixed(1)} °C.</>
        )}
      </p>
    </div>
  );
}
