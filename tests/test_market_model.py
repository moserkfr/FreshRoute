"""M2: the event study recovers an injected festival effect; forecast modes differ only in
calendar terms; regime/seasonality fitting returns well-formed tables."""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from freshroute.calendar_features import resolve
from freshroute.config import FestivalCfg
from freshroute.fit_market import fit_festivals, fit_regime, fit_seasonality, fit_weekday
from freshroute.market_model import FILES, MarketParams, expected

UPLIFT_ARR, UPLIFT_PRICE = 0.30, 0.10
EFFECT_TAUS = (-2, -1, 0)


def synthetic_panel(cfg, seed: int = 0) -> tuple[pd.DataFrame, pd.DataFrame]:
    """4 years × 3 Karnataka markets of tomato with a known festival uplift."""
    rng = np.random.default_rng(seed)
    fest_dates = [pd.Timestamp(f"{y}-10-{10 + 3 * (y - 2021)}") for y in range(2021, 2025)]
    days = pd.date_range("2021-01-01", "2024-12-31", freq="D")
    rows = []
    for m in ("M1", "M2", "M3"):
        level = rng.uniform(50, 150)
        for t in days:
            arr = level * (1 + 0.3 * np.sin(2 * np.pi * t.month / 12))
            price = 20.0 * (1 - 0.2 * np.sin(2 * np.pi * t.month / 12))
            for f in fest_dates:
                if (t - f).days in EFFECT_TAUS:
                    arr *= 1 + UPLIFT_ARR
                    price *= 1 + UPLIFT_PRICE
            rows.append({"date": t, "region": "KA", "district": "Kolar", "market": m,
                         "commodity_id": "tomato",
                         "arrival_t": arr * np.exp(rng.normal(0, 0.05)),
                         "price": price * np.exp(rng.normal(0, 0.03))})
    cal = pd.DataFrame({"festival": "testfest", "date": fest_dates, "regions": "KA",
                        "type": "hindu"})
    return pd.DataFrame(rows), cal


@pytest.fixture(scope="module")
def panel(cfg):
    return synthetic_panel(cfg)


def test_event_study_recovers_injected_effect(cfg, panel):
    df, cal = panel
    fx = fit_festivals(cfg, df, cal)
    eps = abs(cfg.default.market.demand_price_elasticity)
    truth = UPLIFT_ARR + eps * UPLIFT_PRICE
    hit = fx[(fx.festival == "testfest") & (fx.commodity == "tomato") & (fx.region == "KA")]
    for tau in EFFECT_TAUS:
        row = hit[hit.tau == tau]
        assert len(row) == 1, f"effect at τ={tau} not kept"
        r = row.iloc[0]
        assert r.fdi_lo - 0.03 <= truth <= r.fdi_hi + 0.03
        assert r.fdi_mean == pytest.approx(truth, abs=0.05)
    # Days without an injected effect should mostly be dropped (CI includes 0) or be tiny.
    quiet = hit[~hit.tau.isin(EFFECT_TAUS)]
    assert (quiet.fdi_mean.abs() < 0.08).all()
    assert list(fx.columns) == FILES["festival_effects"]


def test_seasonality_and_weekday_shapes(cfg, panel):
    df, _ = panel
    season, _ = fit_seasonality(cfg, df, {"KA"}, "Kolar")
    assert len(season) == 12 * len(cfg.commodities)
    tom = season[season.commodity_id == "tomato"].set_index("month")
    # arrivals peak around month 3 (sin), price moves the other way
    assert tom.s_demand[3] > tom.s_demand[9]
    assert tom.s_price[3] < tom.s_price[9]
    assert np.exp(np.log(tom.s_demand).mean()) == pytest.approx(1.0, abs=1e-3)
    wd = fit_weekday(cfg, df)
    assert set(wd.dest_id) == {d.dest_id for d in cfg.network.destinations}
    assert (wd.w_demand >= 0).all()


def test_regime_table(cfg, panel):
    df, _ = panel
    reg = fit_regime(cfg, df).set_index("regime")
    assert reg.r_supply["glut"] > 1.0 > reg.r_supply["scarcity"]
    assert (reg.r_demand == 1.0).all()


def test_forecast_modes_differ_only_in_calendar_terms():
    fx = pd.DataFrame([{"festival": "f", "commodity": "tomato", "region": "KA", "tau": 0,
                        "fdi_mean": 0.25, "fdi_lo": 0.1, "fdi_hi": 0.4, "pu_mean": 0.1,
                        "n_years": 4}])
    params = MarketParams(
        base=pd.DataFrame([{"dest_id": "d", "commodity_id": "tomato", "base_price_per_kg": 20.0,
                            "base_demand_kg": 1000.0}]),
        weekday=pd.DataFrame(columns=FILES["weekday"]),
        seasonality=pd.DataFrame(columns=FILES["seasonality"]),
        festival_effects=fx,
        festival_calendar=pd.DataFrame([{"festival": "f", "date": "2025-10-20",
                                         "regions": "KA", "type": "hindu"}]),
        regime=pd.DataFrame(columns=FILES["regime"]),
        noise=pd.DataFrame(columns=FILES["noise"]))
    kw = dict(dest_id="d", region="KA", commodity_id="tomato")
    on = expected(params, **kw, d=date(2025, 10, 20), include_calendar=True)
    off = expected(params, **kw, d=date(2025, 10, 20), include_calendar=False)
    assert on.demand_kg == pytest.approx(off.demand_kg * 1.25)
    assert on.price_per_kg == pytest.approx(off.price_per_kg * 1.10)
    plain_on = expected(params, **kw, d=date(2025, 10, 25), include_calendar=True)
    plain_off = expected(params, **kw, d=date(2025, 10, 25), include_calendar=False)
    assert plain_on == plain_off
    other_region = expected(params, dest_id="d", region="TN", commodity_id="tomato",
                            d=date(2025, 10, 20))
    assert other_region.demand_kg == pytest.approx(off.demand_kg)


@pytest.mark.parametrize("match_date,expect", [
    (date(2025, 8, 9), date(2025, 8, 8)),     # Raksha Bandhan Sat → Varamahalakshmi Fri
    (date(2024, 8, 19), date(2024, 8, 16)),   # Mon → previous Fri
    (date(2023, 8, 31), date(2023, 8, 25)),   # Thu → previous Fri
    (date(2026, 8, 28), date(2026, 8, 21)),   # Fri → the Friday before
])
def test_friday_before_rule(match_date, expect):
    f = FestivalCfg(id="varamahalakshmi", tradition="north", match="Raksha Bandhan",
                    rule="friday_before", regions=["KA"], type="hindu")
    assert resolve(f, {"Raksha Bandhan": [match_date]}) == expect
