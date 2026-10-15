"""Market pulse: where prices stand against the seasonal norm, where they are heading, and
which festivals are coming. Display only — planners never read it.

For one instance (scenario × seed) and planning day, per destination × commodity:
  price_obs        realised price yesterday (day − 1, already happened), ₹/kg
  norm             noise-free seasonal × weekday price for that date, without festival terms,
                   regime or scenario price levels: "what this date usually costs", ₹/kg
  z                ln(price_obs / norm) / σ_log_price  (σ from the fitted noise model)
  momentum_pct     log-price OLS slope over the last ``api.pulse.history_days`` observed days,
                   as % change per 7 days
  regime_flag      glut if z ≤ −regime_z, scarcity if z ≥ +regime_z, else normal
  forecast         the planner-visible full forecast for today … today + H + 1
Per destination: a festival radar (calendar festivals in its region within ``radar_days``,
with the fitted demand / price uplift per commodity, when one exists).

Observed history before day −1 is drawn with the generator's market model and noise streams;
the instance itself only starts at day −1, so those earlier days never enter a simulation.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from freshroute.config import AppConfig
from freshroute.generator import Instance, _market_table
from freshroute.market_model import MarketParams, expected, forecast

Regime = Literal["glut", "normal", "scarcity"]
Direction = Literal["up", "down", "flat"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ForecastPoint(_Model):
    date: date
    price_per_kg: float
    price_sd: float
    demand_kg: float
    fdi: float


class CommodityPulse(_Model):
    commodity_id: str
    price_obs: float = Field(description="realised price on the last observed day, ₹/kg")
    norm: float = Field(description="seasonal norm for that date, ₹/kg")
    z: float
    momentum_pct: float = Field(description="% change per 7 days (log-price slope)")
    direction: Direction
    regime_flag: Regime
    history: list[float] = Field(description="observed ₹/kg, oldest first")
    forecast: list[ForecastPoint]


class FestivalUplift(_Model):
    commodity_id: str
    peak_fdi: float = Field(description="largest demand uplift in the window, fraction")
    peak_pu: float = Field(description="largest price uplift in the window, fraction")


class RadarItem(_Model):
    festival: str
    date: date
    days_until: int
    type: str
    uplifts: list[FestivalUplift] = Field(description="empty = no fitted effect for this region")


class DestPulse(_Model):
    dest_id: str
    name: str
    region: str
    regime_flag: Regime = Field(description="from the median z across commodities")
    commodities: list[CommodityPulse]
    festival_radar: list[RadarItem]


class PulseResponse(_Model):
    scenario_id: str
    seed: int
    date: date
    observed_through: date
    scenario_regime: str | None
    destinations: list[DestPulse]


def _flag(z: float, threshold: float) -> Regime:
    if z <= -threshold:
        return "glut"
    if z >= threshold:
        return "scarcity"
    return "normal"


def _observed(inst: Instance, cfg: AppConfig, mp: MarketParams, day: int, n: int) -> dict:
    """{(dest, commodity): [realised price for days day−n … day−1]}."""
    first = day - n
    m = inst.market
    obs = m[(m["day"] >= first) & (m["day"] < day)]
    frames = [obs]
    if first < -1:  # before the instance: same model and noise streams, never simulated
        sc = cfg.scenarios.resolved(inst.scenario_id)
        hist = _market_table(cfg, mp, sc, inst.start_date, -1 - first, first, inst.events,
                             inst.seed)
        frames.insert(0, hist)
    out: dict[tuple[str, str], list[float]] = {}
    for f in frames:
        for r in f.sort_values("day").itertuples(index=False):
            out.setdefault((r.dest_id, r.commodity_id), []).append(float(r.price_true))
    return out


def _momentum_pct(prices: list[float]) -> float:
    if len(prices) < 2 or min(prices) <= 0:
        return 0.0
    y = np.log(np.asarray(prices))
    slope = float(np.polyfit(np.arange(len(y)), y, 1)[0])
    return (math.exp(slope * 7) - 1) * 100


def _radar(cfg: AppConfig, mp: MarketParams, region: str, d: date,
           commodity_ids: list[str]) -> list[RadarItem]:
    pc = cfg.default.api.pulse
    horizon = d + timedelta(days=pc.radar_days)
    items = []
    for r in mp.festival_calendar.itertuples(index=False):
        fdate = date.fromisoformat(str(r.date)[:10])
        regions = {s.strip() for s in str(r.regions).split("|") if s.strip()}
        if not (d <= fdate <= horizon) or not (region in regions or "ALL" in regions):
            continue
        uplifts = []
        for cid in commodity_ids:
            fdi = pu = None
            # The fitted window can start before the festival day: scan it fully.
            for k in range(cfg.default.market.fdi_window[0], cfg.default.market.fdi_window[1] + 1):
                for fest, _tau, f, p, _lo, _hi in mp.festival_terms(cid, region,
                                                                     fdate + timedelta(days=k)):
                    if fest == r.festival:
                        fdi = f if fdi is None else max(fdi, f)
                        pu = p if pu is None else max(pu, p)
            if fdi is not None:
                uplifts.append(FestivalUplift(commodity_id=cid, peak_fdi=round(fdi, 4),
                                              peak_pu=round(pu or 0.0, 4)))
        items.append(RadarItem(festival=r.festival, date=fdate, days_until=(fdate - d).days,
                               type=str(r.type), uplifts=uplifts))
    return sorted(items, key=lambda x: (x.date, x.festival))


def market_pulse(inst: Instance, day: int, cfg: AppConfig, mp: MarketParams,
                 dest_id: str | None = None) -> PulseResponse:
    pc = cfg.default.api.pulse
    d = inst.date_of(day)
    last_obs = d - timedelta(days=1)
    observed = _observed(inst, cfg, mp, day, pc.history_days)
    fc = forecast(inst.market, day, cfg.default.market.horizon_days, "full", [])
    by_key: dict[tuple[str, str], list[ForecastPoint]] = {}
    for f in fc:
        by_key.setdefault((f.dest_id, f.commodity_id), []).append(ForecastPoint(
            date=f.date, price_per_kg=f.price_per_kg_forecast, price_sd=f.price_sd,
            demand_kg=f.demand_kg_mean, fdi=f.fdi))
    cids = [c.id for c in inst.commodities]
    dests = [x for x in inst.destinations if dest_id in (None, x.dest_id)]
    out = []
    for dst in dests:
        rows = []
        for cid in cids:
            hist = observed[(dst.dest_id, cid)]
            norm = expected(mp, dest_id=dst.dest_id, region=dst.region, commodity_id=cid,
                            d=last_obs, include_calendar=False).price_per_kg
            sig = mp.noise_sigma(cid)[1] or 1.0
            z = math.log(hist[-1] / norm) / sig if hist[-1] > 0 and norm > 0 else 0.0
            mom = _momentum_pct(hist)
            direction: Direction = ("flat" if abs(mom) < pc.momentum_flat_pct
                                    else "up" if mom > 0 else "down")
            rows.append(CommodityPulse(
                commodity_id=cid, price_obs=round(hist[-1], 2), norm=round(norm, 2),
                z=round(z, 3), momentum_pct=round(mom, 2), direction=direction,
                regime_flag=_flag(z, pc.regime_z), history=[round(p, 2) for p in hist],
                forecast=by_key.get((dst.dest_id, cid), [])))
        zs = [r.z for r in rows]
        out.append(DestPulse(
            dest_id=dst.dest_id, name=dst.name, region=dst.region,
            regime_flag=_flag(float(np.median(zs)) if zs else 0.0, pc.regime_z),
            commodities=rows, festival_radar=_radar(cfg, mp, dst.region, d, cids)))
    return PulseResponse(scenario_id=inst.scenario_id, seed=inst.seed, date=d,
                         observed_through=last_obs,
                         scenario_regime=inst.overrides.get("regime"), destinations=out)
