"""M1: each violation type triggered by a crafted plan; a clean plan has none."""

from __future__ import annotations

import warnings
from datetime import timedelta

import pytest

from freshroute.baselines import make_allocation
from freshroute.generator import generate
from freshroute.logistics import estimate_trip
from freshroute.schemas import Allocation, DayOutcome, Plan
from freshroute.simulator import Simulator
from freshroute.validator import HARD_TYPES, POLICY_TYPES, droppable, validate

warnings.filterwarnings("ignore")


class _Null:
    name = "null"
    forecast_mode = "flat"


@pytest.fixture(scope="module")
def state(cfg):
    inst = generate("normal", 2, cfg=cfg)
    sim = Simulator(inst, _Null(), cfg)
    sim._receive(0, DayOutcome(day=0, date=inst.date_of(0)))
    return sim.build_state(0)


def pick(state, cid, min_qty=0.0):
    """Freshest batch of a commodity (so only the rule under test can fail)."""
    bs = [b for b in state.batches if b.commodity_id == cid and b.qty_kg >= min_qty]
    return max(bs, key=lambda b: b.remaining_life_mean_days)


def alloc(state, cfg, batch, dest_id, vid, kg):
    lane = next(ln for ln in state.lanes if ln.dest_id == dest_id)
    dest = next(d for d in state.destinations if d.dest_id == dest_id)
    c = next(x for x in state.commodities if x.id == batch.commodity_id)
    v = next(x for x in state.vehicles if x.vehicle_id == vid)
    trip = estimate_trip(d=state.date, lane=lane, dest=dest, commodity=c, vehicle=v,
                         ambient_c=state.ambient_temp_c, cfg=cfg)
    return make_allocation(state, cfg, batch, dest_id, v, kg, trip, 20.0)


def plan_of(state, allocs, setpoints=None):
    return Plan(date=state.date, planner_name="test", allocations=allocs,
                vehicle_assignments={a.vehicle_id: a.dest_id for a in allocs},
                vehicle_setpoints=setpoints or {})


def types(plan, state, cfg):
    return {v.type for v in validate(plan, state, cfg)}


def test_clean_plan_has_no_violations(state, cfg):
    onion = pick(state, "onion")
    p = plan_of(state, [alloc(state, cfg, onion, "blr_kr", "A3", min(500.0, onion.qty_kg))])
    assert validate(p, state, cfg) == []


def test_unknown_ref(state, cfg):
    onion = pick(state, "onion")
    a = alloc(state, cfg, onion, "blr_kr", "A3", 100.0).model_copy(update={"batch_id": "NOPE"})
    assert "unknown_ref" in types(plan_of(state, [a]), state, cfg)


def test_negative_qty(state, cfg):
    onion = pick(state, "onion")
    good = alloc(state, cfg, onion, "blr_kr", "A3", 100.0)
    bad = Allocation.model_construct(**{**good.model_dump(), "qty_kg": -5.0})
    assert "negative_qty" in types(plan_of(state, [bad]), state, cfg)


def test_stock_conservation(state, cfg):
    onion = pick(state, "onion")
    a = alloc(state, cfg, onion, "blr_kr", "A5", onion.qty_kg + 100)
    assert "stock_conservation" in types(plan_of(state, [a]), state, cfg)


def test_vehicle_capacity(state, cfg):
    onions = sorted((b for b in state.batches if b.commodity_id == "onion"),
                    key=lambda b: -b.qty_kg)
    allocs, total = [], 0.0
    for b in onions:
        allocs.append(alloc(state, cfg, b, "blr_kr", "A1", b.qty_kg))
        total += b.qty_kg
        if total > 3000:
            break
    assert total > 3000
    assert "vehicle_capacity" in types(plan_of(state, allocs), state, cfg)


def test_one_destination(state, cfg):
    onion = pick(state, "onion")
    a = alloc(state, cfg, onion, "blr_kr", "A3", 100.0)
    b = alloc(state, cfg, onion, "mysuru", "A3", 100.0)
    p = Plan(date=state.date, planner_name="t", allocations=[a, b],
             vehicle_assignments={"A3": "blr_kr"})
    assert "one_destination" in types(p, state, cfg)


def test_vehicle_unavailable(state, cfg):
    onion = pick(state, "onion")
    s = state.model_copy(update={"vehicles": [
        v.model_copy(update={"available": v.vehicle_id != "A3"}) for v in state.vehicles]})
    p = plan_of(s, [alloc(s, cfg, onion, "blr_kr", "A3", 100.0)])
    assert "vehicle_unavailable" in types(p, s, cfg)


def test_lane_closed(state, cfg):
    onion = pick(state, "onion")
    s = state.model_copy(update={"lanes": [
        ln.model_copy(update={"open": ln.dest_id != "blr_kr"}) for ln in state.lanes]})
    p = plan_of(s, [alloc(s, cfg, onion, "blr_kr", "A3", 100.0)])
    assert "lane_closed" in types(p, s, cfg)


def test_delivery_window(state, cfg):
    onion = pick(state, "onion")
    a = alloc(state, cfg, onion, "blr_kr", "A3", 100.0)
    a = a.model_copy(update={"eta_ts": a.eta_ts + timedelta(hours=3)})
    assert "delivery_window" in types(plan_of(state, [a]), state, cfg)


def test_reefer_compatibility(state, cfg):
    onion, tomato = pick(state, "onion"), pick(state, "tomato")
    allocs = [alloc(state, cfg, onion, "blr_kr", "R1", 100.0),
              alloc(state, cfg, tomato, "blr_kr", "R1", 100.0)]
    assert "reefer_compatibility" in types(plan_of(state, allocs, {"R1": "chilled"}), state, cfg)


def test_shelf_life_chance(state, cfg):
    tomato = pick(state, "tomato")
    weak = tomato.model_copy(update={"remaining_life_mean_days": 1.0,
                                     "remaining_life_sd_days": 0.3})
    s = state.model_copy(update={"batches": [weak if b.batch_id == weak.batch_id else b
                                             for b in state.batches]})
    p = plan_of(s, [alloc(s, cfg, weak, "chennai_koyambedu", "A6", 100.0)])
    assert "shelf_life_chance" in types(p, s, cfg)


def test_min_quality(state, cfg):
    """Sellable on arrival (passes the chance rule) but below the retail DC's 50% minimum."""
    onion = pick(state, "onion")
    c = next(x for x in state.commodities if x.id == "onion")
    mid = onion.model_copy(update={"remaining_life_mean_days": 0.4 * c.shelf_life_ref_days,
                                   "remaining_life_sd_days": 0.0})
    s = state.model_copy(update={"batches": [mid if b.batch_id == mid.batch_id else b
                                             for b in state.batches]})
    found = types(plan_of(s, [alloc(s, cfg, mid, "blr_dc", "A3", 100.0)]), s, cfg)
    assert found == {"min_quality"}


def test_type_sets_cover_everything():
    assert not HARD_TYPES & POLICY_TYPES
    assert POLICY_TYPES == {"shelf_life_chance", "min_quality"}


def test_droppable_respects_policy(state, cfg):
    onion = pick(state, "onion")
    c = next(x for x in state.commodities if x.id == "onion")
    mid = onion.model_copy(update={"remaining_life_mean_days": 0.4 * c.shelf_life_ref_days,
                                   "remaining_life_sd_days": 0.0})
    s = state.model_copy(update={"batches": [mid if b.batch_id == mid.batch_id else b
                                             for b in state.batches]})
    v = validate(plan_of(s, [alloc(s, cfg, mid, "blr_dc", "A3", 100.0)]), s, cfg)
    assert droppable(v, "all")[1] == {(mid.batch_id, "A3")}
    assert droppable(v, "hard") == (set(), set())
