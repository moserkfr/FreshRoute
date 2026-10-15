"""Window-risk option dating: a load that may miss today's receive window is a next-day sale."""

from __future__ import annotations

import warnings
from datetime import timedelta

from freshroute import feasibility
from freshroute.generator import generate
from freshroute.logistics import planning_travel_h
from freshroute.simulator import initial_state

warnings.filterwarnings("ignore")


def test_window_risk_moves_knife_edge_lane_to_next_day(cfg):
    state = initial_state(generate("normal", 51, cfg=cfg), cfg)
    lane = next(ln for ln in state.lanes if ln.dest_id == "chennai_koyambedu")
    assert planning_travel_h(lane, 0.9) > lane.travel_time_h_mean
    # Mean arrival is inside today's window (13:44 < 14:00); the 90% arrival is not.
    mean = feasibility.build(state, cfg, window_risk=False)
    risky = feasibility.build(state, cfg, window_risk=True)
    days = {o.arrival_date for o in mean.options if o.dest_id == lane.dest_id}
    risk_days = {o.arrival_date for o in risky.options if o.dest_id == lane.dest_id}
    assert days == {state.date}
    assert risk_days == {state.date + timedelta(days=1)}
    # Lanes with plenty of slack before closing keep their same-day sale.
    for dest in ("blr_kr", "blr_dc"):
        assert {o.arrival_date for o in risky.options if o.dest_id == dest} == {state.date}
