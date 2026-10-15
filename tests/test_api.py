"""API endpoint contracts (FastAPI TestClient, no server needed)."""

from __future__ import annotations

import json
import warnings
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

import api.main as api_main
from freshroute import milp
from freshroute.schemas import Plan, State
from freshroute.whatif import DemandSpike, NewBatches, PriceShock, apply_event

warnings.filterwarnings("ignore")

FAST = "fefo_greedy"  # heuristic planner: contract tests that don't need the MILP use it


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(api_main.app)


@pytest.fixture(scope="module")
def inst(client):
    r = client.post("/instance", json={"scenario_id": "normal", "seed": 51})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture(scope="module")
def ours(client, inst):
    r = client.post("/plan", json={"state": inst["state"]})
    assert r.status_code == 200, r.text
    return r.json()


# ------------------------------------------------------------------ meta


def test_health_and_docs(client):
    h = client.get("/health").json()
    assert h["status"] == "ok" and "freshroute" in h["planners"]
    paths = client.get("/openapi.json").json()["paths"]
    for p in ("/health", "/scenarios", "/instance", "/plan", "/whatif", "/simulate",
              "/eval/latest", "/market/pulse"):
        assert p in paths
    assert client.get("/docs").status_code == 200


def test_scenarios_lists_all_with_seed_sets(client, cfg):
    j = client.get("/scenarios").json()
    assert {s["id"] for s in j["scenarios"]} == set(cfg.scenarios.scenarios)
    assert j["seeds"]["hidden_min"] == cfg.default.seeds.hidden_min


# ------------------------------------------------------------------ instance


def test_instance_is_reproducible_and_valid(client, inst):
    again = client.post("/instance", json={"scenario_id": "normal", "seed": 51}).json()
    assert again["fingerprint"] == inst["fingerprint"]
    state = State.model_validate(inst["state"])
    assert state.batches and state.date.isoformat() == inst["start_date"]


def test_instance_hides_unannounced_events(client):
    j = client.post("/instance", json={"scenario_id": "lane_closure", "seed": 51}).json()
    assert j["announced_events"] == []
    assert all(ln["open"] for ln in j["state"]["lanes"])


@pytest.mark.parametrize("body,code", [
    ({"scenario_id": "normal", "seed": 1000}, 403),     # hidden set is locked
    ({"scenario_id": "no_such", "seed": 51}, 404),
    ({"scenario_id": "normal", "seed": 51, "risk_p": 1.5}, 422),
])
def test_instance_rejects(client, body, code):
    assert client.post("/instance", json=body).status_code == code


# ------------------------------------------------------------------ plan


def test_plan_ours_is_valid_and_explained(ours, inst):
    plan = Plan.model_validate(ours["plan"])
    assert plan.violations == [] and not plan.solver_meta.fallback_used
    assert {a.batch_id for a in plan.allocations} <= set(plan.reasons)
    s = ours["summary"]
    assert s["shipped_kg"] + s["held_kg"] == pytest.approx(s["stock_kg"], abs=1.0)
    assert s["violations"] == 0 and s["trips"] == len(plan.vehicle_assignments)


def test_plan_baseline_reports_validator_findings(client, inst):
    j = client.post("/plan", json={"state": inst["state"], "planner": "fifo_nearest"}).json()
    assert j["plan"]["planner_name"] == "fifo_nearest"
    assert j["summary"]["violations"] == len(j["plan"]["violations"])


def test_plan_falls_back_instead_of_failing(client, inst, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("solver exploded")
    monkeypatch.setattr(milp, "solve", boom)
    j = client.post("/plan", json={"state": inst["state"]}).json()
    assert j["plan"]["solver_meta"]["fallback_used"] is True
    assert j["plan"]["violations"] == []


def test_plan_rejects_inconsistent_state(client, inst):
    bad = json.loads(json.dumps(inst["state"]))
    bad["batches"][0]["commodity_id"] = "durian"
    r = client.post("/plan", json={"state": bad, "planner": FAST})
    assert r.status_code == 422 and "durian" in r.text


# ------------------------------------------------------------------ what-if


def test_whatif_vehicle_down_replans_without_it(client, inst, ours):
    r = client.post("/whatif", json={"state": inst["state"], "previous_plan": ours["plan"],
                                     "event": {"type": "vehicle_down",
                                               "params": {"vehicle_id": "R1"}}})
    assert r.status_code == 200, r.text
    j = r.json()
    assert all(a["vehicle_id"] != "R1" for a in j["plan"]["allocations"])
    assert j["plan"]["violations"] == []
    used_r1 = any(a["vehicle_id"] == "R1" for a in ours["plan"]["allocations"])
    assert (len(j["diff"]["changes"]) > 0) == used_r1


@pytest.mark.parametrize("event", [
    {"type": "lane_closure", "params": {"dest_id": "blr_kr"}},
    {"type": "demand_spike", "params": {"dest_id": "mysuru", "mult": 1.8, "duration_days": 2}},
    {"type": "price_shock", "params": {"dest_id": "blr_kr", "commodity_id": "tomato",
                                       "mult": 0.5}},
    {"type": "new_batches", "params": {"batches": [{"commodity_id": "mango", "qty_kg": 800}]}},
])
def test_whatif_events(client, inst, event):
    r = client.post("/whatif", json={"state": inst["state"], "event": event, "planner": FAST})
    assert r.status_code == 200, r.text
    j = r.json()
    if event["type"] == "lane_closure":
        assert all(a["dest_id"] != "blr_kr" for a in j["plan"]["allocations"])
    if event["type"] == "new_batches":
        assert any(c["change"] == "new_batch" for c in j["diff"]["changes"])
        assert j["diff"]["summary_delta"]["stock_kg"] == pytest.approx(800)


@pytest.mark.parametrize("event", [
    {"type": "vehicle_down", "params": {"vehicle_id": "ZZ"}},
    {"type": "lane_closure", "params": {"dest_id": "atlantis"}},
    {"type": "demand_spike", "params": {"dest_id": "mysuru", "mult": -1}},
    {"type": "teleport", "params": {}},
])
def test_whatif_rejects_bad_events(client, inst, event):
    r = client.post("/whatif", json={"state": inst["state"], "event": event, "planner": FAST})
    assert r.status_code == 422


def test_apply_event_is_pure_and_scoped(inst, cfg):
    state = State.model_validate(inst["state"])
    before = state.model_dump_json()
    spiked = apply_event(state, DemandSpike(type="demand_spike", params={
        "dest_id": "mysuru", "commodity_id": "tomato", "mult": 2.0, "duration_days": 1}), cfg)
    assert state.model_dump_json() == before
    old = {(m.dest_id, m.commodity_id, m.date): m.demand_kg_mean for m in state.market_forecast}
    for m in spiked.market_forecast:
        hit = (m.dest_id, m.commodity_id, m.date) == ("mysuru", "tomato", state.date)
        assert m.demand_kg_mean == pytest.approx(old[(m.dest_id, m.commodity_id, m.date)]
                                                 * (2.0 if hit else 1.0))
    shocked = apply_event(state, PriceShock(type="price_shock", params={
        "dest_id": "blr_kr", "mult": 0.5}), cfg)
    price = {(m.dest_id, m.commodity_id, m.date): m.price_per_kg_forecast
             for m in state.market_forecast}
    tomorrow = state.date + timedelta(days=1)
    later = [m for m in shocked.market_forecast if m.date == tomorrow]
    assert later
    for m in later:  # a price shock persists past its first day, at the shocked market only
        mult = 0.5 if m.dest_id == "blr_kr" else 1.0
        assert m.price_per_kg_forecast == pytest.approx(
            price[(m.dest_id, m.commodity_id, m.date)] * mult)
    nb = apply_event(state, NewBatches(type="new_batches", params={"batches": [
        {"commodity_id": "onion", "qty_kg": 100, "grade": "B"}] * 2}), cfg)
    new = [b for b in nb.batches if b.batch_id.startswith("NEW-")]
    assert len({b.batch_id for b in new}) == 2
    assert new[0].remaining_life_mean_days == pytest.approx(
        cfg.commodity("onion").shelf_life_ref_days * cfg.default.generator.grade_life_mult["B"])
    assert len(nb.config_snapshot["whatif_events"]) == 1


# ------------------------------------------------------------------ simulate / eval / pulse


def test_simulate_short_run(client):
    r = client.post("/simulate", json={"scenario_id": "normal", "seed": 51, "days": 2,
                                       "planners": ["fifo_nearest", FAST]})
    assert r.status_code == 200, r.text
    runs = r.json()["runs"]
    assert [x["planner"] for x in runs] == ["fifo_nearest", FAST]
    for x in runs:
        assert x["metrics"]["received_kg"] > 0 and x["result"]["days"]


def test_simulate_limits(client, cfg):
    over = cfg.default.api.simulate_max_days + 1
    assert client.post("/simulate", json={"days": over}).status_code == 422
    assert client.post("/simulate", json={"seed": 2000}).status_code == 403


def test_eval_latest(client, tmp_path, monkeypatch):
    monkeypatch.setattr(api_main, "REPORTS_DIR", tmp_path)
    assert client.get("/eval/latest").status_code == 404
    (tmp_path / "eval_20260101_000000.json").write_text(json.dumps(
        {"meta": {"n_seeds": 2}, "runs": [1, 2]}), encoding="utf-8")
    j = client.get("/eval/latest").json()
    assert "runs" not in j and j["source_file"] == "eval_20260101_000000.json"
    assert client.get("/eval/latest", params={"include_runs": True}).json()["runs"] == [1, 2]
    (tmp_path / "final").mkdir()
    (tmp_path / "final" / "eval_20250101_000000.json").write_text('{"meta": {}}',
                                                                  encoding="utf-8")
    assert client.get("/eval/latest").json()["source_file"] == "final/eval_20250101_000000.json"


def test_market_pulse(client, cfg):
    r = client.get("/market/pulse", params={"scenario_id": "festival_peak", "seed": 51})
    assert r.status_code == 200, r.text
    j = r.json()
    assert len(j["destinations"]) == len(cfg.network.destinations)
    d = j["destinations"][0]
    c = d["commodities"][0]
    assert len(c["history"]) == cfg.default.api.pulse.history_days
    assert c["direction"] in ("up", "down", "flat") and c["forecast"]
    assert any(f["festival"] == "deepavali" for f in d["festival_radar"])
    one = client.get("/market/pulse", params={"scenario_id": "festival_peak", "seed": 51,
                                              "dest_id": "mysuru"}).json()
    assert [x["dest_id"] for x in one["destinations"]] == ["mysuru"]


@pytest.mark.parametrize("params,code", [
    ({"date": "2020-01-01"}, 422),
    ({"dest_id": "atlantis"}, 404),
    ({"seed": 1000}, 403),
])
def test_market_pulse_rejects(client, params, code):
    assert client.get("/market/pulse", params=params).status_code == code
