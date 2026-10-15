# FreshRoute web UI — frontend brief

## Frontend layout (built)

React 18 + TypeScript + Vite, react-router, react-leaflet. No CSS framework: design tokens
(light + dark) live in `src/styles/tokens.css`, shared components in `src/styles/base.css`.

```
npm install          # once (python tasks.py setup also does it)
npm run dev          # http://localhost:5173 — regenerates src/data/*.json from config/ first
npm run build        # typecheck + production bundle in dist/
npm run types        # regenerate src/types/freshroute.ts from the JSON Schema
```

| path | what |
|---|---|
| `src/pages/*.tsx` | one file (+ its `.css`) per page: Landing, TodayPlan, MapPage, MarketPulse, WhatIf, Evaluation, Settings, Sources |
| `src/landing/` | landing sections: Hero, StatTiles, HowItWorks, Reviews, BottomSheet |
| `src/state/AppState.tsx` | settings (scenario, seed, risk_p, forecast mode; saved in localStorage), loaded State, our plan + B1 plan |
| `src/api/` | typed client for every endpoint (`VITE_API_BASE` overrides `http://localhost:8000`) |
| `scripts/gen-sources.mjs` | `config/*.yaml` → `src/data/sources.json` (Assumptions & sources page) and `network.json` |

- Reviews on the landing page are real submissions only: `GET/POST /reviews`, stored in `var/reviews.jsonl`.
- "How it works" plays `public/media/how-it-works.mp4` when present (git-ignored), else an animated walkthrough.
- Evaluation uses `/eval/latest` and falls back to `sample/eval_latest.json` (badged as a sample).

The backend is done; this folder is where the frontend lives (Vite dev server on
`localhost:5173`, already allowed by the API's CORS config).

## Run the backend

```
python tasks.py setup     # once: venv + Python deps
python tasks.py api       # FastAPI on http://localhost:8000 (auto-reload)
```

Interactive docs with every request/response shape: http://localhost:8000/docs.
TypeScript-ready JSON Schema for State / Plan / RunResult: `web/src/types/freshroute.schema.json`
(regenerate with `python tasks.py schemas`). Once `web/package.json` exists, `python tasks.py web`
runs `npm run dev`.

## Endpoints

| method | path | use |
|---|---|---|
| GET | `/health` | liveness, planner names |
| GET | `/scenarios` | 11 scenarios (id, description, start date, days, events) + seed ranges |
| POST | `/instance` | `{scenario_id, seed, start_date?, forecast_mode?, risk_p?}` → day-0 `state` (+ announced events, fingerprint) |
| POST | `/plan` | `{state, planner?, risk_p?}` → `{plan, summary}` |
| POST | `/whatif` | `{state, event: {type, params}, previous_plan?, planner?}` → `{state, plan, previous_plan, diff}` |
| POST | `/simulate` | `{scenario_id, seed, planners[], days?, replan?}` → per-planner metrics + daily results |
| GET | `/eval/latest` | latest evaluation report (aggregates, replan, sensitivity) |
| GET | `/market/pulse` | `?scenario_id&seed&date&dest_id` → per destination: price vs norm, momentum, festival radar, regime |

Notes:
- Planners: `freshroute` (ours), `ours_no_calendar` (ablation), `fefo_greedy` (B3),
  `chase_price` (B2), `fifo_nearest` (B1, today's practice).
- Seeds ≥ 1000 are the locked hidden set → 403. Use 51 (validation range) for the demo.
- What-if event types: `lane_closure {dest_id}`, `vehicle_down {vehicle_id}`,
  `demand_spike {dest_id, commodity_id?, mult, duration_days}`,
  `price_shock {dest_id, commodity_id?, mult}`, `new_batches {batches: [{commodity_id, qty_kg, grade?}]}`.
  Pass the plan you are showing as `previous_plan` so the diff is against it.
- Vehicles: `A1`–`A6` ambient, `R1`/`R2` reefers. Destinations: `blr_kr`, `blr_dc`, `mysuru`,
  `chennai_koyambedu`, `hyd_bowenpally`, `hubballi`, `malur_proc`; hub `kolar_hub`.
  Destination coordinates are in `state.destinations[].lat/lon`; the hub is at
  13.1360, 78.1290 (`config/network.yaml`; the state only carries `hub_id`).
- Every plan carries `violations` (should be `[]` for ours), `reasons[batch_id]` (decision,
  runner-up, drivers) and `solver_meta` (incl. `fallback_used`).
- Money is ₹, weights kg, quality 0–1 (show as %).

## Pages

1. **Today's plan** — KPI cards from `summary` (expected net value ₹, fill %, held/shipped kg,
   a "0 violations" badge), plan table (batch → destination / vehicle / discount / arrival
   quality), click a row → "why" card from `plan.reasons`. Compare against B1 by calling
   `/plan` with `planner: "fifo_nearest"` on the same state.
2. **Map** (Leaflet) — hub → destinations; line thickness = kg shipped; colour = mean arrival
   quality; vehicle icons.
3. **Market pulse** — per destination: price vs seasonal norm (z-score), momentum arrow,
   festival radar (next 14 days), regime flag, small forecast sparkline.
4. **What-if** — buttons: close lane, break vehicle, spike demand, price crash, add batches →
   show the new plan, the diff (`diff.changes`: moved batches, changed discounts) and
   `diff.summary_delta` as KPI deltas.
5. **Evaluation** — per-scenario bars with CI whiskers (ours vs B1/B2/B3/ablation), win rate,
   tornado chart, replan vs static, festival metrics.
6. **Settings** — risk appetite slider (`risk_p` 0.80–0.97), forecast mode
   (`full` / `no_calendar` / `flat`).

Design: clean and data-dense, light and dark themes, units on every number (₹, kg, %),
no lorem ipsum. Include an "Assumptions & sources" panel (sources are listed in each
`config/*.yaml` `sources` field).

## Demo flow the UI must support

1. Load `festival_peak`, seed 51, day 0 (Kolar hub, 6 ambient trucks + 2 reefers).
2. Generate the plan → KPI cards vs B1 → open a "why" card.
3. Market pulse: the festival radar shows an upcoming festival at a destination.
4. What-if: "Reefer R1 breaks down" → replan → diff → KPIs update, still 0 violations.
5. Evaluation page: results with CIs, win rate, tornado, calendar ablation
   (`freshroute` vs `ours_no_calendar`).

## Known gaps

- `/plan` with our planner takes ~3 s (what-if 2–4 s) — show a spinner.
- `festival_peak` day 0 has ~43 batches.
- `reports/` is not in git, so on a fresh clone `/eval/latest` returns 404. Build the
  Evaluation page against `web/sample/eval_latest.json` (same shape the endpoint returns,
  from an earlier full run). Final numbers will land in `reports/final/`, which the API
  then serves automatically.
- No live mandi price feed; all prices come from the fitted market model.
