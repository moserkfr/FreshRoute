# Frontend status — handoff for backend work

State of the web UI as of 2026-10-15 (commit `00b138f`), and what the backend must keep
providing for it. The full API brief is in `web/README.md`; this file covers what was
built and what to watch when changing the backend.

## Set up on a new machine

```
git clone https://github.com/moserkfr/FreshRoute && cd FreshRoute
python tasks.py setup      # venv + Python deps (+ npm install in web/)
python tasks.py api        # FastAPI on http://localhost:8000 (auto-reload), docs at /docs
cd web && npm install      # only if setup didn't already do it
npm run dev                # http://localhost:5173
```

- `npm run dev` / `npm run build` first run `scripts/gen-sources.mjs`, which reads
  `config/*.yaml` and writes `web/src/data/network.json` and `sources.json`. Both are
  git-ignored, so on a fresh clone the landing page and map won't load until one of these
  commands has run once.
- No API keys are needed anywhere (backend data sources and map tiles are all public).
- If the page goes blank after adding or removing an npm package, restart Vite with
  `npx vite --force` (stale dependency cache), then hard-refresh the browser.

## What's built

React 18 + TypeScript + Vite, react-router, react-leaflet, no CSS framework.

| route | file | backend calls |
|---|---|---|
| `/` | `pages/Landing.tsx` + `landing/*` (Hero globe, StatTiles, HowItWorks, Reviews, BottomSheet) | `/eval/latest`, `GET/POST /reviews` |
| `/plan` | `pages/TodayPlan.tsx` | `/instance`, `/plan` ×2 (`freshroute` + `fifo_nearest` for the B1 comparison) |
| `/map` | `pages/MapPage.tsx` | uses the loaded state + plan |
| `/market` | `pages/MarketPulse.tsx` | `/market/pulse` |
| `/whatif` | `pages/WhatIf.tsx` | `/whatif` |
| `/evaluation` | `pages/Evaluation.tsx` | `/eval/latest` (falls back to `web/sample/eval_latest.json`, shown with a "sample" badge) |
| `/settings` | `pages/Settings.tsx` | none (scenario, seed, `risk_p`, forecast mode, saved in localStorage) |
| `/sources` | `pages/Sources.tsx` | none (reads generated `sources.json`) |

Shared state lives in `src/state/AppState.tsx`; every request goes through `src/api/client.ts`.
Defaults: scenario `festival_peak`, seed `51`, `risk_p` 0.9, forecast mode `full`.

## Contract the frontend relies on

Changing any of these on the backend means updating `web/src/api/types.ts` and/or
`web/src/types/freshroute.ts` (regenerate the latter with `python tasks.py schemas`, then
`npm run types` in `web/`).

- Base URL `http://localhost:8000` (override with `VITE_API_BASE` in `web/.env`). CORS must
  allow `http://localhost:5173` (`config/default.yaml` → `api.cors_origins`).
- Errors: non-2xx with a JSON `detail` string; the UI shows `detail` as-is.
- `/scenarios` → `seeds.validation` and `seeds.hidden_min` drive the seed input (≥ 1000 → 403).
- `/instance` → `state.destinations[].lat/lon`, `state.vehicles[].type` (`reefer` is styled
  differently), `state.lanes[].open === false` marks closed lanes.
- `/plan` → `plan.allocations[]` (`dest_id`, `vehicle_id`, `qty_kg`, `arrival_quality_mean`),
  `plan.reasons[batch_id]`, `summary.violations`.
- `/whatif` → `diff.changes` and `diff.summary_delta`.
- `/reviews` → `GET` newest first; `POST {name, role, rating 1–5, text ≥ 10 chars}` → 201.
  Stored in `var/reviews.jsonl` (git-ignored, so reviews don't move between machines).
- `/eval/latest` → same shape as `web/sample/eval_latest.json` (`meta.n_seeds`,
  `meta.seed_label`, `meta.scenarios`, `meta.ci_level`, aggregates, sensitivity). `reports/`
  is git-ignored, so a fresh clone returns 404 until `python tasks.py eval` has run.
- The hub isn't in the state: its coordinates come from `config/network.yaml` via the
  generated `network.json`. Moving the hub means editing the YAML, not the API.

## Decisions made during the build

- **Map tiles:** CARTO basemaps now return an "API key required" image, so the map uses
  keyless OpenStreetMap tiles plus an Esri satellite option. A MapLibre vector map was
  tried for sharpness but reverted because it felt slower. Raster tiles look slightly soft
  on high-DPI screens; that's the known trade-off.
- **Stacking order:** z-index values are tokens in `src/styles/tokens.css`
  (`--z-base` < `--z-nav` 50 < `--z-tooltip` 60). Leaflet's own z-indexes are contained
  with `isolation: isolate` in `pages/map.css` so the map can't cover the fixed nav.
- **Hero globe:** real country outlines (Natural Earth, public domain) pre-extracted to
  `src/data/south-asia.json`; India highlighted, neighbours muted.
- **Favicon:** `public/favicon.svg` matches the nav logo.
- **Reviews:** real submissions only, no placeholder reviews.
- **How it works video:** plays `public/media/how-it-works.mp4` if present (git-ignored),
  otherwise an animated walkthrough.

## Open items

- `/plan` with our planner takes ~3 s and `/whatif` 2–4 s; the UI shows a spinner. Faster
  responses would help the demo most.
- Map truck labels overlap near Bengaluru (very short lanes from Kolar); could be spread out
  or grouped.
- No live mandi price feed; all prices come from the fitted market model.
- Final evaluation numbers should land in `reports/final/`, which `/eval/latest` serves
  first. The landing stat tiles and Evaluation page pick them up automatically.
