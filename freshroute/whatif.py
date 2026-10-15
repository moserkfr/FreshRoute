"""What-if replanning: apply an operator event to a State, summarise plans, diff two plans.

Events change only what the planner can see (lanes, fleet, forecast, stock); the planner is
then called again on the new State. Event shapes mirror the scenario events in
``scenarios.yaml`` so a what-if in the UI means the same thing as a disruption in evaluation.

  lane_closure  {dest_id}                                  lane hub → dest_id closed
  vehicle_down  {vehicle_id}                               vehicle unavailable today
  demand_spike  {dest_id, commodity_id?, mult, duration_days}  forecast demand × mult
  price_shock   {dest_id, commodity_id?, mult}             forecast price × mult from today on
  new_batches   {batches: [{commodity_id, qty_kg, grade, …}]}  extra stock arrives at the hub
"""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from freshroute.config import AppConfig
from freshroute.logistics import dispatch_ts
from freshroute.schemas import Batch, Grade, Plan, State


class WhatIfError(ValueError):
    """The event does not fit the State (unknown destination, vehicle, commodity …)."""


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ------------------------------------------------------------------ event models


class LaneClosureParams(_Model):
    dest_id: str


class VehicleDownParams(_Model):
    vehicle_id: str


class DemandSpikeParams(_Model):
    dest_id: str
    commodity_id: str | None = Field(default=None, description="None = every commodity")
    mult: float = Field(gt=0, description="demand multiplier, e.g. 1.8 = +80%")
    duration_days: int = Field(default=1, ge=1)


class PriceShockParams(_Model):
    dest_id: str
    commodity_id: str | None = Field(default=None, description="None = every commodity")
    mult: float = Field(gt=0, description="price multiplier, e.g. 0.5 = −50%")


class NewBatch(_Model):
    """Stock arriving today. Unset life/cost fields get the generator's defaults for a batch
    harvested this morning (see ``default.generator``)."""

    commodity_id: str
    qty_kg: float = Field(gt=0)
    grade: Grade = "A"
    remaining_life_mean_days: float | None = Field(default=None, gt=0)
    remaining_life_sd_days: float | None = Field(default=None, ge=0)
    cost_basis_per_kg: float | None = Field(default=None, ge=0)


class NewBatchesParams(_Model):
    batches: list[NewBatch] = Field(min_length=1)


class LaneClosure(_Model):
    type: Literal["lane_closure"]
    params: LaneClosureParams


class VehicleDown(_Model):
    type: Literal["vehicle_down"]
    params: VehicleDownParams


class DemandSpike(_Model):
    type: Literal["demand_spike"]
    params: DemandSpikeParams


class PriceShock(_Model):
    type: Literal["price_shock"]
    params: PriceShockParams


class NewBatches(_Model):
    type: Literal["new_batches"]
    params: NewBatchesParams


WhatIfEvent = Annotated[LaneClosure | VehicleDown | DemandSpike | PriceShock | NewBatches,
                        Field(discriminator="type")]


# ------------------------------------------------------------------ apply


def apply_event(state: State, event: WhatIfEvent, cfg: AppConfig) -> State:
    """New State with ``event`` applied. The input State is not modified."""
    dests = {d.dest_id for d in state.destinations}
    comms = {c.id for c in state.commodities}
    p = event.params

    def check_dest(dest_id: str) -> None:
        if dest_id not in dests:
            raise WhatIfError(f"unknown destination '{dest_id}'; known: {sorted(dests)}")

    def check_commodity(cid: str | None) -> None:
        if cid is not None and cid not in comms:
            raise WhatIfError(f"unknown commodity '{cid}'; known: {sorted(comms)}")

    update: dict[str, Any] = {}
    if isinstance(event, LaneClosure):
        check_dest(p.dest_id)
        update["lanes"] = [ln.model_copy(update={"open": False}) if ln.dest_id == p.dest_id
                           else ln for ln in state.lanes]
    elif isinstance(event, VehicleDown):
        ids = {v.vehicle_id for v in state.vehicles}
        if p.vehicle_id not in ids:
            raise WhatIfError(f"unknown vehicle '{p.vehicle_id}'; known: {sorted(ids)}")
        update["vehicles"] = [v.model_copy(update={"available": False})
                              if v.vehicle_id == p.vehicle_id else v for v in state.vehicles]
    elif isinstance(event, DemandSpike | PriceShock):
        check_dest(p.dest_id)
        check_commodity(p.commodity_id)
        last = (state.date + timedelta(days=p.duration_days - 1)
                if isinstance(event, DemandSpike) else None)
        rows = []
        for m in state.market_forecast:
            hit = (m.dest_id == p.dest_id and p.commodity_id in (None, m.commodity_id)
                   and m.date >= state.date and (last is None or m.date <= last))
            if not hit:
                rows.append(m)
            elif isinstance(event, DemandSpike):
                rows.append(m.model_copy(update={"demand_kg_mean": m.demand_kg_mean * p.mult,
                                                 "demand_kg_sd": m.demand_kg_sd * p.mult}))
            else:
                rows.append(m.model_copy(update={
                    "price_per_kg_forecast": m.price_per_kg_forecast * p.mult,
                    "price_sd": m.price_sd * p.mult}))
        update["market_forecast"] = rows
    elif isinstance(event, NewBatches):
        for nb in p.batches:
            check_commodity(nb.commodity_id)
        update["batches"] = [*state.batches, *_new_batches(state, p.batches, cfg)]
    events = [*state.config_snapshot.get("whatif_events", []), event.model_dump()]
    update["config_snapshot"] = {**state.config_snapshot, "whatif_events": events}
    return state.model_copy(update=update)


def _new_batches(state: State, new: list[NewBatch], cfg: AppConfig) -> list[Batch]:
    """Batches harvested this morning, with generator-default life and cost basis."""
    g = cfg.default.generator
    comms = {c.id: c for c in state.commodities}
    taken = {b.batch_id for b in state.batches}
    harvest = dispatch_ts(state.date, cfg)
    out = []
    for nb in new:
        c = comms[nb.commodity_id]
        mean = nb.remaining_life_mean_days or c.shelf_life_ref_days * g.grade_life_mult[nb.grade]
        sd = (nb.remaining_life_sd_days if nb.remaining_life_sd_days is not None
              else g.grade_sd_frac[nb.grade] * mean)
        cost = nb.cost_basis_per_kg
        if cost is None:
            cost = _default_cost_basis(state, nb.commodity_id, g.cost_basis_frac)
        i = 1
        while (bid := f"NEW-{c.id[:2].upper()}-{i:03d}") in taken:
            i += 1
        taken.add(bid)
        out.append(Batch(batch_id=bid, commodity_id=c.id, qty_kg=nb.qty_kg, grade=nb.grade,
                         harvest_ts=harvest, location_id=state.hub_id,
                         remaining_life_mean_days=round(mean, 4),
                         remaining_life_sd_days=round(sd, 4),
                         cost_basis_per_kg=round(cost, 2)))
    return out


def _default_cost_basis(state: State, commodity_id: str, frac: tuple[float, float]) -> float:
    """Mean cost basis of the same commodity in stock; else the generator's mid fraction of
    today's mean forecast price."""
    same = [b.cost_basis_per_kg for b in state.batches if b.commodity_id == commodity_id]
    if same:
        return sum(same) / len(same)
    prices = [m.price_per_kg_forecast for m in state.market_forecast
              if m.commodity_id == commodity_id and m.date == state.date]
    return (sum(prices) / len(prices)) * (frac[0] + frac[1]) / 2 if prices else 0.0


# ------------------------------------------------------------------ summary + diff


class PlanSummary(_Model):
    """Planner-side expectations for one plan (forecasts, not simulated outcomes). ₹, kg."""

    stock_kg: float
    shipped_kg: float
    held_kg: float
    discounted_kg: float
    trips: int
    freight_inr: float = Field(description="fixed + round-trip km cost of the vehicles used")
    expected_net_value_inr: float = Field(description="Σ allocation expected net value − freight")
    expected_fill_pct: float = Field(
        description="forecast demand covered on arrival ÷ forecast demand of the dispatch date")
    mean_arrival_quality: float | None
    violations: int
    solve_time_s: float
    fallback_used: bool


def summarise(plan: Plan, state: State) -> PlanSummary:
    vehicles = {v.vehicle_id: v for v in state.vehicles}
    lanes = {ln.dest_id: ln for ln in state.lanes}
    used: dict[str, str] = {}
    for a in plan.allocations:
        used.setdefault(a.vehicle_id, a.dest_id)
    freight = sum(vehicles[v].fixed_cost_per_trip + vehicles[v].cost_per_km
                  * lanes[d].distance_km * 2 for v, d in used.items())
    comm_of = {b.batch_id: b.commodity_id for b in state.batches}
    demand = {(m.dest_id, m.commodity_id, m.date): m.demand_kg_mean for m in state.market_forecast}
    arriving: dict[tuple, float] = defaultdict(float)
    for a in plan.allocations:
        arriving[(a.dest_id, comm_of.get(a.batch_id, ""), a.eta_ts.date())] += a.qty_kg
    covered = sum(min(kg, demand.get(k, 0.0)) for k, kg in arriving.items())
    today = sum(m.demand_kg_mean for m in state.market_forecast if m.date == state.date)
    shipped = sum(a.qty_kg for a in plan.allocations)
    q = (sum(a.arrival_quality_mean * a.qty_kg for a in plan.allocations) / shipped
         if shipped > 0 else None)
    return PlanSummary(
        stock_kg=round(sum(b.qty_kg for b in state.batches), 3),
        shipped_kg=round(shipped, 3),
        held_kg=round(sum(h.qty_kg for h in plan.holds), 3),
        discounted_kg=round(sum(a.qty_kg for a in plan.allocations if a.discount_pct > 0)
                            + sum(h.qty_kg for h in plan.holds if h.discount_pct > 0), 3),
        trips=len(used),
        freight_inr=round(freight, 2),
        expected_net_value_inr=round(sum(a.expected_net_value for a in plan.allocations)
                                     - freight, 2),
        expected_fill_pct=round(covered / today, 4) if today > 0 else 0.0,
        mean_arrival_quality=round(q, 4) if q is not None else None,
        violations=len(plan.violations),
        solve_time_s=round(plan.solver_meta.time_s, 3),
        fallback_used=plan.solver_meta.fallback_used,
    )


class Leg(_Model):
    dest_id: str
    vehicle_id: str
    qty_kg: float
    discount_pct: float


ChangeType = Literal["new_batch", "rerouted", "now_shipped", "now_held", "qty_changed",
                     "discount_changed"]


class BatchChange(_Model):
    batch_id: str
    commodity_id: str
    change: ChangeType
    before: list[Leg]
    after: list[Leg]
    text: str


class PlanDiff(_Model):
    changes: list[BatchChange]
    vehicles_before: dict[str, str]
    vehicles_after: dict[str, str]
    summary_before: PlanSummary
    summary_after: PlanSummary
    summary_delta: dict[str, float] = Field(description="after − before for numeric fields")


def _legs(plan: Plan) -> dict[str, list[Leg]]:
    out: dict[str, list[Leg]] = defaultdict(list)
    for a in plan.allocations:
        out[a.batch_id].append(Leg(dest_id=a.dest_id, vehicle_id=a.vehicle_id,
                                   qty_kg=round(a.qty_kg, 3), discount_pct=a.discount_pct))
    return {k: sorted(v, key=lambda leg: (leg.dest_id, leg.vehicle_id)) for k, v in out.items()}


def _where(legs: list[Leg]) -> str:
    return ", ".join(f"{leg.qty_kg:,.0f} kg → {leg.dest_id} on {leg.vehicle_id}"
                     + (f" at {leg.discount_pct:.0f}% off" if leg.discount_pct else "")
                     for leg in legs) or "held at hub"


def diff_plans(before: Plan, state_before: State, after: Plan, state_after: State,
               kg_tol: float = 1.0) -> PlanDiff:
    """Per-batch changes between two plans (``kg_tol`` ignores rounding-size moves)."""
    lb, la = _legs(before), _legs(after)
    old_ids = {b.batch_id for b in state_before.batches}
    comm = {b.batch_id: b.commodity_id for b in state_after.batches}
    comm.update({b.batch_id: b.commodity_id for b in state_before.batches})
    changes = []
    for bid in sorted(set(lb) | set(la) | (set(comm) - old_ids)):
        b, a = lb.get(bid, []), la.get(bid, [])
        kb, ka = sum(x.qty_kg for x in b), sum(x.qty_kg for x in a)
        if bid not in old_ids:
            kind: ChangeType = "new_batch"
        elif {x.dest_id for x in b} != {x.dest_id for x in a} and b and a:
            kind = "rerouted"
        elif not b and a:
            kind = "now_shipped"
        elif b and not a:
            kind = "now_held"
        elif abs(kb - ka) > kg_tol:
            kind = "qty_changed"
        elif {x.discount_pct for x in b} != {x.discount_pct for x in a}:
            kind = "discount_changed"
        else:
            continue  # same destinations, kg and discount (a vehicle swap alone is not news)
        changes.append(BatchChange(batch_id=bid, commodity_id=comm.get(bid, ""), change=kind,
                                   before=b, after=a,
                                   text=f"{bid}: {_where(b)}  ⇒  {_where(a)}"))
    # Biggest moves first: re-solving can also reshuffle near-equal options by a few kg.
    changes.sort(key=lambda c: -max(sum(x.qty_kg for x in c.before),
                                    sum(x.qty_kg for x in c.after)))
    sb, sa = summarise(before, state_before), summarise(after, state_after)
    delta = {k: round(float(v) - float(getattr(sb, k)), 4) for k, v in sa.model_dump().items()
             if isinstance(v, int | float) and not isinstance(v, bool)
             and getattr(sb, k) is not None}
    return PlanDiff(changes=changes, vehicles_before=dict(before.vehicle_assignments),
                    vehicles_after=dict(after.vehicle_assignments), summary_before=sb,
                    summary_after=sa, summary_delta=delta)
