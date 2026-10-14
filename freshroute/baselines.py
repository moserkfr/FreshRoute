"""Baseline planners. All use the same validator, simulator and cost model as ours.

B1 ``fifo_nearest`` — how a typical depot runs without decision support: oldest stock first,
to the nearest destination that still has (yesterday's) demand, on the first free truck.
No shelf-life check, no discounting. It does respect the physical rules of the depot (one
destination per truck, capacity, open lanes, reefer setpoint), so its losses come from its
decisions, not from the validator throwing loads away.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date

from freshroute.config import AppConfig
from freshroute.logistics import TripEstimate, estimate_trip
from freshroute.schemas import Allocation, Batch, Hold, Plan, SolverMeta, State, Vehicle
from freshroute.shelf_life import life_quantile, price_multiplier, quality

KG_EPS = 1e-6


class _Loader:
    """Tracks one-destination-per-vehicle, capacity and reefer setpoints while filling trucks."""

    def __init__(self, state: State):
        self.vehicles = [v for v in state.vehicles if v.available]
        self.dest_of: dict[str, str] = {}
        self.load: dict[str, float] = defaultdict(float)
        self.group: dict[str, str] = {}

    def space(self, v: Vehicle) -> float:
        return v.capacity_kg - self.load[v.vehicle_id]

    def candidates(self, dest_id: str, temp_group: str) -> list[Vehicle]:
        """Trucks already bound for ``dest_id`` first, then the first free truck(s)."""
        def ok(v: Vehicle) -> bool:
            if v.type == "reefer" and self.group.get(v.vehicle_id, temp_group) != temp_group:
                return False
            return self.space(v) > KG_EPS
        going = [v for v in self.vehicles if self.dest_of.get(v.vehicle_id) == dest_id and ok(v)]
        free = [v for v in self.vehicles if v.vehicle_id not in self.dest_of and ok(v)]
        return going + free

    def put(self, v: Vehicle, dest_id: str, temp_group: str, kg: float) -> None:
        self.dest_of[v.vehicle_id] = dest_id
        self.load[v.vehicle_id] += kg
        if v.type == "reefer":
            self.group[v.vehicle_id] = temp_group


def make_allocation(state: State, cfg: AppConfig, b: Batch, dest_id: str, v: Vehicle,
                    kg: float, trip: TripEstimate, price: float, discount_pct: float = 0.0
                    ) -> Allocation:
    """Allocation with arrival-quality estimates and expected net value (₹, before trucks)."""
    c = next(x for x in state.commodities if x.id == b.commodity_id)
    d = next(x for x in state.destinations if x.dest_id == dest_id)
    q_mean = quality(b.remaining_life_mean_days - trip.delta_life_days, c)
    q_p10 = quality(life_quantile(b.remaining_life_mean_days, b.remaining_life_sd_days, 0.9)
                    - trip.delta_life_days, c)
    m = price_multiplier(q_mean, cfg.default.shelf_life.price_bands)
    unit = price * m * (1 - discount_pct / 100) - d.handling_cost_per_kg - c.handling_cost_per_kg
    return Allocation(batch_id=b.batch_id, dest_id=dest_id, vehicle_id=v.vehicle_id,
                      qty_kg=round(kg, 4), discount_pct=discount_pct, depart_ts=trip.depart,
                      eta_ts=trip.eta, arrival_quality_mean=round(q_mean, 4),
                      arrival_quality_p10=round(q_p10, 4),
                      expected_net_value=round(kg * unit, 2))


def forecast_index(state: State) -> dict[tuple[str, str, date], tuple[float, float]]:
    """(dest, commodity, date) → (demand kg, price ₹/kg) from the State's forecast."""
    return {(m.dest_id, m.commodity_id, m.date): (m.demand_kg_mean, m.price_per_kg_forecast)
            for m in state.market_forecast}


class FifoNearest:
    """B1: oldest batch first → nearest destination with remaining demand; first free truck."""

    name = "fifo_nearest"
    forecast_mode = "flat"

    def plan(self, state: State, cfg: AppConfig) -> Plan:
        fc = forecast_index(state)
        remaining = {k: v[0] for k, v in fc.items()}
        dests = {d.dest_id: d for d in state.destinations}
        comms = {c.id: c for c in state.commodities}
        lanes = sorted((ln for ln in state.lanes if ln.open), key=lambda ln: ln.distance_km)
        loader = _Loader(state)
        allocations: list[Allocation] = []
        holds: list[Hold] = []
        for b in sorted(state.batches, key=lambda x: (x.harvest_ts, x.batch_id)):
            c = comms[b.commodity_id]
            left = b.qty_kg
            for lane in lanes:
                if left <= KG_EPS:
                    break
                d = dests[lane.dest_id]
                for v in loader.candidates(d.dest_id, c.temp_group):
                    trip = estimate_trip(d=state.date, lane=lane, dest=d, commodity=c, vehicle=v,
                                         ambient_c=state.ambient_temp_c, cfg=cfg)
                    key = (d.dest_id, c.id, trip.eta.date())
                    want = min(left, remaining.get(key, 0.0), loader.space(v))
                    if want <= KG_EPS:
                        if remaining.get(key, 0.0) <= KG_EPS:
                            break  # no demand left here → next-nearest destination
                        continue
                    loader.put(v, d.dest_id, c.temp_group, want)
                    remaining[key] -= want
                    left -= want
                    allocations.append(make_allocation(state, cfg, b, d.dest_id, v, want, trip,
                                                       fc.get(key, (0.0, 0.0))[1]))
                    if left <= KG_EPS:
                        break
            if left > KG_EPS:
                holds.append(Hold(batch_id=b.batch_id, qty_kg=round(left, 4),
                                  reason="no destination with remaining demand or no truck space"))
        return Plan(date=state.date, planner_name=self.name, allocations=allocations, holds=holds,
                    vehicle_assignments=dict(loader.dest_of),
                    vehicle_setpoints=dict(loader.group),  # type: ignore[arg-type]
                    solver_meta=SolverMeta(status="heuristic"))


PLANNERS = {"fifo_nearest": FifoNearest}


def get_planner(name: str):
    if name not in PLANNERS:
        raise KeyError(f"unknown planner '{name}'; known: {sorted(PLANNERS)}")
    return PLANNERS[name]()
