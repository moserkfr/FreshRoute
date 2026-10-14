"""M3: the planner solves a 200-batch day within the 15 s target and returns a valid plan."""

from __future__ import annotations

import time
import warnings

import pytest

from freshroute.generator import generate
from freshroute.planner import FreshRoutePlanner
from freshroute.schemas import DayOutcome
from freshroute.simulator import Simulator
from freshroute.validator import validate

warnings.filterwarnings("ignore")
TARGET_S = 15.0
N_BATCHES = 200


@pytest.mark.slow
def test_200_batches_under_target(cfg):
    inst = generate("harvest_glut", 3, cfg=cfg)
    sim = Simulator(inst, FreshRoutePlanner(), cfg)
    day = 0
    while True:  # receive several days of glut arrivals without dispatching
        sim._receive(day, DayOutcome(day=day, date=inst.date_of(day)))
        state = sim.build_state(day)
        if len(state.batches) >= N_BATCHES:
            break
        day += 1
    state = state.model_copy(update={"batches": state.batches[:N_BATCHES]})
    t0 = time.perf_counter()
    plan = FreshRoutePlanner().plan(state, cfg)
    elapsed = time.perf_counter() - t0
    assert len(state.batches) == N_BATCHES
    assert elapsed < TARGET_S, f"{elapsed:.1f} s for {N_BATCHES} batches"
    assert validate(plan, state, cfg) == []
    assert not plan.solver_meta.fallback_used
