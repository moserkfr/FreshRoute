import type { Plan, State } from "../types/freshroute";
import type {
  EvalReport, ForecastMode, Health, InstanceResponse, PlannerName, PlanResponse, PulseResponse,
  Review, ScenariosResponse, StockBatchIn, StockResponse, WhatIfEvent, WhatIfResponse,
} from "./types";

export const API_BASE: string = import.meta.env.VITE_API_BASE ?? "http://localhost:8000";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function request<T>(path: string, init?: { method?: string; json?: unknown }): Promise<T> {
  let res: Response;
  try {
    res = await fetch(API_BASE + path, {
      method: init?.method ?? "GET",
      headers: init?.json !== undefined ? { "Content-Type": "application/json" } : undefined,
      body: init?.json !== undefined ? JSON.stringify(init.json) : undefined,
    });
  } catch {
    throw new ApiError(0, `Can't reach the API at ${API_BASE}. Start it with "python tasks.py api".`);
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail ?? body);
    } catch {
      /* keep statusText */
    }
    throw new ApiError(res.status, detail);
  }
  return res.json() as Promise<T>;
}

export const api = {
  health: () => request<Health>("/health"),
  scenarios: () => request<ScenariosResponse>("/scenarios"),
  instance: (body: { scenario_id: string; seed: number; forecast_mode?: ForecastMode; risk_p?: number | null }) =>
    request<InstanceResponse>("/instance", { method: "POST", json: body }),
  plan: (body: { state: State; planner?: PlannerName; risk_p?: number | null }) =>
    request<PlanResponse>("/plan", { method: "POST", json: body }),
  whatif: (body: { state: State; event: WhatIfEvent; previous_plan?: Plan | null; planner?: PlannerName; risk_p?: number | null }) =>
    request<WhatIfResponse>("/whatif", { method: "POST", json: body }),
  stockState: (body: { batches: StockBatchIn[]; plan_date?: string | null; forecast_mode?: ForecastMode; risk_p?: number | null }) =>
    request<StockResponse>("/stock/state", { method: "POST", json: body }),
  evalLatest: () => request<EvalReport>("/eval/latest"),
  pulse: (q: { scenario_id: string; seed: number; date?: string; dest_id?: string; own_day?: boolean }) => {
    const p = new URLSearchParams({ scenario_id: q.scenario_id, seed: String(q.seed) });
    if (q.date) p.set("date", q.date);
    if (q.own_day) p.set("own_day", "true");
    if (q.dest_id) p.set("dest_id", q.dest_id);
    return request<PulseResponse>(`/market/pulse?${p}`);
  },
  reviews: () => request<Review[]>("/reviews"),
  addReview: (body: { name: string; role: string; rating: number; text: string }) =>
    request<Review>("/reviews", { method: "POST", json: body }),
};
