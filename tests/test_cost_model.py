"""M3: hand-computed tiny instance — revenue, freight, handling, discount, disposal.

Two onion batches (1,000 kg and 500 kg, 50 of 60 reference days left → full price) go to
Bengaluru KR on one 6 t truck; the 500 kg batch is 10% off. True demand 800 kg at ₹20/kg.

  uplift of the discounted batch  = min(cap 0.40, 1.5 × 0.10)          = 0.15
  pool uplift (kg-weighted)       = (1000·0 + 500·0.15) / 1500          = 0.05
  sold                            = min(1500, 800 × 1.05)               = 840 kg
  split ∝ kg·(1+uplift)           : 1000 : 575 → 533.33 kg and 306.67 kg
  revenue                         = 533.33·20 + 306.67·20·0.9           = ₹16,186.67
  discount cost (reported only)   = 306.67·20·0.1                       = ₹613.33
  unsold → disposal               = 660 kg · ₹1                         = ₹660
  handling                        = 1500 · (dest + commodity ₹/kg)
  transport                       = fixed + ₹/km · 2 · 40 km
"""

from __future__ import annotations

import warnings
from datetime import date, datetime

import pandas as pd
import pytest

from freshroute.generator import Instance, build_fleet, to_commodity, to_destination
from freshroute.logistics import estimate_trip
from freshroute.metrics import run_metrics
from freshroute.schemas import IST, Allocation, Lane, Plan
from freshroute.simulator import run

warnings.filterwarnings("ignore")
START = date(2025, 7, 7)
DIST_KM = 40.0


def tiny_instance(cfg) -> Instance:
    dest = to_destination(next(d for d in cfg.network.destinations if d.dest_id == "blr_kr"))
    onion = to_commodity(next(c for c in cfg.commodities if c.id == "onion"))
    lane = Lane(origin_id="kolar_hub", dest_id="blr_kr", distance_km=DIST_KM,
                travel_time_h_mean=DIST_KM / 40.0, travel_time_h_sd=0.0)
    truck = next(v for v in build_fleet(cfg) if v.vehicle_id == "A3")
    harvest = datetime.combine(START, datetime.min.time(), tzinfo=IST).isoformat()
    batches = pd.DataFrame([
        {"day": 0, "batch_id": bid, "commodity_id": "onion", "qty_kg": kg, "grade": "A",
         "harvest_ts": harvest, "remaining_life_mean_days": 50.0,
         "remaining_life_sd_days": 0.0, "actual_life_days": 50.0, "cost_basis_per_kg": 5.0}
        for bid, kg in (("ON-1", 1000.0), ("ON-2", 500.0))])
    market = pd.DataFrame([
        {"day": k, "date": (START + pd.Timedelta(days=k)).isoformat(), "dest_id": "blr_kr",
         "commodity_id": "onion", "demand_full": 800.0, "price_full": 20.0,
         "demand_nocal": 800.0, "price_nocal": 20.0, "sigma_log_demand": 0.0,
         "sigma_log_price": 0.0, "fdi": 0.0, "pu": 0.0, "festival": "",
         "demand_true": 800.0, "price_true": 20.0} for k in range(-1, 8)])
    return Instance(scenario_id="tiny", seed=0, start_date=START, days=1, hub_id="kolar_hub",
                    destinations=[dest], lanes=[lane], vehicles=[truck], commodities=[onion],
                    batches=batches, market=market, ambient_forecast_c=[28.0],
                    ambient_true_c=[28.0], events=[],
                    overrides={"delay_prob": 0.0, "travel_time_mult": 1.0})


class FixedPlan:
    name = "fixed"
    forecast_mode = "flat"

    def plan(self, state, cfg):
        lane, dest, truck = state.lanes[0], state.destinations[0], state.vehicles[0]
        c = state.commodities[0]
        trip = estimate_trip(d=state.date, lane=lane, dest=dest, commodity=c, vehicle=truck,
                             ambient_c=state.ambient_temp_c, cfg=cfg)
        allocs = [Allocation(batch_id=b.batch_id, dest_id="blr_kr", vehicle_id=truck.vehicle_id,
                             qty_kg=b.qty_kg, discount_pct=10.0 if b.batch_id == "ON-2" else 0.0,
                             depart_ts=trip.depart, eta_ts=trip.eta, arrival_quality_mean=0.8,
                             arrival_quality_p10=0.8, expected_net_value=0.0)
                  for b in state.batches]
        return Plan(date=state.date, planner_name=self.name, allocations=allocs,
                    vehicle_assignments={truck.vehicle_id: "blr_kr"})


def test_hand_computed_ledger(cfg):
    inst = tiny_instance(cfg)
    m = run_metrics(run(inst, FixedPlan(), cfg))
    dest, onion, truck = inst.destinations[0], inst.commodities[0], inst.vehicles[0]
    sold_2 = 840 * 575 / 1575
    sold_1 = 840 - sold_2
    assert m["violations_total"] == 0
    assert m["sold_kg"] == pytest.approx(840.0)
    assert m["revenue_inr"] == pytest.approx(sold_1 * 20 + sold_2 * 20 * 0.9)
    assert m["discount_cost_inr"] == pytest.approx(sold_2 * 20 * 0.1)
    assert m["unsold_market_kg"] == pytest.approx(660.0)
    assert m["disposal_inr"] == pytest.approx(660.0 * cfg.default.cost.disposal_per_kg)
    assert m["handling_inr"] == pytest.approx(
        1500 * (dest.handling_cost_per_kg + onion.handling_cost_per_kg))
    assert m["transport_inr"] == pytest.approx(
        truck.fixed_cost_per_trip + truck.cost_per_km * 2 * DIST_KM)
    assert m["storage_inr"] == 0.0
    assert m["net_revenue_inr"] == pytest.approx(
        m["revenue_inr"] - m["transport_inr"] - m["handling_inr"] - m["disposal_inr"])
    assert m["spoilage_pct"] == pytest.approx(660 / 1500)
