"""M3: tiny planning problems with a known answer; fallback; discount engine; B2/B3."""

from __future__ import annotations

import warnings
from datetime import date, datetime, timedelta

import pytest

from freshroute import discount, milp
from freshroute.baselines import ChasePrice, FefoGreedy
from freshroute.generator import build_fleet, build_lanes, to_commodity, to_destination
from freshroute.planner import FreshRoutePlanner
from freshroute.schemas import IST, Batch, MarketDay, State
from freshroute.validator import validate

warnings.filterwarnings("ignore")
TODAY = date(2025, 7, 7)


def world(cfg, dest_ids, vehicle_ids):
    dests = [to_destination(d) for d in cfg.network.destinations if d.dest_id in dest_ids]
    lanes = [ln for ln in build_lanes(cfg, 1.0, 0.0) if ln.dest_id in dest_ids]
    fleet = [v for v in build_fleet(cfg) if v.vehicle_id in vehicle_ids]
    return dests, lanes, fleet


def batch(cfg, bid, cid, kg, life_frac=1.0, sd=0.05, life_days=None):
    c = next(x for x in cfg.commodities if x.id == cid)
    life = life_days if life_days is not None else c.shelf_life_ref_days * life_frac
    return Batch(batch_id=bid, commodity_id=cid, qty_kg=kg, grade="A",
                 harvest_ts=datetime.combine(TODAY, datetime.min.time(), tzinfo=IST),
                 location_id="kolar_hub", remaining_life_mean_days=life,
                 remaining_life_sd_days=sd, cost_basis_per_kg=5.0)


def state(cfg, batches, dest_ids, vehicle_ids, price, demand=50_000.0, days=6):
    """``price``: {(dest, commodity): ₹/kg} or a callable (dest, commodity, day_offset) → ₹/kg."""
    dests, lanes, fleet = world(cfg, dest_ids, vehicle_ids)
    cids = {b.commodity_id for b in batches}
    fc = []
    for k in range(days):
        for d in dest_ids:
            for c in cids:
                p = price(d, c, k) if callable(price) else price[(d, c)]
                fc.append(MarketDay(dest_id=d, commodity_id=c, date=TODAY + timedelta(days=k),
                                    demand_kg_mean=demand, demand_kg_sd=0.0,
                                    price_per_kg_forecast=p, price_sd=0.0))
    return State(date=TODAY, ambient_temp_c=cfg.default.simulation.ambient_temp_c,
                 batches=batches, vehicles=fleet, destinations=dests, lanes=lanes,
                 market_forecast=fc, commodities=[to_commodity(c) for c in cfg.commodities],
                 hub_id="kolar_hub",
                 config_snapshot={"risk_p": 0.9, "horizon_days": 3})


def full_truck(cfg, cid):
    return [batch(cfg, f"B{i}", cid, 1000.0) for i in range(6)]   # fills the 6 t truck A3


def best_destination(cfg, st, cid, kg):
    """Analytic optimum for one full truck to one destination."""
    c = next(x for x in st.commodities if x.id == cid)
    v = st.vehicles[0]
    vals = {}
    for d in st.destinations:
        lane = next(ln for ln in st.lanes if ln.dest_id == d.dest_id)
        p = next(m.price_per_kg_forecast for m in st.market_forecast
                 if m.dest_id == d.dest_id and m.commodity_id == cid)
        vals[d.dest_id] = (kg * (p - d.handling_cost_per_kg - c.handling_cost_per_kg)
                           - milp.trip_cost(v, lane.distance_km))
    return max(vals, key=vals.get)


@pytest.mark.parametrize("mysuru_price", [30.0, 21.0])
def test_known_optimum_destination(cfg, mysuru_price):
    st = state(cfg, full_truck(cfg, "onion"), ["blr_kr", "mysuru"], ["A3"],
               {("blr_kr", "onion"): 20.0, ("mysuru", "onion"): mysuru_price})
    plan = FreshRoutePlanner().plan(st, cfg)
    assert validate(plan, st, cfg) == []
    assert not plan.solver_meta.fallback_used
    sent = {a.dest_id for a in plan.allocations}
    assert sent == {best_destination(cfg, st, "onion", 6000.0)}
    assert sum(a.qty_kg for a in plan.allocations) == pytest.approx(6000.0, abs=1.0)


def test_reefer_runs_one_group_and_picks_the_better_one(cfg):
    batches = ([batch(cfg, f"C{i}", "cabbage", 1000.0) for i in range(3)]
               + [batch(cfg, f"T{i}", "tomato", 1000.0) for i in range(3)])
    st = state(cfg, batches, ["blr_kr"], ["R1"],
               {("blr_kr", "cabbage"): 11.0, ("blr_kr", "tomato"): 30.0})
    plan = FreshRoutePlanner().plan(st, cfg)
    assert validate(plan, st, cfg) == []
    assert plan.vehicle_setpoints == {"R1": "cool"}
    shipped = {a.batch_id[0] for a in plan.allocations}
    assert shipped == {"T"}


def test_holds_storable_stock_for_a_festival_price(cfg):
    # onion is worth ₹10 today but ₹30 two days from now
    def price(_d, _c, k):
        return 30.0 if k >= 2 else 10.0
    st = state(cfg, full_truck(cfg, "onion"), ["blr_kr"], ["A3"], price)
    plan = FreshRoutePlanner().plan(st, cfg)
    assert validate(plan, st, cfg) == []
    assert sum(h.qty_kg for h in plan.holds) == pytest.approx(6000.0, abs=1.0)
    assert not plan.allocations


def test_never_holds_dying_greens(cfg):
    # sellable at the processor today, dead by tomorrow morning at hub temperature
    greens = [batch(cfg, f"S{i}", "spinach", 1000.0, life_days=4.5, sd=0.1) for i in range(6)]
    st = state(cfg, greens, ["malur_proc"], ["A3"], {("malur_proc", "spinach"): 15.0})
    vh = milp.hold_values(st, cfg)
    assert vh == {}                                  # no later sale is feasible
    plan = FreshRoutePlanner().plan(st, cfg)
    assert validate(plan, st, cfg) == []
    assert sum(a.qty_kg for a in plan.allocations) == pytest.approx(6000.0, abs=1.0)


def test_forced_timeout_falls_back_to_heuristic(cfg, monkeypatch):
    st = state(cfg, full_truck(cfg, "onion"), ["blr_kr", "mysuru"], ["A3"],
               {("blr_kr", "onion"): 20.0, ("mysuru", "onion"): 25.0})

    def timed_out(*_a, **_k):
        return milp.MilpResult(status="not_solved", shipped={}, held={}, vehicle_dest={},
                               vehicle_group={})
    monkeypatch.setattr(milp, "solve", timed_out)
    plan = FreshRoutePlanner().plan(st, cfg)
    assert plan.solver_meta.fallback_used
    assert plan.planner_name == "freshroute"
    assert validate(plan, st, cfg) == []
    assert plan.allocations                          # the heuristic still ships stock


def test_real_time_limit_still_returns_a_valid_plan(cfg):
    st = state(cfg, full_truck(cfg, "onion"), ["blr_kr", "mysuru"], ["A3"],
               {("blr_kr", "onion"): 20.0, ("mysuru", "onion"): 25.0})
    plan = FreshRoutePlanner(time_limit_s=0.001).plan(st, cfg)
    assert validate(plan, st, cfg) == []


def test_discount_expected_value_and_choice(cfg):
    # 1000 kg into 800 kg of demand: 20% off lifts sell-through to 800·1.3/1000 = 1.04 → 1
    ev0 = discount.expected_value(800, 1000, 20.0, 1.0, 0, cfg)
    ev20 = discount.expected_value(800, 1000, 20.0, 1.0, 20, cfg)
    c = cfg.default.cost
    assert ev0 == pytest.approx(800 * 20 - 200 * (c.writeoff_per_kg + c.disposal_per_kg))
    assert ev20 == pytest.approx(1000 * 20 * 0.8)
    assert discount.sell_through(800, 1000, 100, cfg) == 1.0   # uplift capped, never > 1


def test_festival_guard_blocks_discount(cfg):
    from freshroute.feasibility import Option
    o = Option(batch_id="B0", commodity_id="onion", dest_id="blr_kr", vehicle_id="A3",
               group=None, arrival_date=TODAY, eta_hour=8.0, delta_life_days=0.1, q_mean=0.5,
               q_p10=0.45, price=20.0, revenue_per_kg=18.0, handling_per_kg=1.0)
    st = state(cfg, [batch(cfg, "B0", "onion", 1000.0)], ["blr_kr"], ["A3"],
               {("blr_kr", "onion"): 20.0}, demand=500.0)
    key = ("B0", "blr_kr", "A3")
    plain = discount.decide(st, cfg, {key: 1000.0}, {key: o}, {"B0": 5.0})[key]
    assert plain.pct > 0                                        # oversupplied → discount
    fest = st.model_copy(update={"market_forecast": [
        m.model_copy(update={"fdi": 0.3}) for m in st.market_forecast]})
    guarded = discount.decide(fest, cfg, {key: 1000.0}, {key: o}, {"B0": 5.0})[key]
    assert guarded.guarded and guarded.pct == 0


def test_b2_chases_price_and_b3_is_feasible(cfg):
    st = state(cfg, full_truck(cfg, "onion"), ["blr_kr", "mysuru"], ["A3"],
               {("blr_kr", "onion"): 20.0, ("mysuru", "onion"): 21.0})
    b2 = ChasePrice().plan(st, cfg)
    assert {a.dest_id for a in b2.allocations} == {"mysuru"}
    b3 = FefoGreedy().plan(st, cfg)
    assert validate(b3, st, cfg) == []
    assert b3.allocations
