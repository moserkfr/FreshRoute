"""M1: determinism (hash), scenario overrides applied, seed splits disjoint."""

from __future__ import annotations

import warnings

import pytest

from freshroute.config import load_config
from freshroute.generator import generate, parse_seeds

warnings.filterwarnings("ignore")


def test_same_seed_same_instance(cfg):
    a = generate("normal", 7, cfg=cfg)
    b = generate("normal", 7, cfg=cfg)
    assert a.fingerprint() == b.fingerprint()


def test_different_seed_different_instance(cfg):
    assert generate("normal", 7, cfg=cfg).fingerprint() != \
        generate("normal", 8, cfg=cfg).fingerprint()


def test_truth_and_forecast_columns_present(cfg):
    inst = generate("normal", 3, cfg=cfg)
    assert {"actual_life_days", "remaining_life_mean_days"} <= set(inst.batches.columns)
    assert {"demand_true", "demand_full", "demand_nocal"} <= set(inst.market.columns)
    assert len(inst.ambient_true_c) == inst.days


def test_no_batch_arrives_spoiled(cfg):
    """Farm-to-hub age must never deliver stock that is already below sellable quality."""
    ref = {c.id: (c.shelf_life_ref_days, c.min_sellable_quality) for c in cfg.commodities}
    b = generate("normal", 4, cfg=cfg).batches
    q = b.remaining_life_mean_days / b.commodity_id.map(lambda c: ref[c][0])
    assert (q > b.commodity_id.map(lambda c: ref[c][1])).all()


def test_supply_override_scales_volume():
    base_cfg = load_config()
    cfg = load_config()
    cfg.scenarios.scenarios["normal"].overrides.supply_mult = 2.0
    base = generate("normal", 11, cfg=base_cfg).batches.qty_kg.sum()
    doubled = generate("normal", 11, cfg=cfg).batches.qty_kg.sum()
    assert doubled == pytest.approx(2 * base, rel=0.02)


def test_scenario_overrides_reach_instance(cfg):
    hw = generate("heatwave", 5, cfg=cfg)
    normal = generate("normal", 5, cfg=cfg)
    delta = cfg.scenarios.resolved("heatwave").overrides.ambient_delta_c
    assert hw.ambient_forecast_c[0] == pytest.approx(normal.ambient_forecast_c[0] + delta)
    monsoon = generate("monsoon", 5, cfg=cfg)
    assert monsoon.overrides["delay_prob"] == 0.4


def test_unannounced_event_is_resolved(cfg):
    inst = generate("lane_closure", 5, cfg=cfg)
    ev = inst.events[0]
    far = max(inst.lanes, key=lambda ln: ln.distance_km).dest_id
    assert ev["params"]["dest"] == far and ev["announced"] is False


def test_seed_splits_disjoint(cfg):
    s = cfg.default.seeds
    tuning = set(range(s.tuning[0], s.tuning[1] + 1))
    validation = set(range(s.validation[0], s.validation[1] + 1))
    assert not tuning & validation
    assert max(tuning | validation) < s.hidden_min


def test_parse_seeds():
    assert parse_seeds("1-3,9") == [1, 2, 3, 9]
