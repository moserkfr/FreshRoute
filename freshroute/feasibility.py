"""Feasibility filter: which (batch, destination, vehicle) options are allowed today.

For every combination this computes the trip (dispatch slot, mean travel, ETA rolled into the
receive window), the shelf life it uses, the arrival quality (mean and p10), and the money per
kg at the arrival date. An option survives only if

  * the lane is open and the vehicle available,
  * a reefer runs the batch's own temperature group (ambient trucks take anything),
  * the chance constraint holds: P(sellable on arrival) ≥ risk_p,
  * arrival quality p10 ≥ the destination's minimum,
  * the arrival date is inside the forecast horizon.

The chance and quality checks use the same rules as the validator plus a small safety margin
(``planner.safety_margin_days``), so rounding never turns an accepted option into a violation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from freshroute.config import AppConfig
from freshroute.logistics import estimate_trip
from freshroute.schemas import Batch, Commodity, Destination, Lane, State, Vehicle
from freshroute.shelf_life import life_quantile, price_multiplier, quality, z_value


@dataclass(frozen=True)
class Option:
    """One feasible way to ship (part of) a batch today. Money is ₹/kg."""

    batch_id: str
    commodity_id: str
    dest_id: str
    vehicle_id: str
    group: str | None              # reefer setpoint group; None for ambient
    arrival_date: date
    eta_hour: float
    delta_life_days: float
    q_mean: float
    q_p10: float
    price: float                   # forecast ₹/kg at full quality on the arrival date
    revenue_per_kg: float          # price · m(q_mean) − destination and commodity handling
    handling_per_kg: float


@dataclass
class FeasibleSet:
    options: list[Option]
    n_candidates: int
    rejected: dict[str, int]       # reason → count (for the pruning log)

    @property
    def pruning_ratio(self) -> float:
        return 1.0 - len(self.options) / self.n_candidates if self.n_candidates else 0.0


def forecast_lookup(state: State) -> dict[tuple[str, str, date], tuple[float, float, float, float]]:
    """(dest, commodity, date) → (demand mean, demand sd, price, fdi)."""
    return {(m.dest_id, m.commodity_id, m.date): (m.demand_kg_mean, m.demand_kg_sd,
                                                  m.price_per_kg_forecast, m.fdi)
            for m in state.market_forecast}


def sellable_margin(b: Batch, c: Commodity, delta: float, risk_p: float) -> float:
    """Reference-days of slack in the chance constraint (≥ 0 means it holds)."""
    l_min = c.min_sellable_quality * c.shelf_life_ref_days
    return life_quantile(b.remaining_life_mean_days, b.remaining_life_sd_days, risk_p) \
        - (delta + l_min)


def quality_p10(b: Batch, c: Commodity, delta: float) -> float:
    """Arrival quality exceeded with 90% probability (validator definition: unclipped)."""
    return (b.remaining_life_mean_days - z_value(0.9) * b.remaining_life_sd_days - delta) \
        / c.shelf_life_ref_days


def build(state: State, cfg: AppConfig) -> FeasibleSet:
    pc = cfg.default.planner
    risk_p = float(state.config_snapshot.get("risk_p", pc.risk_p))
    margin = pc.safety_margin_days
    bands = cfg.default.shelf_life.price_bands
    fc = forecast_lookup(state)
    comms = {c.id: c for c in state.commodities}
    dests = {d.dest_id: d for d in state.destinations}
    lanes: dict[str, Lane] = {ln.dest_id: ln for ln in state.lanes}
    vehicles: list[Vehicle] = list(state.vehicles)
    options: list[Option] = []
    rejected: dict[str, int] = {}
    n = 0

    def reject(why: str) -> None:
        rejected[why] = rejected.get(why, 0) + 1

    for b in state.batches:
        c = comms[b.commodity_id]
        for dest_id, lane in lanes.items():
            d: Destination = dests[dest_id]
            for v in vehicles:
                n += 1
                if not lane.open:
                    reject("lane_closed")
                    continue
                if not v.available:
                    reject("vehicle_unavailable")
                    continue
                group = c.temp_group if v.type == "reefer" else None
                trip = estimate_trip(d=state.date, lane=lane, dest=d, commodity=c, vehicle=v,
                                     ambient_c=state.ambient_temp_c, cfg=cfg)
                arr = trip.eta.date()
                if (dest_id, c.id, arr) not in fc:
                    reject("beyond_horizon")
                    continue
                if sellable_margin(b, c, trip.delta_life_days, risk_p) < margin:
                    reject("shelf_life_chance")
                    continue
                q10 = quality_p10(b, c, trip.delta_life_days)
                if q10 < d.min_quality_accepted + margin / c.shelf_life_ref_days:
                    reject("min_quality")
                    continue
                q_mean = quality(b.remaining_life_mean_days - trip.delta_life_days, c)
                _, _, price, _ = fc[(dest_id, c.id, arr)]
                handling = d.handling_cost_per_kg + c.handling_cost_per_kg
                options.append(Option(
                    batch_id=b.batch_id, commodity_id=c.id, dest_id=dest_id,
                    vehicle_id=v.vehicle_id, group=group, arrival_date=arr,
                    eta_hour=trip.eta.hour + trip.eta.minute / 60,
                    delta_life_days=trip.delta_life_days, q_mean=q_mean, q_p10=q10,
                    price=price, revenue_per_kg=price * price_multiplier(q_mean, bands) - handling,
                    handling_per_kg=handling))
    return FeasibleSet(options=options, n_candidates=n, rejected=rejected)
