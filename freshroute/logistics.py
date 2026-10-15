"""Trip timing shared by planners: dispatch slot, receive windows, cargo temperature.

Time model (one dispatch wave per day):
  dispatch_hour  → loading starts (load_hours at ambient)
  depart_ts      = dispatch + load_hours
  arrival        = depart_ts + travel time
  eta_ts         = arrival, or the next receive-window opening if the destination is closed
                   (the load waits on the truck at cargo temperature)
  unload_hours at ambient after eta_ts; the load is sold on eta_ts's calendar date.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Protocol

from freshroute.config import AppConfig
from freshroute.schemas import IST, Commodity, Destination, Lane, Plan, State, Vehicle
from freshroute.shelf_life import trip_delta_life, z_value


class Planner(Protocol):
    """Anything that turns a morning State into a Plan."""

    name: str
    forecast_mode: str

    def plan(self, state: State, cfg: AppConfig) -> Plan: ...


def dispatch_ts(d: date, cfg: AppConfig) -> datetime:
    return datetime.combine(d, time(cfg.default.simulation.dispatch_hour), tzinfo=IST)


def depart_ts(d: date, cfg: AppConfig) -> datetime:
    return dispatch_ts(d, cfg) + timedelta(hours=cfg.default.simulation.load_hours)


def roll_to_window(arrival: datetime, open_h: int, close_h: int) -> datetime:
    """Earliest time ≥ arrival inside the daily receive window [open_h, close_h)."""
    day_start = datetime.combine(arrival.date(), time(0), tzinfo=arrival.tzinfo)
    opening = day_start + timedelta(hours=open_h)
    closing = day_start + timedelta(hours=close_h)
    if arrival < opening:
        return opening
    if arrival < closing:
        return arrival
    return opening + timedelta(days=1)


def cargo_temp_c(vehicle: Vehicle, group: str | None, ambient_c: float, cfg: AppConfig) -> float:
    """Ambient trucks follow ambient; reefers hold the setpoint of the group they run."""
    if vehicle.type == "reefer" and group is not None:
        return cfg.fleet.reefer_setpoints_c[group]  # type: ignore[index]
    return ambient_c


def planning_travel_h(lane: Lane, risk_p: float) -> float:
    """Travel hours not exceeded with probability ≈ ``risk_p`` (mean + z·sd, normal approx.).

    Used to decide *which day* a load is sold on. A lane whose mean arrival lands just before
    a market closes (Kolar → Chennai: 13:44 vs 14:00) misses the window on most delayed legs;
    planning the sale on the mean arrival books stock against the wrong day's demand.
    """
    return lane.travel_time_h_mean + max(z_value(risk_p), 0.0) * lane.travel_time_h_sd


@dataclass(frozen=True)
class TripEstimate:
    depart: datetime
    eta: datetime
    on_truck_h: float      # travel + waiting for the window
    cargo_c: float
    delta_life_days: float


def estimate_trip(
    *, d: date, lane: Lane, dest: Destination, commodity: Commodity, vehicle: Vehicle,
    ambient_c: float, cfg: AppConfig, travel_h: float | None = None,
) -> TripEstimate:
    """Expected trip (mean travel time unless ``travel_h`` is given) and life consumed."""
    dep = depart_ts(d, cfg)
    t_h = lane.travel_time_h_mean if travel_h is None else travel_h
    eta = roll_to_window(dep + timedelta(hours=t_h), dest.receive_open_h, dest.receive_close_h)
    on_truck = (eta - dep).total_seconds() / 3600.0
    cargo = cargo_temp_c(vehicle, commodity.temp_group, ambient_c, cfg)
    sim = cfg.default.simulation
    dl = trip_delta_life(commodity, ambient_c=ambient_c, cargo_c=cargo, load_h=sim.load_hours,
                         transit_h=on_truck, unload_h=sim.unload_hours,
                         chill_penalty=cfg.default.shelf_life.chill_penalty)
    return TripEstimate(depart=dep, eta=eta, on_truck_h=on_truck, cargo_c=cargo,
                        delta_life_days=dl)
