// Response shapes for the endpoints whose models live in api/main.py, whatif.py and pulse.py
// (State / Plan / RunResult come from the generated schema in ../types/freshroute.ts).
import type { Plan, State } from "../types/freshroute";

export type PlannerName = "freshroute" | "ours_no_calendar" | "fefo_greedy" | "chase_price" | "fifo_nearest";
export type ForecastMode = "full" | "no_calendar" | "flat";

export interface Health { status: "ok"; version: string; planners: string[]; placeholder_data: string[] }

export interface ScenarioInfo { id: string; description: string; start_date: string; days: number; events: Record<string, unknown>[] }
export interface ScenariosResponse {
  scenarios: ScenarioInfo[];
  seeds: { tuning: [number, number]; validation: [number, number]; hidden_min: number };
}

export interface InstanceResponse {
  scenario_id: string; seed: number; start_date: string; days: number; fingerprint: string;
  announced_events: Record<string, unknown>[]; placeholder_data: string[]; state: State;
}

export interface PlanSummary {
  stock_kg: number; shipped_kg: number; held_kg: number; discounted_kg: number; trips: number;
  freight_inr: number; expected_net_value_inr: number; expected_fill_pct: number;
  mean_arrival_quality: number | null; violations: number; solve_time_s: number; fallback_used: boolean;
}
export interface PlanResponse { plan: Plan; summary: PlanSummary }

export interface Leg { dest_id: string; vehicle_id: string; qty_kg: number; discount_pct: number }
export interface BatchChange { batch_id: string; commodity_id: string; change: string; before: Leg[]; after: Leg[]; text: string }
export interface PlanDiff {
  changes: BatchChange[];
  vehicles_before: Record<string, string>; vehicles_after: Record<string, string>;
  summary_before: PlanSummary; summary_after: PlanSummary;
  summary_delta: Record<string, number>;
}

export type WhatIfEvent =
  | { type: "lane_closure"; params: { dest_id: string } }
  | { type: "vehicle_down"; params: { vehicle_id: string } }
  | { type: "demand_spike"; params: { dest_id: string; commodity_id?: string | null; mult: number; duration_days: number } }
  | { type: "price_shock"; params: { dest_id: string; commodity_id?: string | null; mult: number } }
  | { type: "new_batches"; params: { batches: { commodity_id: string; qty_kg: number; grade?: "A" | "B" | "C" }[] } };
export interface WhatIfResponse { state: State; plan: Plan; previous_plan: Plan; diff: PlanDiff }

export type Regime = "glut" | "normal" | "scarcity";
export interface ForecastPoint { date: string; price_per_kg: number; price_sd: number; demand_kg: number; fdi: number }
export interface CommodityPulse {
  commodity_id: string; price_obs: number; norm: number; z: number; momentum_pct: number;
  direction: "up" | "down" | "flat"; regime_flag: Regime; history: number[]; forecast: ForecastPoint[];
}
export interface RadarItem {
  festival: string; date: string; days_until: number; type: string;
  uplifts: { commodity_id: string; peak_fdi: number; peak_pu: number }[];
}
export interface DestPulse { dest_id: string; name: string; region: string; regime_flag: Regime; commodities: CommodityPulse[]; festival_radar: RadarItem[] }
export interface PulseResponse {
  scenario_id: string; seed: number; date: string; observed_through: string;
  scenario_regime: string | null; destinations: DestPulse[];
}

// /eval/latest
export interface Stat { mean: number; ci_lo: number; ci_hi: number; sd: number; n: number }
export type CoreMetric = "spoilage_pct" | "fulfilment_pct" | "net_revenue_inr" | "violations_total";
export interface PlannerAggregate {
  spoilage_pct: Stat; fulfilment_pct: Stat; net_revenue_inr: Stat; violations_total: Stat;
  fulfilment_min_dest?: Stat; spoilage_pct_worst?: Stat; held_end_kg?: Stat;
  festival_fill_rate?: Stat; post_festival_spoilage?: Stat;
  violations_by_type?: Record<string, number>; plan_time_max_s?: number;
  vs_baseline?: Record<CoreMetric, Stat>;
  win_rate?: Record<string, number>;
}
export interface ReplanEntry {
  static: Record<CoreMetric, Stat>; replan: Record<CoreMetric, Stat>; replan_minus_static: Record<CoreMetric, Stat>;
  win_rate_net_revenue: number; ties: number;
}
export interface SensPoint { d_net_revenue_inr: number; d_spoilage_pct: number; n: number }
export interface TornadoRow { param: string; low_factor: number; high_factor: number; low: SensPoint; high: SensPoint; swing_net_revenue_inr: number }
export interface EvalReport {
  meta: {
    timestamp: string; mode: string; scenarios: string[]; planners: string[]; seeds: number[];
    seed_label: string; n_seeds: number; hidden: boolean; baseline: string; ci_level: number;
    bootstrap_resamples: number; placeholder_data: boolean;
  };
  forecast_wape: {
    full: { WAPE_overall: number; WAPE_festival_window: number };
    no_calendar: { WAPE_overall: number; WAPE_festival_window: number };
    n_days: number; n_festival_window_days: number; holdout_year: number;
  };
  aggregate: Record<string, Record<string, PlannerAggregate>>;
  replan: Record<string, Record<string, ReplanEntry>>;
  sensitivity: { scenario: string; planner: string; baseline: string; delta: number; base: SensPoint; tornado: TornadoRow[] };
  source_file?: string;
}

export interface Review { id: string; name: string; role: string; rating: number; text: string; created_at: string }

export type { Plan, State };
