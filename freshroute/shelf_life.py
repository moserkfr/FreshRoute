"""Shelf-life model.

Life is tracked in *reference-days*: one reference-day is one day of storage at the
commodity's ideal temperature ``t_ref_c``. Warmer storage burns reference-days faster
following the Q10 rule; storage below the chilling threshold burns them faster still
(chilling injury). Quality is the fraction of total reference life that remains.

All functions are pure and take plain numbers so the planner, the simulator and the tests
can share them. (The validator deliberately re-implements the arithmetic.)
"""

from __future__ import annotations

from statistics import NormalDist
from typing import Protocol

from freshroute.config import PriceBandsCfg


class ThermalParams(Protocol):
    """Anything with these attributes (config CommodityCfg or schemas.Commodity)."""

    t_ref_c: float
    q10: float
    chill_threshold_c: float | None
    shelf_life_ref_days: float


def consumption_rate(temp_c: float, c: ThermalParams, chill_penalty: float) -> float:
    """Reference-days of life consumed per real day at ``temp_c``.

    Q10 rule: every +10 °C multiplies the decay rate by q10. Below t_ref the rate drops below 1
    (cooler storage extends life) unless the chilling threshold is crossed, in which case the
    rate is multiplied by ``chill_penalty`` — e.g. mango in a 2 °C reefer.
    """
    rate = c.q10 ** ((temp_c - c.t_ref_c) / 10.0)
    if c.chill_threshold_c is not None and temp_c < c.chill_threshold_c:
        rate *= chill_penalty
    return rate


def delta_life(hours: float, temp_c: float, c: ThermalParams, chill_penalty: float) -> float:
    """ΔL (reference-days) consumed over ``hours`` at ``temp_c``: (h/24)·q10^((T−T_ref)/10)."""
    return (hours / 24.0) * consumption_rate(temp_c, c, chill_penalty)


def trip_delta_life(
    c: ThermalParams,
    *,
    ambient_c: float,
    cargo_c: float,
    load_h: float,
    transit_h: float,
    unload_h: float,
    chill_penalty: float,
) -> float:
    """Total ΔL for a trip: loading at ambient, transit at cargo temp, unloading at ambient.

    ``transit_h`` includes any waiting for the receive window (cargo stays on the truck).
    """
    return (
        delta_life(load_h, ambient_c, c, chill_penalty)
        + delta_life(transit_h, cargo_c, c, chill_penalty)
        + delta_life(unload_h, ambient_c, c, chill_penalty)
    )


def quality(remaining_life_days: float, c: ThermalParams) -> float:
    """Quality q = L_remaining / shelf_life_ref_days, clipped to [0, 1]."""
    return min(1.0, max(0.0, remaining_life_days / c.shelf_life_ref_days))


def price_multiplier(q: float, bands: PriceBandsCfg) -> float:
    """Disclosed quality → price multiplier m(q).

    q ≥ full_q → 1.0; mid_q ≤ q < full_q → linear mid_low_mult … 1.0;
    clearance_q ≤ q < mid_q → clearance_mult (processor / clearance only); below → 0 (spoiled).
    """
    if q >= bands.full_q:
        return 1.0
    if q >= bands.mid_q:
        frac = (q - bands.mid_q) / (bands.full_q - bands.mid_q)
        return bands.mid_low_mult + frac * (1.0 - bands.mid_low_mult)
    if q >= bands.clearance_q:
        return bands.clearance_mult
    return 0.0


def z_value(p: float) -> float:
    """One-sided standard-normal quantile, e.g. z(0.9) = 1.2816."""
    return NormalDist().inv_cdf(p)


def life_quantile(mean_days: float, sd_days: float, p: float) -> float:
    """Remaining life that is exceeded with probability ``p``: mean − z_p·sd."""
    return mean_days - z_value(p) * sd_days


def sellable_with_prob(
    mean_days: float, sd_days: float, delta_total_days: float, l_min_days: float, p: float = 0.9
) -> bool:
    """Chance constraint: P(L_arrival ≥ L_min) ≥ p  ⇔  mean − z_p·sd ≥ ΔL_total + L_min.

    ``p`` is the operator's "risk appetite" (UI slider, default 0.9).
    """
    return life_quantile(mean_days, sd_days, p) >= delta_total_days + l_min_days
