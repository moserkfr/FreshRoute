"""Allocation MILP: which batch goes where, on which truck, and what is held back.

Variables
  x[o]      share of the batch shipped through feasible option o = (batch, dest, vehicle)
  y[v,d]    vehicle v serves destination d today (binary; at most one destination per vehicle)
  g[v,k]    reefer v runs temperature group k today (binary; at most one)
  h[b]      share of batch b held at the hub
  s[d,c,j]  kg delivered to (d, c) that fall in demand segment j (see below)
  u[d,c]    unmet forecast demand (kg)

Objective (₹, maximise)
    Σ x·Q·revenue_per_kg                 revenue at forecast price × m(arrival quality) − handling
  − Σ y·(fixed + ₹/km · 2 · distance)    round-trip truck cost
  − Σ s[j]·(1 − p_j)·(price + disposal)  expected unsold: kg in segment j sell with prob. p_j
  − π · Σ u                              unmet-demand penalty (objective only, never booked)
  + Σ hd·Q·V_hold[b,d] − Σ (h − hd)·Q·disposal
                                         value of holding: held kg sell later at destination d
                                         only within the market room fresh arrivals leave

Demand segments refine the "oversupply" penalty: shipped kg are split at multiples of the
forecast demand (planner.demand_breakpoints); a kg in segment j sells only if demand exceeds
the segment's midpoint, which for lognormal demand has probability p_j. The segments' penalty
rises with j, so the LP fills them in order without extra binaries.

V_hold[b,d] (per kg) = best over k = 1..H days of
    price(d, t + k + travel) · m(quality after k days at the hub and the trip)
    − freight/kg − handling − storage · k.
Holding is demand-aware: each future day brings fresh stock (estimated from today's arrivals)
that takes the best markets first, so held kg can only use the demand left over (hold_room).
Without that limit, holding looks free every day and storable stock (onion, mango) piles up.
"""

from __future__ import annotations

import math
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import timedelta

from ortools.linear_solver import pywraplp

from freshroute.config import AppConfig
from freshroute.feasibility import (
    FeasibleSet,
    Option,
    forecast_lookup,
    quality_p10,
    sellable_margin,
)
from freshroute.logistics import estimate_trip
from freshroute.schemas import State, Vehicle
from freshroute.shelf_life import consumption_rate, price_multiplier, quality

HOLD_MIN_KG = 0.01   # smaller remainders are rounding, not a decision to hold


@dataclass
class MilpResult:
    status: str
    shipped: dict[tuple[str, str, str], float]     # (batch, dest, vehicle) → kg
    held: dict[str, float]                         # batch → kg
    vehicle_dest: dict[str, str]
    vehicle_group: dict[str, str]
    objective: float | None = None
    best_bound: float | None = None
    gap: float | None = None
    time_s: float = 0.0
    hold_value: dict[str, float] = field(default_factory=dict)   # batch → ₹/kg
    y_values: dict[tuple[str, str], float] = field(default_factory=dict)  # (vehicle, dest) → y


def trip_cost(v: Vehicle, distance_km: float) -> float:
    return v.fixed_cost_per_trip + v.cost_per_km * 2 * distance_km


def freight_per_kg(state: State, dest_id: str, reefer: bool) -> float:
    """Cheapest full-truck ₹/kg to ``dest_id`` among available vehicles of the given kind."""
    lane = next(ln for ln in state.lanes if ln.dest_id == dest_id)
    costs = [trip_cost(v, lane.distance_km) / v.capacity_kg for v in state.vehicles
             if (v.type == "reefer") == reefer]
    return min(costs) if costs else math.inf


def hub_temperature(state: State, cfg: AppConfig) -> float:
    """Hub storage follows ambient: configured packhouse temp + today's ambient anomaly."""
    return (cfg.default.cost.hub_storage_temp_c
            + state.ambient_temp_c - cfg.default.simulation.ambient_temp_c)


def hold_values(state: State, cfg: AppConfig) -> dict[tuple[str, str], float]:
    """V_hold per (batch, destination), ₹/kg: best over k = 1..H days of selling there later.

    Pairs that are never feasible within the horizon are omitted (their value is −disposal).
    """
    pc = cfg.default.planner
    risk_p = float(state.config_snapshot.get("risk_p", pc.risk_p))
    horizon = int(state.config_snapshot.get("horizon_days", cfg.default.market.horizon_days))
    bands = cfg.default.shelf_life.price_bands
    chill = cfg.default.shelf_life.chill_penalty
    fc = forecast_lookup(state)
    last_date = max(m.date for m in state.market_forecast)
    comms = {c.id: c for c in state.commodities}
    dests = {d.dest_id: d for d in state.destinations}
    hub_t = hub_temperature(state, cfg)
    ambient = next((v for v in state.vehicles if v.type == "ambient"), None)
    reefer = next((v for v in state.vehicles if v.type == "reefer"), None)
    freight = {(ln.dest_id, kind): freight_per_kg(state, ln.dest_id, kind == "reefer")
               for ln in state.lanes for kind in ("ambient", "reefer")}
    out: dict[tuple[str, str], float] = {}
    for b in state.batches:
        c = comms[b.commodity_id]
        rate = consumption_rate(hub_t, c, chill)
        for k in range(1, horizon + 1):
            aged = b.model_copy(update={"remaining_life_mean_days":
                                        b.remaining_life_mean_days - k * rate})
            day = state.date + timedelta(days=k)
            for ln in state.lanes:
                if not ln.open:
                    continue
                d = dests[ln.dest_id]
                for v, kind in ((ambient, "ambient"), (reefer, "reefer")):
                    if v is None:
                        continue
                    trip = estimate_trip(d=day, lane=ln, dest=d, commodity=c, vehicle=v,
                                         ambient_c=state.ambient_temp_c, cfg=cfg)
                    dl = trip.delta_life_days
                    if sellable_margin(aged, c, dl, risk_p) < pc.safety_margin_days:
                        continue
                    if quality_p10(aged, c, dl) < d.min_quality_accepted:
                        continue
                    arr = min(trip.eta.date(), last_date)
                    if (d.dest_id, c.id, arr) not in fc:
                        continue
                    price = fc[(d.dest_id, c.id, arr)][2]
                    q = quality(aged.remaining_life_mean_days - dl, c)
                    val = (price * price_multiplier(q, bands) - d.handling_cost_per_kg
                           - c.handling_cost_per_kg - freight[(d.dest_id, kind)]
                           - c.storage_cost_per_kg_day * k)
                    key = (b.batch_id, d.dest_id)
                    out[key] = max(out.get(key, -math.inf), val)
    return out


def hold_room(state: State, cfg: AppConfig,
              arrivals: dict[str, float]) -> dict[tuple[str, str], float]:
    """Market room (kg) left for held stock at each (commodity, destination) over the horizon.

    Each future day brings fresh stock (``arrivals`` kg per commodity, estimated from today's
    arrivals). Fresh stock takes the destinations with the best net value first; held stock can
    only be sold into the demand that is left.
    """
    horizon = int(state.config_snapshot.get("horizon_days", cfg.default.market.horizon_days))
    fc = forecast_lookup(state)
    last_date = max(m.date for m in state.market_forecast)
    dests = {d.dest_id: d for d in state.destinations}
    comms = {c.id: c for c in state.commodities}
    open_lanes = [ln for ln in state.lanes if ln.open]
    room: dict[tuple[str, str], float] = defaultdict(float)
    for c in state.commodities:
        for k in range(1, horizon + 1):
            day = min(state.date + timedelta(days=k), last_date)
            ranked = []
            for ln in open_lanes:
                dem, _, price, _ = fc.get((ln.dest_id, c.id, day), (0.0, 0.0, 0.0, 0.0))
                net = (price - dests[ln.dest_id].handling_cost_per_kg
                       - comms[c.id].handling_cost_per_kg
                       - freight_per_kg(state, ln.dest_id, reefer=False))
                ranked.append((net, ln.dest_id, dem))
            fresh = arrivals.get(c.id, 0.0)
            for _net, did, dem in sorted(ranked, reverse=True):
                take = min(fresh, dem)
                fresh -= take
                room[(c.id, did)] += dem - take
    return dict(room)


def sell_probabilities(breakpoints: list[float], sigma_log: float) -> list[float]:
    """P(demand > midpoint of each segment) for mean-1 lognormal demand with log-sd σ."""
    edges = [0.0, *breakpoints]
    probs = []
    for lo, hi in zip(edges[:-1], edges[1:], strict=True):
        mid = (lo + hi) / 2
        if sigma_log <= 0:
            probs.append(1.0 if mid < 1.0 else 0.0)
            continue
        mu = -0.5 * sigma_log**2
        z = (math.log(mid) - mu) / sigma_log
        probs.append(0.5 * math.erfc(z / math.sqrt(2)))
    return probs


def solve(state: State, cfg: AppConfig, feas: FeasibleSet,
          time_limit_s: float | None = None,
          arrivals: dict[str, float] | None = None) -> MilpResult:
    """Solve today's allocation. ``arrivals``: expected fresh kg per commodity per day.

    Two phases. The LP relaxation (trucks may go "partly" to several destinations) solves in
    well under a second and its value is a valid upper bound. Each truck is then limited to
    the destinations it uses in the relaxation, and the MILP is solved on that smaller model —
    the truck/destination choices are what make the full MILP slow. The reported gap is
    measured against the relaxation bound, so it is honest about what may have been lost.
    """
    t0 = time.perf_counter()
    pc = cfg.default.planner
    limit = time_limit_s if time_limit_s is not None else pc.time_limit_s
    vhold = hold_values(state, cfg)
    room = hold_room(state, cfg, arrivals or {})
    lp = _solve_model(state, cfg, feas.options, vhold, room, relax=True, time_limit_s=limit)
    if lp.status not in ("optimal", "feasible"):
        return lp
    allowed = {k for k, v in lp.y_values.items() if v > pc.lp_fix_threshold}
    opts = [o for o in feas.options if (o.vehicle_id, o.dest_id) in allowed]
    left = max(0.5, limit - (time.perf_counter() - t0))
    res = _solve_model(state, cfg, opts, vhold, room, relax=False, time_limit_s=left)
    res.time_s = time.perf_counter() - t0
    if res.objective is not None and lp.objective is not None:
        res.best_bound = lp.objective
        res.gap = max(0.0, lp.objective - res.objective) / max(1.0, abs(res.objective))
    return res


def _solve_model(state: State, cfg: AppConfig, opts: list[Option],
                 vhold: dict[tuple[str, str], float], room: dict[tuple[str, str], float],
                 relax: bool, time_limit_s: float) -> MilpResult:
    """Build and solve the model over ``opts``; ``relax`` makes every integer continuous."""
    t0 = time.perf_counter()
    pc = cfg.default.planner
    cost = cfg.default.cost
    solver = pywraplp.Solver.CreateSolver(pc.solver)
    if solver is None:
        raise RuntimeError(f"OR-Tools solver {pc.solver} unavailable")
    solver.SetTimeLimit(int(1000 * time_limit_s))
    if pc.node_limit and not relax:  # work-based stop: same seed, same plan on any machine
        solver.SetSolverSpecificParametersAsString(f"limits/totalnodes = {pc.node_limit}")
    batches = {b.batch_id: b for b in state.batches}
    vehicles = {v.vehicle_id: v for v in state.vehicles}
    lanes = {ln.dest_id: ln for ln in state.lanes}
    fc = forecast_lookup(state)
    best_hold = {bid: max((v for (b2, _), v in vhold.items() if b2 == bid),
                          default=-cost.disposal_per_kg) for bid in batches}
    inf = solver.infinity()

    def binary(name: str) -> pywraplp.Variable:
        return solver.NumVar(0, 1, name) if relax else solver.BoolVar(name)

    # Variables -------------------------------------------------------------------------------
    x: dict[int, pywraplp.Variable] = {}
    for i in range(len(opts)):
        x[i] = (binary(f"x{i}") if not pc.split_batches else solver.NumVar(0, 1, f"x{i}"))
    h = {bid: solver.NumVar(0, 1, f"h_{bid}") for bid in batches}
    pairs = sorted({(o.vehicle_id, o.dest_id) for o in opts})
    y = {p: binary(f"y_{p[0]}_{p[1]}") for p in pairs}
    groups = sorted({o.group for o in opts if o.group})
    reefers = sorted({o.vehicle_id for o in opts if o.group})
    g = {(v, k): binary(f"g_{v}_{k}") for v in reefers for k in groups}

    by_batch: dict[str, list[int]] = defaultdict(list)
    by_vd: dict[tuple[str, str], list[int]] = defaultdict(list)
    by_dc: dict[tuple[str, str], list[int]] = defaultdict(list)
    for i, o in enumerate(opts):
        by_batch[o.batch_id].append(i)
        by_vd[(o.vehicle_id, o.dest_id)].append(i)
        by_dc[(o.dest_id, o.commodity_id)].append(i)

    obj = solver.Objective()
    obj.SetMaximization()

    # 1. every batch is shipped or held; held kg are sold later only into the market room that
    #    fresh arrivals leave (hd[b,d] at V_hold[b,d]); the rest of a hold is worth −disposal.
    disposal = cost.disposal_per_kg
    hd: dict[tuple[str, str], pywraplp.Variable] = {}
    for (bid, did), val in vhold.items():
        if val > -disposal:
            hd[(bid, did)] = solver.NumVar(0, 1, f"hd_{bid}_{did}")
            obj.SetCoefficient(hd[(bid, did)], batches[bid].qty_kg * val)
    hd_by_batch: dict[str, list] = defaultdict(list)
    hd_by_cd: dict[tuple[str, str], list] = defaultdict(list)
    for (bid, did), var in hd.items():
        hd_by_batch[bid].append(var)
        hd_by_cd[(batches[bid].commodity_id, did)].append((bid, var))
    for bid in batches:
        solver.Add(sum(x[i] for i in by_batch[bid]) + h[bid] == 1)
        solver.Add(sum(hd_by_batch[bid]) <= h[bid])
        obj.SetCoefficient(h[bid], -batches[bid].qty_kg * disposal)
        for var in hd_by_batch[bid]:  # hd replaces part of h's −disposal with V_hold
            obj.SetCoefficient(var, obj.GetCoefficient(var) + batches[bid].qty_kg * disposal)
    for (cid, did), items in hd_by_cd.items():
        solver.Add(sum(var * batches[bid].qty_kg for bid, var in items)
                   <= room.get((cid, did), 0.0))
    for i, o in enumerate(opts):
        obj.SetCoefficient(x[i], batches[o.batch_id].qty_kg * o.revenue_per_kg)

    # 2–3. capacity on chosen destination; one destination per vehicle
    for (vid, did), idx in by_vd.items():
        solver.Add(sum(x[i] * batches[opts[i].batch_id].qty_kg for i in idx)
                   <= vehicles[vid].capacity_kg * y[(vid, did)])
        obj.SetCoefficient(y[(vid, did)], -trip_cost(vehicles[vid], lanes[did].distance_km))
    for vid in sorted({p[0] for p in pairs}):
        solver.Add(sum(y[p] for p in pairs if p[0] == vid) <= 1)
    # Symmetry breaking: identical trucks (same type, capacity, costs) are interchangeable, so
    # order them by destination index (unused last). Removes equivalent plans from the search.
    dest_ix = {d: k + 1 for k, d in enumerate(sorted(lanes))}
    unused = len(dest_ix) + 1

    def rank(vid: str):
        ys = [(dest_ix[d], y[(v, d)]) for (v, d) in pairs if v == vid]
        return sum(k * var for k, var in ys) + unused * (1 - sum(var for _, var in ys))

    def signature(v: Vehicle) -> tuple:
        return (v.type, v.capacity_kg, v.cost_per_km, v.fixed_cost_per_trip, v.available)

    used_vehicles = sorted({p[0] for p in pairs})
    for a, b in zip(used_vehicles[:-1], used_vehicles[1:], strict=True):
        if signature(vehicles[a]) == signature(vehicles[b]):
            solver.Add(rank(a) <= rank(b))

    # 4. demand balance with sell-probability segments
    bp = pc.demand_breakpoints
    for (did, cid), idx in by_dc.items():
        arr = opts[idx[0]].arrival_date
        dem, dem_sd, price, _ = fc[(did, cid, arr)]
        delivered = sum(x[i] * batches[opts[i].batch_id].qty_kg for i in idx)
        sigma = math.sqrt(math.log(1 + (dem_sd / dem) ** 2)) if dem > 0 else 0.0
        probs = sell_probabilities(bp, sigma)
        edges = [0.0, *bp]
        segs = []
        for j, p in enumerate(probs):
            width = (edges[j + 1] - edges[j]) * dem
            s = solver.NumVar(0, width, f"s_{did}_{cid}_{j}")
            obj.SetCoefficient(s, -(1 - p) * (price + cost.disposal_per_kg))
            segs.append(s)
        tail = solver.NumVar(0, inf, f"s_{did}_{cid}_tail")   # beyond the last breakpoint
        obj.SetCoefficient(tail, -(price + cost.disposal_per_kg))
        solver.Add(sum(segs) + tail == delivered)
        u = solver.NumVar(0, inf, f"u_{did}_{cid}")
        solver.Add(u >= dem - delivered)
        obj.SetCoefficient(u, -cost.unmet_demand_penalty_per_kg)

    # 5. reefer compatibility: one group per reefer, options must match it
    for vid in reefers:
        solver.Add(sum(g[(vid, k)] for k in groups) <= 1)
    for i, o in enumerate(opts):
        if o.group:
            solver.Add(x[i] <= g[(o.vehicle_id, o.group)])

    # 7. optional fairness: each (d, c) gets ≥ φ · min(demand, its share of available stock)
    if pc.fairness_enabled:
        avail: dict[str, float] = defaultdict(float)
        for b in batches.values():
            avail[b.commodity_id] += b.qty_kg
        dem_c: dict[str, float] = defaultdict(float)
        for (did, cid), idx in by_dc.items():
            dem_c[cid] += fc[(did, cid, opts[idx[0]].arrival_date)][0]
        for (did, cid), idx in by_dc.items():
            dem = fc[(did, cid, opts[idx[0]].arrival_date)][0]
            share = avail[cid] * dem / dem_c[cid] if dem_c[cid] > 0 else 0.0
            solver.Add(sum(x[i] * batches[opts[i].batch_id].qty_kg for i in idx)
                       >= pc.fairness_phi * min(dem, share))

    params = pywraplp.MPSolverParameters()
    params.SetDoubleParam(pywraplp.MPSolverParameters.RELATIVE_MIP_GAP, pc.mip_gap)
    status = solver.Solve(params)
    names = {pywraplp.Solver.OPTIMAL: "optimal", pywraplp.Solver.FEASIBLE: "feasible",
             pywraplp.Solver.INFEASIBLE: "infeasible", pywraplp.Solver.UNBOUNDED: "unbounded",
             pywraplp.Solver.ABNORMAL: "abnormal", pywraplp.Solver.NOT_SOLVED: "not_solved"}
    st = names.get(status, str(status))
    elapsed = time.perf_counter() - t0
    if status not in (pywraplp.Solver.OPTIMAL, pywraplp.Solver.FEASIBLE):
        return MilpResult(status=st, shipped={}, held={}, vehicle_dest={}, vehicle_group={},
                          time_s=elapsed, hold_value=best_hold)
    y_values = {p: var.solution_value() for p, var in y.items()}

    # Rounded to 0.1 g: far inside the validator's 1 g tolerances, and a batch shipped in full
    # leaves no crumbs at the hub (the simulator clears lots below 0.1 g).
    shipped: dict[tuple[str, str, str], float] = {}
    for i, o in enumerate(opts):
        kg = round(x[i].solution_value() * batches[o.batch_id].qty_kg, 4)
        if kg >= pc.min_alloc_kg:
            shipped[(o.batch_id, o.dest_id, o.vehicle_id)] = kg
    vehicle_dest = {p[0]: p[1] for p, var in y.items() if var.solution_value() > 0.5}
    vehicle_group = {v: k for (v, k), var in g.items() if var.solution_value() > 0.5}
    held = {}
    for bid, b in batches.items():
        rest = b.qty_kg - sum(kg for (bb, _, _), kg in shipped.items() if bb == bid)
        if rest >= HOLD_MIN_KG:
            held[bid] = round(rest, 4)
    objective = solver.Objective().Value()
    bound = solver.Objective().BestBound()
    gap = abs(bound - objective) / max(1.0, abs(objective)) if st == "feasible" else 0.0
    return MilpResult(status=st, shipped=shipped, held=held, vehicle_dest=vehicle_dest,
                      vehicle_group=vehicle_group, objective=objective, best_bound=bound,
                      gap=gap, time_s=elapsed, hold_value=best_hold, y_values=y_values)


__all__ = ["MilpResult", "Option", "freight_per_kg", "hold_values", "solve", "trip_cost"]
