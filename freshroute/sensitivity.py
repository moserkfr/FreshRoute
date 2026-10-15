"""Parameter perturbations for the sensitivity (tornado) analysis.

Each perturbation scales one model parameter by a factor (e.g. 0.8 / 1.2) everywhere it is used:
the simulator's truth *and* what the planners are told, so the question answered is "how does
our advantage over the baseline change if the world is like this?", not "what if our model is
wrong". All planners see the same perturbed world (paired comparison).

  q10           every commodity's Q10 (temperature sensitivity of decay)
  life_sd       shelf-life uncertainty: the grade sd fractions of remaining life
  delay_prob    share of road legs that are delayed
  demand_sigma  day-to-day demand noise (log-sd)
  price_sigma   day-to-day price noise (log-sd)
  elasticity    demand-price elasticity ε_d used in the festival demand index
                (FDI = AU + |ε_d|·PU, so festival demand effects move with it)
  freight       truck costs: ₹/km and fixed ₹/trip of every vehicle class
"""

from __future__ import annotations

import dataclasses

from freshroute.config import AppConfig
from freshroute.market_model import MarketParams

PARAMS = ("q10", "life_sd", "delay_prob", "demand_sigma", "price_sigma", "elasticity", "freight")


def perturb(cfg: AppConfig, mp: MarketParams, param: str, factor: float
            ) -> tuple[AppConfig, MarketParams]:
    """Return copies of ``cfg`` and ``mp`` with ``param`` scaled by ``factor``."""
    if param not in PARAMS:
        raise KeyError(f"unknown sensitivity parameter '{param}'; known: {PARAMS}")
    cfg = cfg.model_copy(deep=True)
    frames = {f.name: getattr(mp, f.name) for f in dataclasses.fields(mp)}
    if param == "q10":
        for c in cfg.commodities:
            c.q10 = max(1.01, c.q10 * factor)
    elif param == "life_sd":
        g = cfg.default.generator
        g.grade_sd_frac = {k: v * factor for k, v in g.grade_sd_frac.items()}
    elif param == "delay_prob":
        t = cfg.default.travel
        t.delay_prob = min(1.0, t.delay_prob * factor)
    elif param in ("demand_sigma", "price_sigma"):
        col = "sigma_log_demand" if param == "demand_sigma" else "sigma_log_price"
        noise = mp.noise.copy()
        noise[col] = noise[col] * factor
        frames["noise"] = noise
    elif param == "elasticity":
        m = cfg.default.market
        old = abs(m.demand_price_elasticity)
        m.demand_price_elasticity *= factor
        shift = abs(m.demand_price_elasticity) - old
        fx = mp.festival_effects.copy()
        for col in ("fdi_mean", "fdi_lo", "fdi_hi"):
            fx[col] = fx[col] + shift * fx["pu_mean"]
        frames["festival_effects"] = fx
    elif param == "freight":
        for k in cfg.fleet.classes:
            k.cost_per_km *= factor
            k.fixed_cost_per_trip *= factor
    return cfg, MarketParams(**frames)
