"""M1: mass balance over seeds, replan hook, determinism, metrics consistency."""

from __future__ import annotations

import warnings

import pytest

from freshroute.baselines import get_planner
from freshroute.generator import generate
from freshroute.metrics import run_metrics
from freshroute.simulator import run

warnings.filterwarnings("ignore")


@pytest.mark.parametrize("scenario,seed", [("normal", 1), ("normal", 2), ("heatwave", 3),
                                           ("harvest_glut", 4), ("monsoon", 5)])
def test_mass_balance_every_day(cfg, scenario, seed):
    """The simulator asserts mass balance daily; check the closing ledger here too."""
    r = run(generate(scenario, seed, cfg=cfg), get_planner("fifo_nearest"), cfg)
    received = sum(d.received_kg for d in r.days)
    out = sum(d.sold_kg + d.spoiled_storage_kg + d.spoiled_transit_kg + d.unsold_market_kg
              for d in r.days)
    assert received == pytest.approx(out + r.days[-1].held_end_kg, rel=1e-6)
    assert r.days[-1].in_transit_end_kg == pytest.approx(0.0, abs=1e-6)


def test_same_seed_same_result(cfg):
    inst = generate("normal", 6, cfg=cfg)
    a = run(inst, get_planner("fifo_nearest"), cfg)
    b = run(generate("normal", 6, cfg=cfg), get_planner("fifo_nearest"), cfg)
    assert a.model_dump() == b.model_dump() or [d.model_dump(exclude={"plan_time_s"})
                                                for d in a.days] == \
        [d.model_dump(exclude={"plan_time_s"}) for d in b.days]


class _Counting:
    """Wraps B1 and counts how often the simulator asks for a plan."""

    name = "counting"
    forecast_mode = "flat"

    def __init__(self):
        self.calls = 0
        self.inner = get_planner("fifo_nearest")

    def plan(self, state, cfg):
        self.calls += 1
        return self.inner.plan(state, cfg)


def test_replan_hook_calls_planner_again_on_event_day(cfg):
    inst = generate("lane_closure", 7, cfg=cfg)
    static, dynamic = _Counting(), _Counting()
    rs = run(inst, static, cfg, replan=False)
    rd = run(inst, dynamic, cfg, replan=True)
    assert static.calls == inst.days
    assert dynamic.calls == inst.days + 1          # one extra plan on the event day
    ev_day = inst.events[0]["day"]
    assert rd.days[ev_day].replanned and not rs.days[ev_day].replanned


def test_closed_lane_known_after_event(cfg):
    """From the day after an unannounced closure, the planner's State shows the lane closed."""
    inst = generate("lane_closure", 8, cfg=cfg)
    far = inst.events[0]["params"]["dest"]
    seen: dict[int, bool] = {}

    class Spy(_Counting):
        def plan(self, state, cfg):
            day = (state.date - inst.start_date).days
            seen.setdefault(day, next(ln.open for ln in state.lanes if ln.dest_id == far))
            return super().plan(state, cfg)

    run(inst, Spy(), cfg)
    ev = inst.events[0]["day"]
    assert seen[ev] is True          # unannounced: not known at the morning plan
    assert seen[ev + 1] is False


def test_metrics_match_ledger(cfg):
    r = run(generate("normal", 9, cfg=cfg), get_planner("fifo_nearest"), cfg)
    m = run_metrics(r)
    assert 0.0 <= m["spoilage_pct"] <= 1.0
    assert m["spoiled_kg"] == pytest.approx(
        m["spoiled_storage_kg"] + m["spoiled_transit_kg"] + m["unsold_market_kg"])
    assert m["net_revenue_inr"] == pytest.approx(
        m["revenue_inr"] - m["transport_inr"] - m["handling_inr"] - m["storage_inr"]
        - m["disposal_inr"])
    assert m["fulfilment_min_dest"] <= m["fulfilment_pct"] + 1e-9 or \
        m["fulfilment_min_dest"] <= max(m["fulfilment_by_dest"].values())
