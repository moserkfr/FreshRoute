"""FreshRoute planner: feasibility → MILP → discount → validator → explain.

``FreshRoutePlanner`` uses the full forecast (seasonality, weekday, festivals, regime);
``NoCalendarPlanner`` is the same planner on the no-calendar forecast (ablation that measures
what the festival calendar is worth).

If the MILP ends without a usable solution (infeasible, error, or time limit with no
incumbent) the plan comes from the B3 heuristic (``fefo_greedy``) on the same State and
``solver_meta.fallback_used`` is set. The plan is checked by the independent validator before it
is returned; any allocation it rejects is turned into a hold (expected: none).
"""

from __future__ import annotations

import time

from freshroute import discount as disc
from freshroute import feasibility, milp
from freshroute.config import AppConfig
from freshroute.explain import explain
from freshroute.logistics import estimate_trip
from freshroute.schemas import Allocation, Hold, Plan, SolverMeta, State
from freshroute.validator import validate


class FreshRoutePlanner:
    name = "freshroute"
    forecast_mode = "full"

    def __init__(self, time_limit_s: float | None = None):
        self.time_limit_s = time_limit_s
        self._seen: set[str] = set()       # batch ids in yesterday's State
        self._last_date = None
        self._arrivals: dict[str, float] = {}

    def _expected_arrivals(self, state: State) -> dict[str, float]:
        """Fresh kg per commodity expected each coming day ≈ what arrived today.

        Today's arrivals are the batches not in yesterday's State. Without a history (first
        day, or a one-off API call) every batch counts as new — the conservative choice, as
        it leaves the least room for held stock. A second call on the same day (mid-day
        replanning) reuses the morning's estimate.
        """
        if self._last_date == state.date:
            return dict(self._arrivals)
        consecutive = (self._last_date is not None
                       and (state.date - self._last_date).days == 1)
        out: dict[str, float] = {}
        for b in state.batches:
            if not consecutive or b.batch_id not in self._seen:
                out[b.commodity_id] = out.get(b.commodity_id, 0.0) + b.qty_kg
        self._seen = {b.batch_id for b in state.batches}
        self._last_date = state.date
        self._arrivals = out
        return dict(out)

    # ------------------------------------------------------------------ pipeline

    def plan(self, state: State, cfg: AppConfig) -> Plan:
        t0 = time.perf_counter()
        feas = feasibility.build(state, cfg)
        arrivals = self._expected_arrivals(state)
        try:
            res = milp.solve(state, cfg, feas, self.time_limit_s, arrivals)
        except Exception as exc:  # solver crash → heuristic fallback, never no plan
            return self._fallback(state, cfg, f"error: {exc}", t0, feas.pruning_ratio)
        if res.status not in ("optimal", "feasible"):
            return self._fallback(state, cfg, res.status, t0, feas.pruning_ratio)

        options = {(o.batch_id, o.dest_id, o.vehicle_id): o for o in feas.options}
        discounts = disc.decide(state, cfg, res.shipped, options, res.hold_value)
        allocations = self._allocations(state, cfg, res, options, discounts)
        holds = [Hold(batch_id=bid, qty_kg=kg, reason=self._hold_reason(state, cfg, bid, res))
                 for bid, kg in res.held.items()]
        used = {a.vehicle_id for a in allocations}
        plan = Plan(
            date=state.date, planner_name=self.name, allocations=allocations, holds=holds,
            vehicle_assignments={v: d for v, d in res.vehicle_dest.items() if v in used},
            vehicle_setpoints={v: g for v, g in res.vehicle_group.items() if v in used},  # type: ignore[misc]
            solver_meta=SolverMeta(status=res.status, objective=res.objective,
                                   best_bound=res.best_bound, gap=res.gap,
                                   time_s=time.perf_counter() - t0,
                                   pruning_ratio=feas.pruning_ratio))
        plan = self._enforce_validator(plan, state, cfg)
        shipped = {(a.batch_id, a.dest_id, a.vehicle_id): a.qty_kg for a in plan.allocations}
        held = {h.batch_id: h.qty_kg for h in plan.holds}
        vtypes = {v.vehicle_id: v.type for v in state.vehicles}
        reasons = explain(state, feas.options, shipped, held, res.hold_value, discounts, vtypes)
        meta = plan.solver_meta.model_copy(update={"time_s": time.perf_counter() - t0})
        return plan.model_copy(update={"reasons": reasons, "solver_meta": meta})

    # ------------------------------------------------------------------ steps

    @staticmethod
    def _allocations(state: State, cfg: AppConfig, res: milp.MilpResult, options: dict,
                     discounts: dict) -> list[Allocation]:
        from freshroute.baselines import make_allocation

        batches = {b.batch_id: b for b in state.batches}
        vehicles = {v.vehicle_id: v for v in state.vehicles}
        dests = {d.dest_id: d for d in state.destinations}
        lanes = {ln.dest_id: ln for ln in state.lanes}
        comms = {c.id: c for c in state.commodities}
        out = []
        for key, kg in sorted(res.shipped.items()):
            bid, did, vid = key
            o = options[key]
            b = batches[bid]
            trip = estimate_trip(d=state.date, lane=lanes[did], dest=dests[did],
                                 commodity=comms[b.commodity_id], vehicle=vehicles[vid],
                                 ambient_c=state.ambient_temp_c, cfg=cfg)
            out.append(make_allocation(state, cfg, b, did, vehicles[vid], kg, trip, o.price,
                                       discount_pct=discounts[key].pct))
        return out

    @staticmethod
    def _hold_reason(state: State, cfg: AppConfig, bid: str, res: milp.MilpResult) -> str:
        vh = res.hold_value.get(bid, 0.0)
        if vh <= -cfg.default.cost.disposal_per_kg + 1e-9:
            if disc.clearance_needed(state, cfg, bid):
                return (f"cannot reach any buyer in sellable condition; recommend local "
                        f"clearance at {cfg.default.planner.clearance_discount_pct:.0f}% off")
            return "no feasible buyer today or later; dispose or clear locally"
        return f"worth more later: hold value ₹{vh:.1f}/kg after storage"

    @staticmethod
    def _enforce_validator(plan: Plan, state: State, cfg: AppConfig) -> Plan:
        violations = validate(plan, state, cfg)
        if not violations:
            return plan
        bad = {(v.batch_id, v.vehicle_id) for v in violations if v.batch_id}
        bad_vehicles = {v.vehicle_id for v in violations if v.batch_id is None}
        keep, moved = [], {}
        for a in plan.allocations:
            if (a.batch_id, a.vehicle_id) in bad or a.vehicle_id in bad_vehicles:
                moved[a.batch_id] = moved.get(a.batch_id, 0.0) + a.qty_kg
            else:
                keep.append(a)
        holds = list(plan.holds) + [Hold(batch_id=b, qty_kg=round(kg, 4),
                                         reason="removed by validator check")
                                    for b, kg in moved.items()]
        return plan.model_copy(update={"allocations": keep, "holds": holds})

    def _fallback(self, state: State, cfg: AppConfig, why: str, t0: float,
                  pruning: float) -> Plan:
        from freshroute.baselines import FefoGreedy

        p = FefoGreedy().plan(state, cfg)
        meta = SolverMeta(status=f"fallback ({why})", time_s=time.perf_counter() - t0,
                          fallback_used=True, pruning_ratio=pruning)
        return p.model_copy(update={"planner_name": self.name, "solver_meta": meta})


class NoCalendarPlanner(FreshRoutePlanner):
    name = "ours_no_calendar"
    forecast_mode = "no_calendar"
