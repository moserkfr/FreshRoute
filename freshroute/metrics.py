"""Run metrics. Pure functions of a ``RunResult`` — no simulation state.

  spoilage_pct    = (spoiled_storage + spoiled_transit + unsold_market) kg / received kg
  fulfilment_pct  = sold kg / demand kg (overall, per destination, min destination)
  net_revenue_inr = revenue − transport − handling − storage − disposal
  discount_cost_inr is reported separately (revenue forgone, already absent from revenue).

Stock still held at the hub when the run ends is reported (``held_end_kg``) but is neither
sold nor spoiled, so it does not enter spoilage.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, timedelta

from freshroute.schemas import DayOutcome, RunResult

FESTIVAL_FILL_DAYS = 3      # festival_fill_rate window: ±3 days around each festival
POST_FESTIVAL_DAYS = 3      # post_festival_spoilage: the 3 days after each festival


def _sum(days: Iterable[DayOutcome], field: str) -> float:
    return float(sum(getattr(d, field) for d in days))


def _ratio(num: float, den: float) -> float:
    return num / den if den > 0 else 0.0


def spoiled_kg(days: Iterable[DayOutcome]) -> float:
    days = list(days)
    return (_sum(days, "spoiled_storage_kg") + _sum(days, "spoiled_transit_kg")
            + _sum(days, "unsold_market_kg"))


def day_metrics(d: DayOutcome) -> dict[str, float]:
    """Per-day figures (the ledger already carries the raw kg and ₹)."""
    return {
        "day": d.day,
        "spoilage_pct": _ratio(spoiled_kg([d]), d.received_kg),
        "fulfilment_pct": _ratio(d.sold_kg, d.demand_kg),
        "net_revenue_inr": d.revenue_inr - d.transport_inr - d.handling_inr - d.storage_inr
        - d.disposal_inr,
    }


def run_metrics(r: RunResult, festival_dates: Iterable[date] = ()) -> dict:
    """Headline metrics for one run. ``festival_dates`` enables the festival metrics."""
    days = r.days
    received = _sum(days, "received_kg")
    sold = _sum(days, "sold_kg")
    demand = _sum(days, "demand_kg")
    revenue = _sum(days, "revenue_inr")
    costs = {k: _sum(days, f"{k}_inr") for k in ("transport", "handling", "storage", "disposal")}

    dest_sold: dict[str, float] = {}
    dest_demand: dict[str, float] = {}
    for d in days:
        for k, v in d.dest_sold_kg.items():
            dest_sold[k] = dest_sold.get(k, 0.0) + v
        for k, v in d.dest_demand_kg.items():
            dest_demand[k] = dest_demand.get(k, 0.0) + v
    per_dest = {k: _ratio(dest_sold.get(k, 0.0), v) for k, v in sorted(dest_demand.items())}

    violations: dict[str, int] = {}
    for d in days:
        for k, v in d.violations.items():
            violations[k] = violations.get(k, 0) + v

    planning = [d for d in days if not d.settlement_only]
    gaps = [d.solver_gap for d in planning if d.solver_gap is not None]
    out = {
        "scenario_id": r.scenario_id,
        "seed": r.seed,
        "planner": r.planner_name,
        "forecast_mode": r.forecast_mode,
        "received_kg": received,
        "sold_kg": sold,
        "demand_kg": demand,
        "spoiled_kg": spoiled_kg(days),
        "spoiled_storage_kg": _sum(days, "spoiled_storage_kg"),
        "spoiled_transit_kg": _sum(days, "spoiled_transit_kg"),
        "unsold_market_kg": _sum(days, "unsold_market_kg"),
        "rejected_kg": _sum(days, "rejected_kg"),
        "held_end_kg": days[-1].held_end_kg if days else 0.0,
        "spoilage_pct": _ratio(spoiled_kg(days), received),
        "fulfilment_pct": _ratio(sold, demand),
        "fulfilment_by_dest": per_dest,
        "fulfilment_min_dest": min(per_dest.values()) if per_dest else 0.0,
        "revenue_inr": revenue,
        **{f"{k}_inr": v for k, v in costs.items()},
        "net_revenue_inr": revenue - sum(costs.values()),
        "discount_cost_inr": _sum(days, "discount_cost_inr"),
        "violations": violations,
        "violations_total": sum(violations.values()),
        "dropped_kg": _sum(days, "dropped_kg"),
        "trips": int(_sum(days, "trips")),
        "plan_time_s": _sum(planning, "plan_time_s"),
        "plan_time_max_s": max((d.plan_time_s for d in planning), default=0.0),
        "solver_gap_max": max(gaps) if gaps else None,
        "fallback_days": sum(d.fallback_used for d in planning),
        "placeholder_data": bool(r.placeholder_data),
    }
    out.update(festival_metrics(r, festival_dates))
    return out


def festival_metrics(r: RunResult, festival_dates: Iterable[date]) -> dict[str, float | None]:
    """``festival_fill_rate`` (fulfilment within ±3 d) and ``post_festival_spoilage`` (3 d after).

    ``None`` when no festival falls near the simulated window.
    """
    fests = sorted(set(festival_dates))
    planning = [d for d in r.days if not d.settlement_only]
    if not fests or not planning:
        return {"festival_fill_rate": None, "post_festival_spoilage": None}

    def near(day: date) -> bool:
        return any(abs((day - f).days) <= FESTIVAL_FILL_DAYS for f in fests)

    def after(day: date) -> bool:
        return any(f < day <= f + timedelta(days=POST_FESTIVAL_DAYS) for f in fests)

    win = [d for d in planning if near(d.date)]
    post = [d for d in planning if after(d.date)]
    return {
        "festival_fill_rate": _ratio(_sum(win, "sold_kg"), _sum(win, "demand_kg"))
        if win else None,
        "post_festival_spoilage": _ratio(spoiled_kg(post), _sum(post, "received_kg"))
        if post else None,
    }
