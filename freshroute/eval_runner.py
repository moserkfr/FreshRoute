"""Monte Carlo evaluation: scenario × planner × seeds → reports/eval_<ts>.json/.md.

Every planner meets the same instance per (scenario, seed) — same batches, markets, road
delays (common random numbers) — so per-seed differences vs the baseline are paired.

Modes (``--mode``, default ``all``):
  main         every scenario × planner × seed, with CIs, paired differences vs B1, win-rates
  replan       disruption scenarios: the same planner with vs without mid-day replanning
  sensitivity  tornado: each parameter ×(1 ± δ) on one scenario, Δ(planner − B1) per setting

Usage:
  python -m freshroute.eval_runner                       # all modes, validation seeds
  python -m freshroute.eval_runner --mode main --scenarios normal --seeds 51-60
  python -m freshroute.eval_runner --hidden              # locked final run (seeds ≥ hidden_min)
  python -m freshroute.eval_runner --resume reports/ckpt_<ts>.jsonl   # continue a stopped run

Each finished instance is appended to reports/ckpt_<ts>.jsonl and a partial report is written
after every phase, so a stopped run loses at most the instances in flight.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np

from freshroute.baselines import PLANNERS, get_planner
from freshroute.config import AppConfig, load_config
from freshroute.generator import generate, parse_seeds
from freshroute.market_model import PROCESSED_DIR, load_market_params
from freshroute.metrics import run_metrics
from freshroute.sensitivity import perturb
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


def run_one(task: dict) -> list[dict]:
    """Generate one instance and run the task's planners on it (worker entry point).

    task: scenario, seed, planners, and optionally replan (bool) and perturb ((param, factor)).
    """
    import warnings
    warnings.filterwarnings("ignore")  # placeholder warnings are reported once by the parent
    cfg = load_config()
    mp = load_market_params()
    pert = task.get("perturb")
    if pert:
        cfg, mp = perturb(cfg, mp, pert[0], pert[1])
    inst = generate(task["scenario"], task["seed"], cfg=cfg, market_params=mp)
    fests = festival_dates(cfg, inst.start_date, inst.days)
    out = []
    for name in task["planners"]:
        t0 = time.perf_counter()
        r = run(inst, get_planner(name), cfg, replan=task.get("replan", False))
        m = run_metrics(r, fests)
        m.update(wall_s=time.perf_counter() - t0, instance_fingerprint=inst.fingerprint()[:16],
                 replan=bool(task.get("replan", False)), mode=task.get("mode", "main"),
                 perturb_param=pert[0] if pert else None,
                 perturb_factor=pert[1] if pert else 1.0)
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
            for k in ("fulfilment_min_dest", "spoilage_pct_worst", "held_end_kg",
                      "fulfilment_of_possible_pct"):
                if all(k in r for r in pr):
                    entry[k] = summarise([r[k] for r in pr], n, level)
            for k in ("festival_fill_rate", "post_festival_spoilage"):
                vals = [r[k] for r in pr if r.get(k) is not None]
                if vals:
                    entry[k] = summarise(vals, n, level)
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


def aggregate_replan(rows: list[dict], cfg: AppConfig) -> dict:
    """Replan − static per (scenario, planner), paired by seed."""
    ev = cfg.default.evaluation
    n, level = ev.bootstrap_resamples, ev.ci_level
    out: dict = {}
    for sc in sorted({r["scenario_id"] for r in rows}):
        for p in sorted({r["planner"] for r in rows if r["scenario_id"] == sc}):
            sub = [r for r in rows if r["scenario_id"] == sc and r["planner"] == p]
            static = {r["seed"]: r for r in sub if not r["replan"]}
            dyn = {r["seed"]: r for r in sub if r["replan"]}
            seeds = sorted(static.keys() & dyn.keys())
            if not seeds:
                continue
            entry = {"static": {k: summarise([static[s][k] for s in seeds], n, level)
                                for k in HEADLINE},
                     "replan": {k: summarise([dyn[s][k] for s in seeds], n, level)
                                for k in HEADLINE},
                     "replan_minus_static": {
                         k: summarise([dyn[s][k] - static[s][k] for s in seeds], n, level)
                         for k in HEADLINE},
                     "win_rate_net_revenue": sum(dyn[s]["net_revenue_inr"]
                                                 > static[s]["net_revenue_inr"] + 1e-6
                                                 for s in seeds) / len(seeds),
                     "ties": sum(abs(dyn[s]["net_revenue_inr"] - static[s]["net_revenue_inr"])
                                 <= 1e-6 for s in seeds) / len(seeds)}
            out.setdefault(sc, {})[p] = entry
    return out


def aggregate_sensitivity(rows: list[dict], cfg: AppConfig) -> dict:
    """Tornado data: Δ(planner − baseline) per parameter setting, paired by seed."""
    sc = cfg.default.evaluation.sensitivity
    base_name = cfg.default.evaluation.baseline

    def advantage(sub: list[dict]) -> dict[str, float]:
        ours = {r["seed"]: r for r in sub if r["planner"] == sc.planner}
        b1 = {r["seed"]: r for r in sub if r["planner"] == base_name}
        seeds = sorted(ours.keys() & b1.keys())
        return {"d_net_revenue_inr": float(np.mean([ours[s]["net_revenue_inr"]
                                                    - b1[s]["net_revenue_inr"] for s in seeds])),
                "d_spoilage_pct": float(np.mean([ours[s]["spoilage_pct"]
                                                 - b1[s]["spoilage_pct"] for s in seeds])),
                "n": len(seeds)}

    base = advantage([r for r in rows if r["perturb_param"] is None])
    tornado = []
    for param in sc.params:
        lo = advantage([r for r in rows if r["perturb_param"] == param
                        and r["perturb_factor"] < 1])
        hi = advantage([r for r in rows if r["perturb_param"] == param
                        and r["perturb_factor"] > 1])
        tornado.append({"param": param, "low_factor": 1 - sc.delta, "high_factor": 1 + sc.delta,
                        "low": lo, "high": hi,
                        "swing_net_revenue_inr": abs(hi["d_net_revenue_inr"]
                                                     - lo["d_net_revenue_inr"])})
    tornado.sort(key=lambda t: -t["swing_net_revenue_inr"])
    return {"scenario": sc.scenario, "planner": sc.planner, "baseline": base_name,
            "delta": sc.delta, "base": base, "tornado": tornado}


# ------------------------------------------------------------------ reporting


def _fmt(k: str, s: dict) -> str:
    if "_pct" in k:
        return f"{s['mean']:.1%} [{s['ci_lo']:.1%}, {s['ci_hi']:.1%}]"
    if k.endswith("_inr"):
        return f"₹{s['mean']:,.0f} [{s['ci_lo']:,.0f}, {s['ci_hi']:,.0f}]"
    return f"{s['mean']:.2f} [{s['ci_lo']:.2f}, {s['ci_hi']:.2f}]"


def _md_main(report: dict, lines: list[str]) -> None:
    meta = report["meta"]
    for sc, planners in report.get("aggregate", {}).items():
        lines += [f"## {sc}", "",
                  "| planner | spoilage | fulfilment | of possible | net revenue | violations "
                  "| spoilage incl. end stock | end stock (t) |",
                  "|---|---|---|---|---|---|---|---|"]
        for p, e in planners.items():
            possible = e.get("fulfilment_of_possible_pct")
            cells = [_fmt(k, e[k]) for k in HEADLINE]
            cells.insert(2, _fmt("fulfilment_of_possible_pct", possible) if possible else "—")
            lines.append(f"| {p} | " + " | ".join(cells)
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
        fest = [(p, e) for p, e in planners.items() if "festival_fill_rate" in e]
        if fest:
            lines += ["", "| planner | festival fill rate (±3 d) | post-festival spoilage (3 d) |",
                      "|---|---|---|"]
            for p, e in fest:
                post = e.get("post_festival_spoilage")
                post_txt = _fmt("post_pct", post) if post else "—"
                lines.append(f"| {p} | {_fmt('fill_pct', e['festival_fill_rate'])} "
                             f"| {post_txt} |")
        lines.append("")


def _md_replan(report: dict, lines: list[str]) -> None:
    lines += ["## Replan vs static (mid-day replanning hook)", "",
              "| scenario | planner | static net revenue | replan net revenue | Δ net revenue "
              "| Δ spoilage | replan better | identical |", "|---|---|---|---|---|---|---|---|"]
    for sc, planners in report["replan"].items():
        for p, e in planners.items():
            d = e["replan_minus_static"]
            cells = [sc, p, _fmt("net_revenue_inr", e["static"]["net_revenue_inr"]),
                     _fmt("net_revenue_inr", e["replan"]["net_revenue_inr"]),
                     _fmt("net_revenue_inr", d["net_revenue_inr"]),
                     _fmt("spoilage_pct", d["spoilage_pct"]),
                     f"{e['win_rate_net_revenue']:.0%}", f"{e['ties']:.0%}"]
            lines.append("| " + " | ".join(cells) + " |")
    lines.append("")


def _md_sensitivity(report: dict, lines: list[str]) -> None:
    t = report["sensitivity"]
    b = t["base"]
    lines += [f"## Sensitivity (tornado): `{t['planner']}` − `{t['baseline']}` on "
              f"{t['scenario']}, each parameter ×(1 ± {t['delta']:.0%})", "",
              f"Base advantage: ₹{b['d_net_revenue_inr']:,.0f} net revenue, "
              f"{b['d_spoilage_pct']:+.1%} spoilage ({b['n']} seeds).", "",
              "| parameter | Δ revenue at −δ | Δ revenue at +δ | Δ spoilage at −δ "
              "| Δ spoilage at +δ |", "|---|---|---|---|---|"]
    for row in t["tornado"]:
        lo, hi = row["low"], row["high"]
        lines.append(f"| {row['param']} | ₹{lo['d_net_revenue_inr']:,.0f} | "
                     f"₹{hi['d_net_revenue_inr']:,.0f} | {lo['d_spoilage_pct']:+.1%} | "
                     f"{hi['d_spoilage_pct']:+.1%} |")
    lines.append("")


def to_markdown(report: dict) -> str:
    meta = report["meta"]
    lines = [f"# FreshRoute evaluation — {meta['timestamp']}", ""]
    if meta["placeholder_data"]:
        lines += ["> **PLACEHOLDER MARKET DATA** — numbers are not final.", ""]
    lines += [f"Seeds: {meta['seed_label']} ({meta['n_seeds']}) · baseline: "
              f"`{meta['baseline']}` · {int(meta['ci_level'] * 100)}% bootstrap CI over seeds", ""]
    w = report.get("forecast_wape")
    if w:
        lines += ["## Forecast accuracy (hold-out year, daily arrivals at destination markets)", "",
                  "| forecast | WAPE overall | WAPE festival windows (±7 d) |", "|---|---|---|"]
        for mode in ("full", "no_calendar"):
            lines.append(f"| {mode} | {w[mode]['WAPE_overall']:.1%} | "
                         f"{w[mode]['WAPE_festival_window']:.1%} |")
        lines.append("")
    _md_main(report, lines)
    if report.get("replan"):
        _md_replan(report, lines)
    if report.get("sensitivity"):
        _md_sensitivity(report, lines)
    return "\n".join(lines)


def task_key(task: dict) -> str:
    return json.dumps(task, sort_keys=True, default=str)


def load_checkpoint(path: Path) -> dict[str, list[dict]]:
    """Finished instances from a checkpoint file (a torn last line from a crash is skipped)."""
    done: dict[str, list[dict]] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            done[rec["key"]] = rec["rows"]
    return done


def run_tasks(tasks: list[dict], jobs: int, label: str, ckpt: Path | None = None,
              done: dict[str, list[dict]] | None = None) -> list[dict]:
    done = done or {}
    results = {task_key(t): done[task_key(t)] for t in tasks if task_key(t) in done}
    todo = [t for t in tasks if task_key(t) not in results]
    n_runs = sum(len(t["planners"]) for t in todo)
    print(f"{label}: {len(tasks)} instance(s), {len(results)} from checkpoint, "
          f"{n_runs} runs on {jobs} worker(s)")
    t0 = time.perf_counter()
    fh = ckpt.open("a", encoding="utf-8") if ckpt else None

    def finish(task: dict, res: list[dict]) -> None:
        results[task_key(task)] = res
        if fh:
            fh.write(json.dumps({"key": task_key(task), "rows": res}, default=str) + "\n")
            fh.flush()
        print(f"\r  {len(results)}/{len(tasks)} instances", end="", flush=True)

    try:
        if jobs > 1 and len(todo) > 1:
            with ProcessPoolExecutor(max_workers=jobs) as ex:
                futs = {ex.submit(run_one, t): t for t in todo}
                for f in as_completed(futs):
                    finish(futs[f], f.result())
        else:
            for task in todo:
                finish(task, run_one(task))
    finally:
        if fh:
            fh.close()
    print(f"\n  done in {time.perf_counter() - t0:.0f} s")
    # task order, not completion order, so a resumed run gives the same report
    return [r for t in tasks for r in results[task_key(t)]]


def main(argv: list[str] | None = None) -> int:
    cfg = load_config()
    ev = cfg.default.evaluation
    ap = argparse.ArgumentParser(description="FreshRoute Monte Carlo evaluation")
    ap.add_argument("--mode", default="all", choices=["all", "main", "replan", "sensitivity"])
    ap.add_argument("--scenarios", default="all", help="comma list or 'all' (main mode)")
    ap.add_argument("--planners", default="all", help="comma list or 'all' (main mode)")
    ap.add_argument("--seeds", default=None, help="e.g. 51-100 (default: validation split)")
    ap.add_argument("--hidden", action="store_true", help="final locked run on seeds ≥ hidden_min")
    ap.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    ap.add_argument("--out", type=Path, default=REPORTS_DIR)
    ap.add_argument("--resume", type=Path, default=None,
                    help="checkpoint of a stopped run (same arguments) to continue")
    ap.add_argument("--no-checkpoint", action="store_true")
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
    base = ev.baseline
    if base in PLANNERS and base not in planners:
        planners.append(base)  # paired differences need the baseline on every seed
    for sc in [*scenarios, *ev.replan_scenarios, ev.sensitivity.scenario]:
        cfg.scenarios.resolved(sc)  # readable error on typos
    for p in [*planners, *ev.replan_planners, ev.sensitivity.planner]:
        get_planner(p)

    placeholder = bool(load_market_params().placeholder_files)  # prints the warning once
    report: dict = {"meta": {
        "timestamp": datetime.now().strftime("%Y%m%d_%H%M%S"), "mode": args.mode,
        "scenarios": scenarios, "planners": planners, "seeds": seeds, "seed_label": label,
        "n_seeds": len(seeds), "hidden": args.hidden, "baseline": base,
        "ci_level": ev.ci_level, "bootstrap_resamples": ev.bootstrap_resamples,
        "placeholder_data": placeholder}}
    fit_report = PROCESSED_DIR / "fit_report.json"
    if fit_report.exists():
        report["forecast_wape"] = json.loads(fit_report.read_text(encoding="utf-8")).get(
            "holdout_wape")
    args.out.mkdir(parents=True, exist_ok=True)
    ckpt: Path | None = None
    done: dict[str, list[dict]] = {}
    if args.resume:
        ckpt, done = args.resume, load_checkpoint(args.resume)
        print(f"resuming from {ckpt}: {len(done)} finished instance(s)")
    elif not args.no_checkpoint:
        ckpt = args.out / f"ckpt_{report['meta']['timestamp']}.jsonl"
        print(f"checkpoint: {ckpt}")
    rows: list[dict] = []

    def save(final: bool) -> None:
        report["runs"] = rows
        write_report(report, args.out, final)

    if args.mode in ("all", "main"):
        main_rows = run_tasks([{"scenario": sc, "seed": s, "planners": planners, "mode": "main"}
                               for sc in scenarios for s in seeds], args.jobs, "main", ckpt, done)
        report["aggregate"] = aggregate(main_rows, cfg)
        rows += main_rows
        save(final=False)
    if args.mode in ("all", "replan"):
        tasks = [{"scenario": sc, "seed": s, "planners": ev.replan_planners, "replan": rp,
                  "mode": "replan"}
                 for sc in ev.replan_scenarios for s in seeds for rp in (False, True)]
        rp_rows = run_tasks(tasks, args.jobs, "replan vs static", ckpt, done)
        report["replan"] = aggregate_replan(rp_rows, cfg)
        rows += rp_rows
        save(final=False)
    if args.mode in ("all", "sensitivity"):
        sc = ev.sensitivity
        n_sens = sc.seeds[1] - sc.seeds[0] + 1
        # explicit --seeds (or --hidden) override the configured sensitivity seeds
        s_seeds = (seeds[:n_sens] if args.hidden or args.seeds
                   else list(range(sc.seeds[0], sc.seeds[1] + 1)))
        pl = [sc.planner, base]
        settings = [None] + [(prm, f) for prm in sc.params for f in (1 - sc.delta, 1 + sc.delta)]
        tasks = [{"scenario": sc.scenario, "seed": s, "planners": pl, "perturb": st,
                  "mode": "sensitivity"} for st in settings for s in s_seeds]
        s_rows = run_tasks(tasks, args.jobs, "sensitivity", ckpt, done)
        report["sensitivity"] = aggregate_sensitivity(s_rows, cfg)
        rows += s_rows
    save(final=True)
    return 0


def write_report(report: dict, out: Path, final: bool) -> None:
    """Write eval_<ts>.json/.md; after each phase a partial report goes to eval_<ts>_partial.*."""
    ts = report["meta"]["timestamp"]
    stem = ts if final else f"{ts}_partial"
    jp, mp_ = out / f"eval_{stem}.json", out / f"eval_{stem}.md"
    jp.write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    if final and report.get("sensitivity"):
        (out / f"tornado_{ts}.json").write_text(
            json.dumps(report["sensitivity"], indent=1), encoding="utf-8")
    md = to_markdown(report)
    mp_.write_text(md, encoding="utf-8")
    if final:
        print(md)
        for f in out.glob(f"eval_{ts}_partial.*"):
            f.unlink()
    print(f"wrote {jp}\nwrote {mp_}")


if __name__ == "__main__":
    sys.exit(main())
