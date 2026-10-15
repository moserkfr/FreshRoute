"""Multi-day simulator: executes plans against sampled reality and keeps the ledger.

Separation of knowledge:
  * Planners only ever receive a ``State``: mean/sd shelf life, lane mean travel times and a
    forecast built in the planner's own ``forecast_mode``.
  * The simulator alone reads the instance's truth: each batch's ``actual_life_days``, sampled
    travel delays, actual ambient temperature, true demand and price.

Common random numbers: travel delays are drawn from a stream keyed by
(seed, day, vehicle index) with a fixed number of draws, so every planner run on the same seed
meets the same road conditions, batches and markets.

Day loop (one dispatch wave per day):
  1. Morning: age held stock 24 h at hub storage temp (+ storage cost); stock whose *actual*
     quality falls below ``min_sellable_quality`` is spoiled_storage. Add today's arrivals.
  2. Build the State from forecasts only; call the planner.
  3. Validate; drop violating allocations (per ``simulation.drop_violations``); count.
  4. Event hook: unannounced events dated today become known. With replan=true the planner is
     called again on the updated State (no truck has left yet). Without replanning, loads on a
     closed lane or a broken-down vehicle simply do not leave the hub.
  5. Execute: actual travel (delay process), actual cargo temperature, actual life.
  6. Settle every load that arrives today: rejected / spoiled in transit / sold / unsold.
  7. Mass-balance assertion.
"""

from __future__ import annotations

import time as _time
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from freshroute.config import AppConfig, load_config
from freshroute.generator import Instance, rng_for
from freshroute.logistics import Planner, cargo_temp_c, depart_ts, roll_to_window
from freshroute.market_model import forecast
from freshroute.schemas import IST, Batch, DayOutcome, Plan, RunResult, State
from freshroute.shelf_life import consumption_rate, delta_life, price_multiplier, quality
from freshroute.validator import droppable, validate

MASS_TOL_KG = 1e-4


class MassBalanceError(AssertionError):
    pass


@dataclass
class HubLot:
    batch_id: str
    commodity_id: str
    qty: float
    grade: str
    harvest_ts: datetime
    mean_life: float      # planner-visible estimate (reference-days)
    sd_life: float
    actual_life: float    # truth — never shown to planners
    cost_basis: float


@dataclass
class Shipment:
    batch_id: str
    dest_id: str
    commodity_id: str
    qty: float
    q_actual: float
    discount_pct: float
    arrival_date: date


class Simulator:
    """Runs one planner over one instance. Construct once per (instance, planner) run."""

    def __init__(self, instance: Instance, planner: Planner, cfg: AppConfig | None = None,
                 replan: bool | None = None, risk_p: float | None = None):
        self.inst = instance
        self.planner = planner
        self.cfg = cfg or load_config()
        self.replan = self.cfg.default.simulation.replan if replan is None else replan
        self.risk_p = risk_p if risk_p is not None else self.cfg.default.planner.risk_p
        self.comms = {c.id: c for c in instance.commodities}
        self.dests = {d.dest_id: d for d in instance.destinations}
        self.lanes = {ln.dest_id: ln for ln in instance.lanes}
        self.vidx = {v.vehicle_id: i for i, v in enumerate(instance.vehicles)}
        self.hub_temp = (self.cfg.default.cost.hub_storage_temp_c
                         + float(instance.overrides.get("ambient_delta_c", 0.0)))
        self.inventory: dict[str, HubLot] = {}
        self.transit: list[Shipment] = []
        self.revealed: list[dict] = []
        m = instance.market
        self.truth = {(r.day, r.dest_id, r.commodity_id): (r.demand_true, r.price_true)
                      for r in m.itertuples(index=False)}
        self.cum = defaultdict(float)

    # ------------------------------------------------------------------ knowledge

    def known_events(self, day: int) -> list[dict]:
        """Events the planner knows about on ``day``'s morning."""
        return [e for e in self.inst.events
                if e["announced"] or (e in self.revealed and e["day"] <= day)]

    @staticmethod
    def _in_force(ev: dict, day: int) -> bool:
        dur = ev["params"].get("duration_days")
        return ev["day"] <= day and (dur is None or day < ev["day"] + int(dur))

    def build_state(self, day: int) -> State:
        known = self.known_events(day)
        closed = {e["params"]["dest"] for e in known
                  if e["type"] == "lane_closure" and self._in_force(e, day)}
        down = {e["params"]["vehicle_id"] for e in known
                if e["type"] == "vehicle_down" and self._in_force(e, day)}
        hub = self.inst.hub_id
        batches = [Batch(batch_id=lot.batch_id, commodity_id=lot.commodity_id,
                         qty_kg=round(lot.qty, 4), grade=lot.grade, harvest_ts=lot.harvest_ts,
                         location_id=hub, remaining_life_mean_days=round(lot.mean_life, 6),
                         remaining_life_sd_days=round(lot.sd_life, 6),
                         cost_basis_per_kg=lot.cost_basis)
                   for lot in self.inventory.values() if lot.qty > MASS_TOL_KG]
        mode = getattr(self.planner, "forecast_mode", "full")
        market_events = [e for e in known if not e["announced"]]  # announced already in *_full
        return State(
            date=self.inst.date_of(day),
            ambient_temp_c=self.inst.ambient_forecast_c[min(day, self.inst.days - 1)],
            batches=batches,
            vehicles=[v.model_copy(update={"available": v.vehicle_id not in down})
                      for v in self.inst.vehicles],
            destinations=list(self.inst.destinations),
            lanes=[ln.model_copy(update={"open": ln.dest_id not in closed})
                   for ln in self.inst.lanes],
            market_forecast=forecast(self.inst.market, day, self.cfg.default.market.horizon_days,
                                     mode, market_events),
            commodities=list(self.inst.commodities),
            hub_id=hub,
            config_snapshot={"risk_p": self.risk_p, "forecast_mode": mode, "day": day,
                             "horizon_days": self.cfg.default.market.horizon_days},
        )

    # ------------------------------------------------------------------ main loop

    def run(self) -> RunResult:
        outcomes: list[DayOutcome] = []
        for day in range(self.inst.days):
            outcomes.append(self._day(day))
        # Wind-down: no new stock arrives, planners keep dispatching what they hold, so every
        # planner's leftover stock ends sold or spoiled instead of being cut off by the run end.
        wind = self.cfg.default.simulation.wind_down_days
        if wind > self.cfg.default.simulation.settle_extra_days:
            raise ValueError("wind_down_days must not exceed settle_extra_days (market data)")
        for w in range(wind):
            outcomes.append(self._day(self.inst.days + w, wind_down=True))
        # Settle loads still on the road after the last planning day.
        last = self.inst.date_of(self.inst.days - 1 + wind)
        k = 0
        while self.transit:
            k += 1
            d = last + timedelta(days=k)
            out = DayOutcome(day=self.inst.days - 1 + wind + k, date=d, settlement_only=True)
            self._settle(out, self.inst.days - 1 + wind + k, d, count_demand=False)
            self._close_ledger(out)
            outcomes.append(out)
            if k > 10:
                raise RuntimeError("shipments still in transit 10 days after the run")
        return RunResult(scenario_id=self.inst.scenario_id, seed=self.inst.seed,
                         planner_name=self.planner.name,
                         forecast_mode=getattr(self.planner, "forecast_mode", "full"),
                         days=outcomes, placeholder_data=self.inst.placeholder_data,
                         events=self.inst.events)

    def _day(self, day: int, wind_down: bool = False) -> DayOutcome:
        today = self.inst.date_of(day)
        out = DayOutcome(day=day, date=today, settlement_only=wind_down)
        if day > 0:
            self._age_inventory(out)
        self._receive(day, out)

        state = self.build_state(day)
        plan = self._plan_and_validate(state, out)

        todays_events = [e for e in self.inst.events if e["day"] == day and not e["announced"]]
        if todays_events:
            self.revealed.extend(todays_events)
            if self.replan:
                state = self.build_state(day)
                plan = self._plan_and_validate(state, out, replanning=True)
                out.replanned = True

        self._execute(day, plan, out)
        self._settle(out, day, today, count_demand=not wind_down)
        self._close_ledger(out)
        return out

    # ------------------------------------------------------------------ steps

    def _age_inventory(self, out: DayOutcome) -> None:
        disposal = self.cfg.default.cost.disposal_per_kg
        chill = self.cfg.default.shelf_life.chill_penalty
        for bid in list(self.inventory):
            lot = self.inventory[bid]
            c = self.comms[lot.commodity_id]
            rate = consumption_rate(self.hub_temp, c, chill)
            out.storage_inr += lot.qty * c.storage_cost_per_kg_day
            lot.actual_life -= rate
            lot.mean_life -= rate
            if quality(lot.actual_life, c) < c.min_sellable_quality:
                out.spoiled_storage_kg += lot.qty
                out.disposal_inr += lot.qty * disposal
                del self.inventory[bid]

    def _receive(self, day: int, out: DayOutcome) -> None:
        b = self.inst.batches
        for r in b[b["day"] == day].itertuples(index=False):
            self.inventory[r.batch_id] = HubLot(
                batch_id=r.batch_id, commodity_id=r.commodity_id, qty=float(r.qty_kg),
                grade=r.grade, harvest_ts=datetime.fromisoformat(r.harvest_ts),
                mean_life=float(r.remaining_life_mean_days),
                sd_life=float(r.remaining_life_sd_days),
                actual_life=float(r.actual_life_days), cost_basis=float(r.cost_basis_per_kg))
            out.received_kg += float(r.qty_kg)

    def _plan_and_validate(self, state: State, out: DayOutcome, replanning: bool = False) -> Plan:
        t0 = _time.perf_counter()
        plan = self.planner.plan(state, self.cfg)
        out.plan_time_s += _time.perf_counter() - t0
        out.fallback_used = out.fallback_used or plan.solver_meta.fallback_used
        out.solver_gap = plan.solver_meta.gap
        violations = validate(plan, state, self.cfg)
        if replanning:  # count only the plan that is actually executed
            out.violations = {}
            out.dropped_kg = 0.0
        for v in violations:
            out.violations[v.type] = out.violations.get(v.type, 0) + 1
        whole, pairs = droppable(violations, self.cfg.default.simulation.drop_violations)
        kept = []
        for a in plan.allocations:
            if a.vehicle_id in whole or (a.batch_id, a.vehicle_id) in pairs \
                    or (a.batch_id, "*") in pairs:
                out.dropped_kg += a.qty_kg
            else:
                kept.append(a)
        plan = plan.model_copy(update={"allocations": kept, "violations": violations})
        return plan

    def _sample_travel_h(self, day: int, vehicle_id: str, dest_id: str) -> float:
        """Actual hours on the road: base time + delay (20% of legs by default)."""
        lane = self.lanes[dest_id]
        tr = self.cfg.default.travel
        ov = self.inst.overrides
        base = lane.distance_km / tr.speed_kmph * float(ov.get("travel_time_mult", 1.0))
        r = rng_for(self.inst.seed, "delays", day, self.vidx[vehicle_id])
        u, z = r.random(), r.standard_normal(8)   # fixed draw count → common random numbers
        if u >= float(ov.get("delay_prob", tr.delay_prob)):
            return base
        scale = lane.distance_km / 100.0
        mu, sd = tr.delay_mean_h_per_100km * scale, tr.delay_sd_h_per_100km * scale
        draws = [mu + sd * zi for zi in z if mu + sd * zi >= 0]
        return base + (draws[0] if draws else 0.0)

    def _execute(self, day: int, plan: Plan, out: DayOutcome) -> None:
        in_force = [e for e in self.inst.events if self._in_force(e, day)]
        closed = {e["params"]["dest"] for e in in_force if e["type"] == "lane_closure"}
        down = {e["params"]["vehicle_id"] for e in in_force if e["type"] == "vehicle_down"}
        sim = self.cfg.default.simulation
        chill = self.cfg.default.shelf_life.chill_penalty
        amb = self.inst.ambient_true_c[min(day, self.inst.days - 1)]
        dep = depart_ts(self.inst.date_of(day), self.cfg)
        by_vehicle: dict[str, list] = defaultdict(list)
        for a in plan.allocations:
            by_vehicle[a.vehicle_id].append(a)
        vehicles = {v.vehicle_id: v for v in self.inst.vehicles}
        for vid, allocs in sorted(by_vehicle.items()):
            dest_id = allocs[0].dest_id
            if vid in down or dest_id in closed:
                continue  # reality: the truck can't go — the stock stays at the hub
            v = vehicles[vid]
            d = self.dests[dest_id]
            lane = self.lanes[dest_id]
            travel = self._sample_travel_h(day, vid, dest_id)
            eta = roll_to_window(dep + timedelta(hours=travel), d.receive_open_h,
                                 d.receive_close_h)
            on_truck = (eta - dep).total_seconds() / 3600.0
            shipped_any = False
            for a in allocs:
                lot = self.inventory.get(a.batch_id)
                if lot is None or lot.qty <= MASS_TOL_KG:
                    continue
                qty = min(a.qty_kg, lot.qty)
                c = self.comms[lot.commodity_id]
                group = plan.vehicle_setpoints.get(vid, c.temp_group)
                cargo = cargo_temp_c(v, group, amb, self.cfg)
                dl = (delta_life(sim.load_hours, amb, c, chill)
                      + delta_life(on_truck, cargo, c, chill)
                      + delta_life(sim.unload_hours, amb, c, chill))
                self.transit.append(Shipment(
                    batch_id=lot.batch_id, dest_id=dest_id, commodity_id=c.id, qty=qty,
                    q_actual=quality(lot.actual_life - dl, c), discount_pct=a.discount_pct,
                    arrival_date=eta.astimezone(IST).date()))
                out.handling_inr += qty * (d.handling_cost_per_kg + c.handling_cost_per_kg)
                out.dispatched_kg += qty
                lot.qty -= qty
                if lot.qty <= MASS_TOL_KG:
                    del self.inventory[lot.batch_id]
                shipped_any = True
            if shipped_any:
                out.trips += 1
                out.transport_inr += v.fixed_cost_per_trip + v.cost_per_km * lane.distance_km * 2

    def _settle(self, out: DayOutcome, day: int, today: date, count_demand: bool) -> None:
        """Sell every load arriving today against today's true demand."""
        cost = self.cfg.default.cost
        bands = self.cfg.default.shelf_life.price_bands
        arriving = [s for s in self.transit if s.arrival_date <= today]
        self.transit = [s for s in self.transit if s.arrival_date > today]
        pools: dict[tuple[str, str], list[Shipment]] = defaultdict(list)
        for s in arriving:
            pools[(s.dest_id, s.commodity_id)].append(s)
        if count_demand:
            for dest_id in self.dests:
                for cid in self.comms:
                    dem = self.truth.get((day, dest_id, cid), (0.0, 0.0))[0]
                    out.demand_kg += dem
                    out.dest_demand_kg[dest_id] = out.dest_demand_kg.get(dest_id, 0.0) + dem
        for (dest_id, cid), lots in sorted(pools.items()):
            c = self.comms[cid]
            d = self.dests[dest_id]
            demand, price = self.truth.get((day, dest_id, cid), (0.0, 0.0))
            sellable = []
            for s in lots:
                if s.q_actual < c.min_sellable_quality:
                    out.spoiled_transit_kg += s.qty
                    out.disposal_inr += s.qty * cost.disposal_per_kg
                elif s.q_actual < d.min_quality_accepted:
                    out.unsold_market_kg += s.qty
                    out.rejected_kg += s.qty
                    out.disposal_inr += s.qty * cost.disposal_per_kg
                else:
                    sellable.append(s)
            if not sellable:
                continue
            uplift = [min(cost.discount_uplift_cap, cost.discount_elasticity * s.discount_pct / 100)
                      for s in sellable]
            supply = sum(s.qty for s in sellable)
            mean_uplift = sum(u * s.qty for u, s in zip(uplift, sellable, strict=True)) / supply
            sold_total = min(supply, demand * (1.0 + mean_uplift))
            sold = _share(sold_total, [s.qty * (1 + u) for u, s in zip(uplift, sellable,
                                                                        strict=True)],
                          [s.qty for s in sellable])
            for s, kg in zip(sellable, sold, strict=True):
                gross = kg * price * price_multiplier(s.q_actual, bands)
                out.revenue_inr += gross * (1 - s.discount_pct / 100)
                out.discount_cost_inr += gross * s.discount_pct / 100
                out.sold_kg += kg
                if count_demand:
                    out.dest_sold_kg[dest_id] = out.dest_sold_kg.get(dest_id, 0.0) + kg
                unsold = s.qty - kg
                out.unsold_market_kg += unsold
                out.disposal_inr += unsold * cost.disposal_per_kg

    def _close_ledger(self, out: DayOutcome) -> None:
        out.held_end_kg = sum(lot.qty for lot in self.inventory.values())
        out.in_transit_end_kg = sum(s.qty for s in self.transit)
        for k in ("received_kg", "sold_kg", "spoiled_storage_kg", "spoiled_transit_kg",
                  "unsold_market_kg"):
            self.cum[k] += getattr(out, k)
        lhs = self.cum["received_kg"]
        rhs = (self.cum["sold_kg"] + self.cum["spoiled_storage_kg"]
               + self.cum["spoiled_transit_kg"] + self.cum["unsold_market_kg"]
               + out.held_end_kg + out.in_transit_end_kg)
        if abs(lhs - rhs) > MASS_TOL_KG * max(1.0, lhs) * 10:
            raise MassBalanceError(
                f"day {out.day}: received {lhs:.3f} kg ≠ sold+spoiled+unsold+held+transit "
                f"{rhs:.3f} kg")


def _share(total: float, weights: list[float], caps: list[float]) -> list[float]:
    """Split ``total`` proportionally to ``weights`` without exceeding ``caps`` (water-filling)."""
    alloc = [0.0] * len(weights)
    remaining = total
    active = [i for i, w in enumerate(weights) if w > 0 and caps[i] > 0]
    while remaining > 1e-9 and active:
        wsum = sum(weights[i] for i in active)
        capped = []
        for i in active:
            give = remaining * weights[i] / wsum
            if alloc[i] + give >= caps[i] - 1e-12:
                capped.append(i)
        if not capped:
            for i in active:
                alloc[i] += remaining * weights[i] / wsum
            remaining = 0.0
            break
        for i in capped:
            remaining -= caps[i] - alloc[i]
            alloc[i] = caps[i]
        active = [i for i in active if i not in capped]
    return alloc


def run(instance: Instance, planner: Planner, cfg: AppConfig | None = None,
        replan: bool | None = None, risk_p: float | None = None) -> RunResult:
    """Simulate ``planner`` on ``instance``."""
    return Simulator(instance, planner, cfg, replan=replan, risk_p=risk_p).run()



class _ForecastOnly:
    """Stand-in planner that only tells the State builder which forecast to use."""

    name = "none"

    def __init__(self, forecast_mode: str):
        self.forecast_mode = forecast_mode

    def plan(self, state: State, cfg: AppConfig) -> Plan:  # pragma: no cover - never called
        raise NotImplementedError


def initial_state(instance: Instance, cfg: AppConfig | None = None, forecast_mode: str = "full",
                  risk_p: float | None = None) -> State:
    """The day-0 morning State a planner would receive (day-0 arrivals in stock)."""
    sim = Simulator(instance, _ForecastOnly(forecast_mode), cfg, replan=False, risk_p=risk_p)
    sim._receive(0, DayOutcome(day=0, date=instance.start_date))
    return sim.build_state(0)
