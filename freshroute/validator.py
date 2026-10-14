"""Independent plan validator.

Every plan — ours and every baseline — passes through here before the simulator executes it.
This module intentionally does NOT import ``shelf_life`` or ``logistics``: it recomputes ETAs
and Q10 life consumption with its own few lines of arithmetic, so a bug in the planner's
shared helpers cannot hide its own violations.

Violation types
  hard (physically impossible / against the rules of the depot; always dropped):
    unknown_ref, negative_qty, stock_conservation, vehicle_capacity, one_destination,
    vehicle_unavailable, lane_closed, delivery_window, reefer_compatibility
  policy (expected-quality rules; dropped only when simulation.drop_violations == "all"):
    shelf_life_chance, min_quality
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, time, timedelta
from statistics import NormalDist

from freshroute.config import AppConfig
from freshroute.schemas import Plan, State, Violation

HARD_TYPES = frozenset({
    "unknown_ref", "negative_qty", "stock_conservation", "vehicle_capacity", "one_destination",
    "vehicle_unavailable", "lane_closed", "delivery_window", "reefer_compatibility",
})
POLICY_TYPES = frozenset({"shelf_life_chance", "min_quality"})
TOL_KG = 1e-3
TOL_H = 0.05


def _rate(temp: float, t_ref: float, q10: float, chill_thr: float | None,
          chill_pen: float) -> float:
    r = q10 ** ((temp - t_ref) / 10.0)
    return r * chill_pen if chill_thr is not None and temp < chill_thr else r


def _expected_eta(depart: datetime, travel_h: float, open_h: int, close_h: int) -> datetime:
    arr = depart + timedelta(hours=travel_h)
    midnight = datetime.combine(arr.date(), time(0), tzinfo=arr.tzinfo)
    if arr < midnight + timedelta(hours=open_h):
        return midnight + timedelta(hours=open_h)
    if arr < midnight + timedelta(hours=close_h):
        return arr
    return midnight + timedelta(days=1, hours=open_h)


def validate(plan: Plan, state: State, cfg: AppConfig) -> list[Violation]:
    """Return every violation found in ``plan`` against ``state`` (empty list = valid)."""
    out: list[Violation] = []

    def add(t: str, detail: str, batch: str | None = None, vehicle: str | None = None) -> None:
        out.append(Violation(type=t, batch_id=batch, vehicle_id=vehicle, detail=detail))

    batches = {b.batch_id: b for b in state.batches}
    vehicles = {v.vehicle_id: v for v in state.vehicles}
    dests = {d.dest_id: d for d in state.destinations}
    lanes = {ln.dest_id: ln for ln in state.lanes}
    comms = {c.id: c for c in state.commodities}
    sim = cfg.default.simulation
    risk_p = float(state.config_snapshot.get("risk_p", cfg.default.planner.risk_p))
    z = NormalDist().inv_cdf(risk_p)
    z10 = NormalDist().inv_cdf(0.9)
    chill_pen = cfg.default.shelf_life.chill_penalty

    shipped: dict[str, float] = defaultdict(float)
    load: dict[str, float] = defaultdict(float)
    v_dests: dict[str, set[str]] = defaultdict(set)
    v_groups: dict[str, set[str]] = defaultdict(set)

    for a in plan.allocations:
        b, v, d = batches.get(a.batch_id), vehicles.get(a.vehicle_id), dests.get(a.dest_id)
        if b is None or v is None or d is None or a.dest_id not in lanes:
            add("unknown_ref", f"allocation references unknown batch/vehicle/destination "
                f"({a.batch_id}, {a.vehicle_id}, {a.dest_id})", a.batch_id, a.vehicle_id)
            continue
        if a.qty_kg <= 0:
            add("negative_qty", f"qty {a.qty_kg} kg ≤ 0", a.batch_id, a.vehicle_id)
        shipped[a.batch_id] += a.qty_kg
        load[a.vehicle_id] += a.qty_kg
        v_dests[a.vehicle_id].add(a.dest_id)
        c = comms[b.commodity_id]
        v_groups[a.vehicle_id].add(c.temp_group)
        if not v.available:
            add("vehicle_unavailable", f"{v.vehicle_id} is not available today",
                a.batch_id, v.vehicle_id)
        lane = lanes[a.dest_id]
        if not lane.open:
            add("lane_closed", f"lane to {a.dest_id} is closed", a.batch_id, v.vehicle_id)

        # Delivery window: recompute the ETA from the lane mean and compare.
        eta = _expected_eta(a.depart_ts, lane.travel_time_h_mean, d.receive_open_h,
                            d.receive_close_h)
        h = a.eta_ts.astimezone(eta.tzinfo).hour + a.eta_ts.minute / 60
        in_window = d.receive_open_h <= h < d.receive_close_h
        if not in_window or abs((a.eta_ts - eta).total_seconds()) / 3600 > TOL_H:
            add("delivery_window", f"ETA {a.eta_ts:%d %b %H:%M} vs window "
                f"{d.receive_open_h:02d}–{d.receive_close_h:02d}h (expected {eta:%d %b %H:%M})",
                a.batch_id, v.vehicle_id)

        # Shelf life at arrival, recomputed independently.
        amb = state.ambient_temp_c
        if v.type == "reefer":
            grp = plan.vehicle_setpoints.get(v.vehicle_id, c.temp_group)
            cargo = cfg.fleet.reefer_setpoints_c[grp]
        else:
            cargo = amb
        on_truck = (eta - a.depart_ts).total_seconds() / 3600
        r_amb = _rate(amb, c.t_ref_c, c.q10, c.chill_threshold_c, chill_pen)
        r_cargo = _rate(cargo, c.t_ref_c, c.q10, c.chill_threshold_c, chill_pen)
        dl = (sim.load_hours * r_amb + on_truck * r_cargo + sim.unload_hours * r_amb) / 24
        l_min = c.min_sellable_quality * c.shelf_life_ref_days
        if b.remaining_life_mean_days - z * b.remaining_life_sd_days < dl + l_min - 1e-9:
            add("shelf_life_chance", f"P(sellable on arrival) < {risk_p:.2f}: life "
                f"{b.remaining_life_mean_days:.2f}±{b.remaining_life_sd_days:.2f} ref-d, trip uses "
                f"{dl:.2f}, need ≥ {l_min:.2f} left", a.batch_id, v.vehicle_id)
        q_p10 = (b.remaining_life_mean_days - z10 * b.remaining_life_sd_days - dl) \
            / c.shelf_life_ref_days
        if q_p10 < d.min_quality_accepted - 1e-9:
            add("min_quality", f"arrival quality p10 {q_p10:.0%} < {d.name} minimum "
                f"{d.min_quality_accepted:.0%}", a.batch_id, v.vehicle_id)

    for bid, qty in shipped.items():
        if bid in batches and qty > batches[bid].qty_kg + TOL_KG:
            add("stock_conservation", f"ships {qty:.1f} kg of {batches[bid].qty_kg:.1f} kg",
                bid)
    for h in plan.holds:
        if h.batch_id in batches and shipped.get(h.batch_id, 0) + h.qty_kg \
                > batches[h.batch_id].qty_kg + TOL_KG:
            add("stock_conservation", f"ship + hold exceed stock for {h.batch_id}", h.batch_id)
    for vid, kg in load.items():
        if vid in vehicles and kg > vehicles[vid].capacity_kg + TOL_KG:
            add("vehicle_capacity", f"{kg:.0f} kg > capacity {vehicles[vid].capacity_kg:.0f} kg",
                vehicle=vid)
    for vid, ds in v_dests.items():
        assigned = plan.vehicle_assignments.get(vid)
        if len(ds) > 1 or (assigned is not None and assigned not in ds):
            add("one_destination", f"{vid} serves {sorted(ds)} (assigned {assigned})",
                vehicle=vid)
    for vid, groups in v_groups.items():
        v = vehicles.get(vid)
        if v is None or v.type != "reefer":
            continue
        sp = plan.vehicle_setpoints.get(vid)
        if len(groups) > 1 or (sp is not None and sp not in groups):
            add("reefer_compatibility", f"reefer {vid} carries groups {sorted(groups)} "
                f"at setpoint {sp}", vehicle=vid)
    return out


def droppable(violations: list[Violation], policy: str) -> tuple[set[str], set[tuple[str, str]]]:
    """Which allocations the simulator must drop.

    Returns (vehicle_ids whose whole load is dropped, {(batch_id, vehicle_id)} pairs dropped).
    Vehicle-level hard violations (capacity, one_destination, reefer mix) void the whole truck.
    """
    whole: set[str] = set()
    pairs: set[tuple[str, str]] = set()
    for v in violations:
        if policy == "hard" and v.type not in HARD_TYPES:
            continue
        if v.batch_id is None and v.vehicle_id is not None:
            whole.add(v.vehicle_id)
        elif v.batch_id is not None and v.vehicle_id is not None:
            pairs.add((v.batch_id, v.vehicle_id))
        elif v.batch_id is not None:
            pairs.add((v.batch_id, "*"))
    return whole, pairs
