"""Monte Carlo evaluation: scenario × planner × seeds → reports/eval_<ts>.json/.md.

Every planner meets the same instance per (scenario, seed) — same batches, markets, road
delays (common random numbers) — so per-seed differences vs the baseline are paired.

Usage:
  python -m freshroute.eval_runner                       # validation seeds, all scenarios
  python -m freshroute.eval_runner --scenarios normal --seeds 51-60
  python -m freshroute.eval_runner --hidden              # locked final run (seeds ≥ hidden_min)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np

from freshroute.baselines import PLANNERS, get_planner
from freshroute.config import AppConfig, load_config
from freshroute.generator import generate, parse_seeds
from freshroute.market_model import load_market_params
from freshroute.metrics import run_metrics
from freshroute.simulator import run

ROOT = Path(__file__).resolve().parent.parent
REPORTS_DIR = ROOT / "reports"
HEADLINE = ("spoilage_pct", "fulfilment_pct", "net_revenue_inr", "violations_total")
# Lower is better for these; higher for the rest (used by win-rate).
LOWER_BETTER = {"spoilage_pct", "violations_total"}


# ------------------------------------------------------------------ statistics


def bootstrap_ci(x: np.ndarray, n: int, level: float, seed: int = 0) -> tuple[float, float]:
    """Percentile bootstrap CI of the mean. A single observation gives a zero-width interval."""
    if len(x) < 2:
        return float(x.mean()), float(x.mean())
    rng = np.random.default_rng(seed)
    means = x[rng.integers(0, len(x), size=(n, len(x)))].mean(axis=1)
    a = (1 - level) / 2
    return float(np.quantile(means, a)), float(np.quantile(means, 1 - a))


def summarise(values: list[float], n: int, level: float) -> dict[str, float]:
    x = np.asarray(values, dtype=float)
    lo, hi = bootstrap_ci(x, n, level)
    return {"mean": float(x.mean()), "ci_lo": lo, "ci_hi": hi, "sd": float(x.std(ddof=0)),
            "n": len(x)}


# ------------------------------------------------------------------ running


def festival_dates(cfg: AppConfig, start: date, days: int) -> list[date]:
    """Calendar festivals within (or within 3 days of) the simulated window."""
    cal = load_market_params().festival_calendar
    lo, hi = start - timedelta(days=3), start + timedelta(days=days + 3)
    out = []
    for d in cal["date"]:
        dd = date.fromisoformat(str(d)[:10])
        if lo <= dd <= hi:
            out.append(dd)
    return sorted(set(out))


def run_one(task: tuple[str, int, list[str]]) -> list[dict]:
    """Generate one instance and run every planner on it (worker entry point)."""
    import warnings
    warnings.filterwarnings("ignore")  # placeholder warnings are reported once by the parent
    scenario, seed, planners = task
    cfg = load_config()
    inst = generate(scenario, seed, cfg=cfg)
    fests = festival_dates(cfg, inst.start_date, inst.days)
    out = []
    for name in planners:
        t0 = time.perf_counter()
        r = run(inst, get_planner(name), cfg)
        m = run_metrics(r, fests)
        m["wall_s"] = time.perf_counter() - t0
        m["instance_fingerprint"] = inst.fingerprint()[:16]
        out.append(m)
    return out


def aggregate(rows: list[dict], cfg: AppConfig) -> dict:
    ev = cfg.default.evaluation
    n, level, base = ev.bootstrap_resamples, ev.ci_level, ev.baseline
    out: dict = {}
    for sc in sorted({r["scenario_id"] for r in rows}):
        sc_rows = [r for r in rows if r["scenario_id"] == sc]
        by_planner: dict[str, dict] = {}
        base_by_seed = {r["seed"]: r for r in sc_rows if r["planner"] == base}
        for p in sorted({r["planner"] for r in sc_rows}):
            pr = sorted((r for r in sc_rows if r["planner"] == p), key=lambda r: r["seed"])
            entry = {k: summarise([r[k] for r in pr], n, level) for k in HEADLINE}
            for k in ("fulfilment_min_dest", "spoilage_pct_worst", "held_end_kg"):
                entry[k] = summarise([r[k] for r in pr], n, level)
            entry["violations_by_type"] = {}
            for r in pr:
                for k, v in r["violations"].items():
                    entry["violations_by_type"][k] = entry["violations_by_type"].get(k, 0) + v
            entry["plan_time_max_s"] = max(r["plan_time_max_s"] for r in pr)
            if p != base and base_by_seed:
                paired = [(r, base_by_seed[r["seed"]]) for r in pr if r["seed"] in base_by_seed]
                entry["vs_baseline"] = {
                    k: summarise([a[k] - b[k] for a, b in paired], n, level) for k in HEADLINE}
                wins = {}
                for k in ("net_revenue_inr", "spoilage_pct"):
                    better = [(a[k] < b[k]) if k in LOWER_BETTER else (a[k] > b[k])
                              for a, b in paired]
                    wins[k] = sum(better) / len(better) if better else 0.0
                entry["win_rate"] = wins
            by_planner[p] = entry
        out[sc] = by_planner
    return out


# ------------------------------------------------------------------ reporting


def _fmt(k: str, s: dict) -> str:
    if "_pct" in k:
        return f"{s['mean']:.1%} [{s['ci_lo']:.1%}, {s['ci_hi']:.1%}]"
    if k.endswith("_inr"):
        return f"₹{s['mean']:,.0f} [{s['ci_lo']:,.0f}, {s['ci_hi']:,.0f}]"
    return f"{s['mean']:.2f} [{s['ci_lo']:.2f}, {s['ci_hi']:.2f}]"


def to_markdown(report: dict) -> str:
    meta = report["meta"]
    lines = [f"# FreshRoute evaluation — {meta['timestamp']}", ""]
    if meta["placeholder_data"]:
        lines += ["> **PLACEHOLDER MARKET DATA** — numbers are not final.", ""]
    lines += [f"Seeds: {meta['seed_label']} ({meta['n_seeds']}) · planners: "
              f"{', '.join(meta['planners'])} · baseline: `{meta['baseline']}` · "
              f"{int(meta['ci_level'] * 100)}% bootstrap CI over seeds", ""]
    for sc, planners in report["aggregate"].items():
        lines += [f"## {sc}", "",
                  "| planner | spoilage | fulfilment | net revenue | violations "
                  "| spoilage incl. end stock | end stock (t) |",
                  "|---|---|---|---|---|---|---|"]
        for p, e in planners.items():
            lines.append(f"| {p} | " + " | ".join(_fmt(k, e[k]) for k in HEADLINE)
                         + f" | {_fmt('spoilage_pct_worst', e['spoilage_pct_worst'])}"
                         + f" | {e['held_end_kg']['mean'] / 1000:.1f} |")
        diffs = [(p, e) for p, e in planners.items() if "vs_baseline" in e]
        if diffs:
            lines += ["", f"Paired difference vs `{meta['baseline']}` (planner − baseline):", "",
                      "| planner | Δ spoilage | Δ fulfilment | Δ net revenue | "
                      "win-rate revenue | win-rate spoilage |", "|---|---|---|---|---|---|"]
            for p, e in diffs:
                d = e["vs_baseline"]
                lines.append(f"| {p} | {_fmt('spoilage_pct', d['spoilage_pct'])} | "
                             f"{_fmt('fulfilment_pct', d['fulfilment_pct'])} | "
                             f"{_fmt('net_revenue_inr', d['net_revenue_inr'])} | "
                             f"{e['win_rate']['net_revenue_inr']:.0%} | "
                             f"{e['win_rate']['spoilage_pct']:.0%} |")
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    cfg = load_config()
    ap = argparse.ArgumentParser(description="FreshRoute Monte Carlo evaluation")
    ap.add_argument("--scenarios", default="all", help="comma list or 'all'")
    ap.add_argument("--planners", default="all", help="comma list or 'all'")
    ap.add_argument("--seeds", default=None, help="e.g. 51-100 (default: validation split)")
    ap.add_argument("--hidden", action="store_true", help="final locked run on seeds ≥ hidden_min")
    ap.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    ap.add_argument("--out", type=Path, default=REPORTS_DIR)
    args = ap.parse_args(argv)

    sd = cfg.default.seeds
    if args.hidden:
        n = sd.validation[1] - sd.validation[0] + 1
        seeds = list(range(sd.hidden_min, sd.hidden_min + n))
        label = f"hidden {seeds[0]}–{seeds[-1]}"
    else:
        seeds = parse_seeds(args.seeds) if args.seeds else list(range(sd.validation[0],
                                                                       sd.validation[1] + 1))
        if any(s >= sd.hidden_min for s in seeds):
            print(f"seeds ≥ {sd.hidden_min} are the hidden set; pass --hidden for the final run.")
            return 2
        label = args.seeds or f"validation {seeds[0]}–{seeds[-1]}"
    scenarios = (sorted(cfg.scenarios.scenarios) if args.scenarios == "all"
                 else args.scenarios.split(","))
    planners = sorted(PLANNERS) if args.planners == "all" else args.planners.split(",")
    base = cfg.default.evaluation.baseline
    if base in PLANNERS and base not in planners:
        planners.append(base)  # paired differences need the baseline on every seed
    for sc in scenarios:
        cfg.scenarios.resolved(sc)  # readable error on typos
    for p in planners:
        get_planner(p)

    placeholder = bool(load_market_params().placeholder_files)  # prints the warning once
    tasks = [(sc, s, planners) for sc in scenarios for s in seeds]
    print(f"evaluating {len(scenarios)} scenario(s) × {len(planners)} planner(s) × "
          f"{len(seeds)} seed(s) = {len(tasks) * len(planners)} runs on {args.jobs} worker(s)")
    t0 = time.perf_counter()
    rows: list[dict] = []
    if args.jobs > 1 and len(tasks) > 1:
        with ProcessPoolExecutor(max_workers=args.jobs) as ex:
            for i, res in enumerate(ex.map(run_one, tasks), 1):
                rows.extend(res)
                print(f"\r  {i}/{len(tasks)} instances", end="", flush=True)
    else:
        for i, task in enumerate(tasks, 1):
            rows.extend(run_one(task))
            print(f"\r  {i}/{len(tasks)} instances", end="", flush=True)
    print(f"\n  done in {time.perf_counter() - t0:.0f} s")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    report = {
        "meta": {"timestamp": ts, "scenarios": scenarios, "planners": planners,
                 "seeds": seeds, "seed_label": label, "n_seeds": len(seeds),
                 "hidden": args.hidden, "baseline": base,
                 "ci_level": cfg.default.evaluation.ci_level,
                 "bootstrap_resamples": cfg.default.evaluation.bootstrap_resamples,
                 "placeholder_data": placeholder},
        "aggregate": aggregate(rows, cfg),
        "runs": rows,
    }
    args.out.mkdir(parents=True, exist_ok=True)
    jp, mp_ = args.out / f"eval_{ts}.json", args.out / f"eval_{ts}.md"
    jp.write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    md = to_markdown(report)
    mp_.write_text(md, encoding="utf-8")
    print(md)
    print(f"wrote {jp}\nwrote {mp_}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
