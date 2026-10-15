"""M4: sensitivity perturbations, replan/tornado aggregation, bootstrap CI."""

from __future__ import annotations

import numpy as np
import pytest

from freshroute.eval_runner import aggregate_replan, aggregate_sensitivity, bootstrap_ci
from freshroute.generator import build_fleet
from freshroute.market_model import load_market_params
from freshroute.sensitivity import PARAMS, perturb


@pytest.fixture(scope="module")
def mp():
    return load_market_params()


def test_every_parameter_changes_only_its_target(cfg, mp):
    for param in PARAMS:
        c2, m2 = perturb(cfg, mp, param, 1.2)
        assert c2 is not cfg and m2 is not mp
        changed = {
            "q10": c2.commodities[0].q10 != cfg.commodities[0].q10,
            "life_sd": c2.default.generator.grade_sd_frac != cfg.default.generator.grade_sd_frac,
            "delay_prob": c2.default.travel.delay_prob != cfg.default.travel.delay_prob,
            "demand_sigma": not m2.noise.sigma_log_demand.equals(mp.noise.sigma_log_demand),
            "price_sigma": not m2.noise.sigma_log_price.equals(mp.noise.sigma_log_price),
            "elasticity": (c2.default.market.demand_price_elasticity
                           != cfg.default.market.demand_price_elasticity),
            "freight": build_fleet(c2)[0].cost_per_km != build_fleet(cfg)[0].cost_per_km,
        }
        assert changed[param], param
        assert sum(changed.values()) == 1, f"{param} changed {changed}"


def test_perturbation_does_not_mutate_the_original(cfg, mp):
    q10 = cfg.commodities[0].q10
    sig = mp.noise.sigma_log_demand.copy()
    perturb(cfg, mp, "q10", 0.8)
    perturb(cfg, mp, "demand_sigma", 0.8)
    assert cfg.commodities[0].q10 == q10
    assert mp.noise.sigma_log_demand.equals(sig)


def test_elasticity_shifts_fdi_by_price_uplift(cfg, mp):
    if mp.festival_effects.empty:
        pytest.skip("no festival effects fitted")
    c2, m2 = perturb(cfg, mp, "elasticity", 1.2)
    shift = abs(c2.default.market.demand_price_elasticity) \
        - abs(cfg.default.market.demand_price_elasticity)
    expect = mp.festival_effects.fdi_mean + shift * mp.festival_effects.pu_mean
    assert np.allclose(m2.festival_effects.fdi_mean, expect)


def _row(planner, seed, net, spoil, **kw):
    return {"scenario_id": "s", "planner": planner, "seed": seed, "net_revenue_inr": net,
            "spoilage_pct": spoil, "fulfilment_pct": 0.3, "violations_total": 0, **kw}


def test_replan_aggregation_pairs_by_seed(cfg):
    rows = [_row("p", s, 100 + s, 0.1, replan=False) for s in range(5)]
    rows += [_row("p", s, 110 + s, 0.08, replan=True) for s in range(5)]
    out = aggregate_replan(rows, cfg)["s"]["p"]
    assert out["replan_minus_static"]["net_revenue_inr"]["mean"] == pytest.approx(10)
    assert out["win_rate_net_revenue"] == 1.0 and out["ties"] == 0.0


def test_tornado_uses_paired_advantage(cfg):
    sc = cfg.default.evaluation.sensitivity
    base = cfg.default.evaluation.baseline
    rows = []
    for s in range(3):
        rows += [_row(sc.planner, s, 200, 0.02, perturb_param=None, perturb_factor=1.0),
                 _row(base, s, 100, 0.12, perturb_param=None, perturb_factor=1.0)]
        for prm in sc.params:
            for f, gain in ((0.8, 50), (1.2, 150)):
                rows += [_row(sc.planner, s, 100 + gain, 0.02, perturb_param=prm,
                              perturb_factor=f),
                         _row(base, s, 100, 0.12, perturb_param=prm, perturb_factor=f)]
    t = aggregate_sensitivity(rows, cfg)
    assert t["base"]["d_net_revenue_inr"] == pytest.approx(100)
    assert t["base"]["d_spoilage_pct"] == pytest.approx(-0.10)
    first = t["tornado"][0]
    assert first["low"]["d_net_revenue_inr"] == pytest.approx(50)
    assert first["high"]["d_net_revenue_inr"] == pytest.approx(150)
    assert len(t["tornado"]) == len(sc.params)


def test_bootstrap_ci_brackets_mean():
    x = np.random.default_rng(1).normal(10, 2, 200)
    lo, hi = bootstrap_ci(x, 2000, 0.95)
    assert lo < x.mean() < hi
    assert bootstrap_ci(np.array([3.0]), 100, 0.95) == (3.0, 3.0)


_DETERMINISM_SCRIPT = """
import warnings; warnings.filterwarnings("ignore")
from freshroute.baselines import get_planner
from freshroute.config import load_config
from freshroute.generator import generate
from freshroute.simulator import run
cfg = load_config()
inst = generate("normal", 51, cfg=cfg)
inst.days = 3
r = run(inst, get_planner("freshroute"), cfg)
print(repr(sum(d.revenue_inr for d in r.days)))
"""


def test_planner_is_reproducible_across_processes():
    """Same seed, same plan: no dependence on Python's per-process set ordering."""
    import os
    import subprocess
    import sys
    outs = set()
    for hs in ("1", "2"):
        env = {**os.environ, "PYTHONHASHSEED": hs, "PYTHONUTF8": "1"}
        p = subprocess.run([sys.executable, "-c", _DETERMINISM_SCRIPT], env=env,
                           capture_output=True, text=True, timeout=300, check=True)
        outs.add(p.stdout.strip().splitlines()[-1])
    assert len(outs) == 1, outs
