import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api } from "../api/client";
import type { ForecastMode, InstanceResponse, PlanResponse, ScenariosResponse, State, StockResponse } from "../api/types";

export interface Settings {
  scenarioId: string;
  seed: number;
  riskP: number;
  forecastMode: ForecastMode;
}

const DEFAULTS: Settings = { scenarioId: "festival_peak", seed: 51, riskP: 0.9, forecastMode: "full" };

function readSettings(): Settings {
  try {
    const raw = localStorage.getItem("fr.settings");
    return raw ? { ...DEFAULTS, ...JSON.parse(raw) } : DEFAULTS;
  } catch {
    return DEFAULTS;
  }
}

interface AppStateValue {
  settings: Settings;
  updateSettings: (patch: Partial<Settings>) => void;
  scenarios: ScenariosResponse | null;
  instance: InstanceResponse | null;
  /** The state being planned — day 0, or the post-event state once a what-if is applied. */
  state: State | null;
  ours: PlanResponse | null;
  baseline: PlanResponse | null;
  /** Where `state` came from: a generated scenario day, or the seller's own stock list. */
  source: "scenario" | "stock";
  stock: StockResponse | null;
  busy: "instance" | "plan" | null;
  error: string | null;
  loadInstance: () => Promise<State | null>;
  generatePlan: () => Promise<void>;
  /** Make the seller's stock the current state and plan it (FreshRoute + B1). */
  planStock: (stock: StockResponse) => Promise<void>;
  /** Adopt a what-if outcome as the current state/plan. */
  adopt: (state: State, ours: PlanResponse) => void;
  clearError: () => void;
}

const Ctx = createContext<AppStateValue | null>(null);

export function AppStateProvider({ children }: { children: ReactNode }) {
  const [settings, setSettings] = useState<Settings>(readSettings);
  const [scenarios, setScenarios] = useState<ScenariosResponse | null>(null);
  const [instance, setInstance] = useState<InstanceResponse | null>(null);
  const [state, setState] = useState<State | null>(null);
  const [ours, setOurs] = useState<PlanResponse | null>(null);
  const [baseline, setBaseline] = useState<PlanResponse | null>(null);
  const [source, setSource] = useState<AppStateValue["source"]>("scenario");
  const [stock, setStock] = useState<StockResponse | null>(null);
  const [busy, setBusy] = useState<AppStateValue["busy"]>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    try {
      localStorage.setItem("fr.settings", JSON.stringify(settings));
    } catch {
      /* private mode: settings just won't persist */
    }
  }, [settings]);

  useEffect(() => {
    api.scenarios().then(setScenarios).catch(() => setScenarios(null));
  }, []);

  const updateSettings = useCallback((patch: Partial<Settings>) => {
    const s = settings;
    const next = { ...s, ...patch };
    // A different instance or forecast invalidates what's loaded; a new risk appetite, the plans.
    const reload = next.scenarioId !== s.scenarioId || next.seed !== s.seed || next.forecastMode !== s.forecastMode;
    // (your own stock is kept on the My stock page; plan it again from there)
    if (reload) {
      setSource("scenario");
      setStock(null);
      setInstance(null);
      setState(null);
      setOurs(null);
      setBaseline(null);
    } else if (next.riskP !== s.riskP) {
      setOurs(null);
      setBaseline(null);
    }
    setSettings(next);
  }, [settings]);

  const loadInstance = useCallback(async () => {
    setBusy("instance");
    setError(null);
    try {
      const inst = await api.instance({
        scenario_id: settings.scenarioId, seed: settings.seed,
        forecast_mode: settings.forecastMode, risk_p: settings.riskP,
      });
      setInstance(inst);
      setSource("scenario");
      setStock(null);
      setState(inst.state);
      setOurs(null);
      setBaseline(null);
      return inst.state;
    } catch (e) {
      setError((e as Error).message);
      return null;
    } finally {
      setBusy(null);
    }
  }, [settings]);

  const planBoth = useCallback(async (s: State) => {
    setBusy("plan");
    setError(null);
    try {
      const [a, b] = await Promise.all([
        api.plan({ state: s, planner: "freshroute", risk_p: settings.riskP }),
        api.plan({ state: s, planner: "fifo_nearest", risk_p: settings.riskP }),
      ]);
      setOurs(a);
      setBaseline(b);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }, [settings.riskP]);

  const generatePlan = useCallback(async () => {
    const s = state ?? (source === "scenario" ? await loadInstance() : null);
    if (s) await planBoth(s);
  }, [state, source, loadInstance, planBoth]);

  const planStock = useCallback(async (st: StockResponse) => {
    setSource("stock");
    setStock(st);
    setInstance(null);
    setState(st.state);
    setOurs(null);
    setBaseline(null);
    await planBoth(st.state);
  }, [planBoth]);

  const adopt = useCallback((s: State, p: PlanResponse) => {
    setState(s);
    setOurs(p);
    setBaseline(null); // B1 was planned on the old state; re-run to compare
  }, []);

  const value = useMemo<AppStateValue>(() => ({
    settings, updateSettings, scenarios, instance, state, ours, baseline, source, stock, busy, error,
    loadInstance, generatePlan, planStock, adopt, clearError: () => setError(null),
  }), [settings, updateSettings, scenarios, instance, state, ours, baseline, source, stock, busy, error, loadInstance, generatePlan, planStock, adopt]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useApp(): AppStateValue {
  const v = useContext(Ctx);
  if (!v) throw new Error("useApp must be used inside <AppStateProvider>");
  return v;
}
