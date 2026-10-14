"""M1: Q10 math, chill penalty, quality → price bands, chance constraint, mango calibration."""

from __future__ import annotations

import pytest

from freshroute.shelf_life import (
    consumption_rate,
    delta_life,
    life_quantile,
    price_multiplier,
    quality,
    sellable_with_prob,
    trip_delta_life,
    z_value,
)


def comm(cfg, cid):
    return next(c for c in cfg.commodities if c.id == cid)


def test_rate_is_one_at_reference_temperature(cfg):
    for c in cfg.commodities:
        assert consumption_rate(c.t_ref_c, c, 1.5) == pytest.approx(1.0)


def test_q10_doubles_every_ten_degrees(cfg):
    onion = comm(cfg, "onion")  # q10 = 2, no chilling threshold
    assert consumption_rate(10.0, onion, 1.5) == pytest.approx(2.0)
    assert consumption_rate(20.0, onion, 1.5) == pytest.approx(4.0)
    assert delta_life(12.0, 20.0, onion, 1.5) == pytest.approx(2.0)  # half a day at 4×


def test_chill_penalty_applies_only_below_threshold(cfg):
    mango = comm(cfg, "mango")  # t_ref 13, chill threshold 13
    plain = mango.q10 ** ((2.0 - mango.t_ref_c) / 10.0)
    assert consumption_rate(2.0, mango, 1.5) == pytest.approx(plain * 1.5)
    assert consumption_rate(13.0, mango, 1.5) == pytest.approx(1.0)


def test_trip_delta_is_sum_of_legs(cfg):
    tomato = comm(cfg, "tomato")
    total = trip_delta_life(tomato, ambient_c=28, cargo_c=13, load_h=1, transit_h=6, unload_h=1,
                            chill_penalty=1.5)
    expect = (delta_life(1, 28, tomato, 1.5) + delta_life(6, 13, tomato, 1.5)
              + delta_life(1, 28, tomato, 1.5))
    assert total == pytest.approx(expect)


def test_quality_is_clipped(cfg):
    spinach = comm(cfg, "spinach")
    assert quality(-3.0, spinach) == 0.0
    assert quality(spinach.shelf_life_ref_days * 2, spinach) == 1.0
    assert quality(spinach.shelf_life_ref_days / 2, spinach) == pytest.approx(0.5)


def test_price_bands(cfg):
    b = cfg.default.shelf_life.price_bands
    assert price_multiplier(0.9, b) == 1.0
    assert price_multiplier(b.full_q, b) == 1.0
    assert price_multiplier(b.mid_q, b) == pytest.approx(b.mid_low_mult)
    mid = (b.mid_q + b.full_q) / 2
    assert price_multiplier(mid, b) == pytest.approx((b.mid_low_mult + 1) / 2)
    assert price_multiplier(0.2, b) == b.clearance_mult
    assert price_multiplier(0.1, b) == 0.0


def test_chance_constraint():
    assert z_value(0.9) == pytest.approx(1.2816, abs=1e-4)
    assert life_quantile(10.0, 2.0, 0.9) == pytest.approx(10 - 1.2816 * 2, abs=1e-3)
    # mean − z·sd = 7.437; needs ΔL + L_min
    assert sellable_with_prob(10.0, 2.0, 5.0, 2.4, p=0.9)
    assert not sellable_with_prob(10.0, 2.0, 5.0, 2.5, p=0.9)
    # higher risk appetite p → stricter
    assert sellable_with_prob(10.0, 2.0, 5.0, 3.0, p=0.6)


def test_mango_ambient_calibration(cfg):
    """17.5 ref-days at 13 °C, Q10 = 2, at 28 °C ≈ 6.2 days — within 4–10 days."""
    mango = comm(cfg, "mango")
    days_at_ambient = mango.shelf_life_ref_days / consumption_rate(28.0, mango, 1.5)
    assert days_at_ambient == pytest.approx(17.5 / 2 ** 1.5, abs=0.05)
    assert 4.0 <= days_at_ambient <= 10.0
