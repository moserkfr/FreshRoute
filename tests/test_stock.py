"""Seller stock → State: same life rule as the generator, clear errors, plannable output."""

from __future__ import annotations

import warnings

import pytest
from fastapi.testclient import TestClient

import api.main as api_main
from freshroute.generator import generate
from freshroute.logistics import dispatch_ts
from freshroute.shelf_life import batch_life
from freshroute.simulator import initial_state
from freshroute.stock import StockBatch, StockError, build_state

warnings.filterwarnings("ignore")

DAY = "2026-10-15"


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(api_main.app)


@pytest.fixture(scope="module")
def base(cfg):
    return initial_state(generate("normal", 51, start_date=DAY, days=1, cfg=cfg), cfg)


def test_life_matches_generator(cfg, base):
    comms = {c.id: c for c in base.commodities}
    hub_temp = cfg.default.cost.hub_storage_temp_c
    morning = dispatch_ts(base.date, cfg)
    for b in base.batches[:10]:
        age_days = (morning - b.harvest_ts).total_seconds() / 86400
        mean, sd = batch_life(comms[b.commodity_id], b.grade, age_days, hub_temp, cfg)
        # harvest_ts is stored to the minute, so the recovered age is off by up to 30 s
        assert mean == pytest.approx(b.remaining_life_mean_days, abs=0.01)
        assert sd == pytest.approx(b.remaining_life_sd_days, abs=0.01)


def test_older_stock_has_less_life_and_spoiled_is_left_out(cfg, base):
    state, rows = build_state(base, [
        StockBatch(commodity_id="tomato", qty_kg=500, label="T-today"),
        StockBatch(commodity_id="tomato", qty_kg=500, harvest_date="2026-10-14"),
        StockBatch(commodity_id="spinach", qty_kg=200, harvest_date="2026-10-10"),
    ], cfg)
    fresh, older, gone = rows
    assert fresh.batch_id == "T-today" and older.batch_id == "MY-002"
    assert older.remaining_life_mean_days < fresh.remaining_life_mean_days
    assert older.days_left_at_hub < fresh.days_left_at_hub
    assert gone.status == "spoiled" and not gone.planned
    assert [b.batch_id for b in state.batches] == ["T-today", "MY-002"]
    assert state.vehicles == base.vehicles and state.market_forecast == base.market_forecast


@pytest.mark.parametrize("stock,msg", [
    ([StockBatch(commodity_id="durian", qty_kg=5)], "unknown commodity"),
    ([StockBatch(commodity_id="tomato", qty_kg=5, harvest_date="2026-10-16")], "after the"),
    ([StockBatch(commodity_id="tomato", qty_kg=5, label="a"),
      StockBatch(commodity_id="okra", qty_kg=5, label="a")], "duplicate"),
    ([StockBatch(commodity_id="tomato", qty_kg=5, label="<script>")], "lot name"),
    ([], "at least one"),
])
def test_bad_stock_is_rejected(cfg, base, stock, msg):
    with pytest.raises(StockError, match=msg):
        build_state(base, stock, cfg)


def test_endpoint_plans_seller_stock_without_violations(client):
    r = client.post("/stock/state", json={"plan_date": DAY, "batches": [
        {"commodity_id": "tomato", "qty_kg": 1200},
        {"commodity_id": "mango", "qty_kg": 2500, "harvest_date": "2026-10-12", "cost_per_kg": 40},
        {"commodity_id": "spinach", "qty_kg": 300, "grade": "B", "harvest_date": "2026-10-10"},
    ]})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["plan_date"] == DAY and j["excluded_kg"] == pytest.approx(300)
    assert [r["cost_assumed"] for r in j["rows"]] == [True, False, True]
    p = client.post("/plan", json={"state": j["state"]}).json()
    assert p["summary"]["violations"] == 0
    assert p["summary"]["stock_kg"] == pytest.approx(3700, abs=1)


def test_endpoint_rejects(client):
    assert client.post("/stock/state", json={"batches": []}).status_code == 422
    r = client.post("/stock/state", json={"batches": [{"commodity_id": "durian", "qty_kg": 1}]})
    assert r.status_code == 422 and "durian" in r.text


def test_pulse_on_own_day(client):
    r = client.get("/market/pulse", params={"scenario_id": "normal", "seed": 51, "date": DAY,
                                            "own_day": True})
    assert r.status_code == 200, r.text
    assert r.json()["date"] == DAY
    # without own_day the date must sit inside the scenario window
    assert client.get("/market/pulse", params={"date": DAY}).status_code == 422
