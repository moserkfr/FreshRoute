"""Seeded scenario-instance generator.

``generate(scenario_id, seed)`` builds everything the simulator needs for one run:
network + lanes, fleet, daily batch arrivals (with each batch's *true* remaining life),
and a market table holding both the planner-visible expectations and the sampled truth.

Determinism: every random draw comes from ``numpy.random.default_rng([seed, STREAM, day, …])``.
Each component (batches, market noise, shelf-life truth, …) has its own stream keyed by day,
so changing e.g. the number of simulated days or a market parameter does not shift the
batch draws of other days/components. Same seed → byte-identical instance (see
``Instance.fingerprint``).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import warnings
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy.stats import truncnorm

from freshroute.config import REPO_ROOT, AppConfig, ScenarioCfg, ScenarioEvent, load_config
from freshroute.market_model import MarketParams, expected, load_market_params
from freshroute.schemas import IST, Commodity, Destination, Lane, Vehicle
from freshroute.shelf_life import consumption_rate

log = logging.getLogger(__name__)

GENERATED_DIR = REPO_ROOT / "data" / "generated"
DISTANCE_MATRIX = REPO_ROOT / "data" / "raw" / "distance_matrix.csv"

# Random sub-stream ids. Never renumber: it would change every generated instance.
STREAM = {"batches": 1, "market": 2, "life": 3, "delays": 4, "events": 5, "ambient": 6,
          "festival": 7}


def rng_for(seed: int, stream: str, *keys: int) -> np.random.Generator:
    """Independent generator for (seed, component, keys…)."""
    return np.random.default_rng([seed, STREAM[stream], *keys])


# --------------------------------------------------------------------- network


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def delay_moments(cfg: AppConfig, distance_km: float, delay_prob: float) -> tuple[float, float]:
    """Mean and sd (hours) of the random delay on one leg.

    With prob ``delay_prob`` a leg incurs TruncNormal(μ, σ, ≥0) × distance/100 km hours.
    """
    t = cfg.default.travel
    scale = distance_km / 100.0
    mu, sd = t.delay_mean_h_per_100km * scale, t.delay_sd_h_per_100km * scale
    if sd <= 0:
        m1, m2 = mu, mu * mu
    else:
        a = (0.0 - mu) / sd
        dist = truncnorm(a, np.inf, loc=mu, scale=sd)
        m1, m2 = float(dist.mean()), float(dist.var() + dist.mean() ** 2)
    mean = delay_prob * m1
    var = delay_prob * m2 - mean**2
    return mean, math.sqrt(max(var, 0.0))


def build_lanes(cfg: AppConfig, travel_time_mult: float, delay_prob: float) -> list[Lane]:
    """Hub → destination lanes. OSRM matrix if present, else haversine × road factor."""
    hub = cfg.network.hub
    matrix: dict[str, float] = {}
    if DISTANCE_MATRIX.exists():
        df = pd.read_csv(DISTANCE_MATRIX)
        matrix = {r.dest_id: float(r.distance_km) for r in df.itertuples() if r.origin_id == hub.id}
    lanes = []
    used_fallback = []
    for d in cfg.network.destinations:
        if d.dest_id in matrix:
            dist = matrix[d.dest_id]
        else:
            dist = haversine_km(hub.lat, hub.lon, d.lat, d.lon) * cfg.default.travel.road_factor
            used_fallback.append(d.dest_id)
        base_h = dist / cfg.default.travel.speed_kmph * travel_time_mult
        dmean, dsd = delay_moments(cfg, dist, delay_prob)
        lanes.append(Lane(origin_id=hub.id, dest_id=d.dest_id, distance_km=round(dist, 1),
                          travel_time_h_mean=round(base_h + dmean, 3),
                          travel_time_h_sd=round(dsd, 3)))
    if used_fallback:
        _warn_once(f"distance_matrix.csv missing for {used_fallback}: using haversine × "
                   f"{cfg.default.travel.road_factor} road factor")
    return lanes


_WARNED: set[str] = set()


def _warn_once(msg: str) -> None:
    if msg not in _WARNED:
        _WARNED.add(msg)
        warnings.warn(msg, stacklevel=3)


def build_fleet(cfg: AppConfig) -> list[Vehicle]:
    out = []
    for entry in cfg.fleet.default_fleet:
        k = cfg.vehicle_class(entry.class_id)
        out.append(Vehicle(vehicle_id=entry.vehicle_id, type=k.type, class_id=k.class_id,
                           capacity_kg=k.capacity_kg, cost_per_km=k.cost_per_km,
                           fixed_cost_per_trip=k.fixed_cost_per_trip))
    return out


def to_commodity(c) -> Commodity:  # CommodityCfg → schema
    return Commodity(**c.model_dump(exclude={"harvest_age_max_days", "agmarknet_names"}))


def to_destination(d) -> Destination:
    fit_only = {"demand_weight", "agmarknet_markets", "price_premium"}
    return Destination(**d.model_dump(exclude=fit_only))


# -------------------------------------------------------------------- instance


@dataclass
class Instance:
    """One generated scenario run. ``batches`` and ``market`` hold truth columns that only
    the simulator may read (``actual_life_days``, ``demand_true``, ``price_true``)."""

    scenario_id: str
    seed: int
    start_date: date
    days: int
    hub_id: str
    destinations: list[Destination]
    lanes: list[Lane]
    vehicles: list[Vehicle]
    commodities: list[Commodity]
    batches: pd.DataFrame
    market: pd.DataFrame
    ambient_forecast_c: list[float]
    ambient_true_c: list[float]
    events: list[dict[str, Any]]
    overrides: dict[str, Any]
    placeholder_data: list[str] = field(default_factory=list)

    def date_of(self, day: int) -> date:
        return self.start_date + timedelta(days=day)

    def lane(self, dest_id: str) -> Lane:
        return next(ln for ln in self.lanes if ln.dest_id == dest_id)

    def fingerprint(self) -> str:
        """sha256 over every generated value — the determinism (hash) test uses this."""
        h = hashlib.sha256()
        h.update(self.batches.to_csv(index=False).encode())
        h.update(self.market.to_csv(index=False).encode())
        meta = {
            "lanes": [ln.model_dump() for ln in self.lanes],
            "vehicles": [v.model_dump() for v in self.vehicles],
            "ambient": [self.ambient_forecast_c, self.ambient_true_c],
            "events": self.events,
            "overrides": self.overrides,
        }
        h.update(json.dumps(meta, sort_keys=True, default=str).encode())
        return h.hexdigest()

    def save(self, directory: Path | None = None) -> Path:
        out = directory or GENERATED_DIR / f"{self.scenario_id}_seed{self.seed}"
        out.mkdir(parents=True, exist_ok=True)
        self.batches.to_parquet(out / "batches.parquet", index=False)
        self.market.to_parquet(out / "market.parquet", index=False)
        meta = {
            "scenario_id": self.scenario_id, "seed": self.seed,
            "start_date": self.start_date.isoformat(), "days": self.days,
            "hub_id": self.hub_id,
            "destinations": [d.model_dump() for d in self.destinations],
            "lanes": [ln.model_dump() for ln in self.lanes],
            "vehicles": [v.model_dump() for v in self.vehicles],
            "commodities": [c.model_dump() for c in self.commodities],
            "ambient_forecast_c": self.ambient_forecast_c,
            "ambient_true_c": self.ambient_true_c,
            "events": self.events, "overrides": self.overrides,
            "placeholder_data": self.placeholder_data,
            "fingerprint": self.fingerprint(),
        }
        (out / "meta.json").write_text(json.dumps(meta, indent=2, default=str), encoding="utf-8")
        return out


# ------------------------------------------------------------------ generation


def effective_regime(ov) -> str | None:
    """Regime whose fitted multipliers apply on top of the scenario.

    A scenario that states its own supply/price multipliers (harvest_glut: +60% arrivals,
    −25% prices) gives the *total* effect; its regime label is descriptive and the fitted
    regime factors are not applied again, otherwise the shock would be counted twice.
    """
    if ov.supply_mult != 1.0 or ov.price_mult != 1.0:
        return None
    return ov.regime


def _resolve_events(cfg: AppConfig, sc: ScenarioCfg, lanes: list[Lane], seed: int) -> list[dict]:
    """Turn scenario events into concrete ones (selectors → dest_id, ranges → realised values)."""
    by_dist = sorted(lanes, key=lambda ln: ln.distance_km)
    out = []
    for i, ev in enumerate(sc.events):
        e: ScenarioEvent = ev
        params = dict(e.params)
        if params.get("dest") == "farthest":
            params["dest"] = by_dist[-1].dest_id
        elif params.get("dest") == "nearest":
            params["dest"] = by_dist[0].dest_id
        if e.type == "price_shock":
            r = rng_for(seed, "events", i)
            params["mult"] = float(r.uniform(params.get("mult_low", 0.5),
                                             params.get("mult_high", 0.5)))
        out.append({"id": f"ev{i}", "type": e.type, "day": e.day, "announced": e.announced,
                    "params": params})
    return out


def event_multipliers(
    events: list[dict], dest_id: str, commodity_id: str, day: int
) -> tuple[float, float]:
    """(demand ×, price ×) from demand_spike / price_shock events in force on ``day``."""
    dm = pm = 1.0
    for ev in events:
        p = ev["params"]
        if p.get("dest") != dest_id:
            continue
        if ev["type"] == "demand_spike":
            if ev["day"] <= day < ev["day"] + int(p.get("duration_days", 1)):
                if p.get("commodity") in (None, commodity_id):
                    dm *= float(p["mult"])
        elif ev["type"] == "price_shock":
            if day >= ev["day"] and p.get("commodity") in (None, commodity_id):
                pm *= float(p["mult"])
    return dm, pm


def _market_table(cfg: AppConfig, mp: MarketParams, sc: ScenarioCfg, start: date,
                  n_dates: int, first_offset: int, events: list[dict], seed: int) -> pd.DataFrame:
    """Expectations + truth for every (date, dest, commodity).

    Columns ``*_full`` / ``*_nocal`` are what a forecaster with/without the festival calendar
    would expect (incl. announced events and run-wide scenario levels). ``*_true`` add festival
    estimation error, unannounced events and noise — only the simulator reads them.
    """
    ov = sc.overrides
    announced = [e for e in events if e["announced"]]
    rows = []
    fest_err: dict[tuple[str, str, str, int], float] = {}
    for k in range(n_dates):
        day = first_offset + k
        d = start + timedelta(days=day)
        rng = rng_for(seed, "market", day + 10_000)  # +offset keeps day −1 non-negative
        for dest in cfg.network.destinations:
            for c in cfg.commodities:
                kw = dict(params=mp, dest_id=dest.dest_id, region=dest.region,
                          commodity_id=c.id, d=d, regime=effective_regime(ov),
                          price_mult=ov.price_mult)
                full = expected(**kw, include_calendar=True)
                nocal = expected(**kw, include_calendar=False)
                # Truth festival effect = fitted estimate + error drawn within its CI, one draw
                # per (festival, commodity, region, year) so the whole window shifts together.
                f_d_true = 1.0
                for fest, _tau, fdi, _pu, lo, hi in mp.festival_terms(c.id, dest.region, d):
                    key = (fest, c.id, dest.region, d.year)
                    if key not in fest_err:
                        fest_err[key] = float(
                            rng_for(seed, "festival", d.year, len(fest_err)).standard_normal())
                    f_d_true *= 1.0 + fdi + fest_err[key] * (hi - lo) / 3.92
                calendar_ratio = f_d_true / (1.0 + full.fdi) if full.fdi > -1 else 1.0
                dm_a, pm_a = event_multipliers(announced, dest.dest_id, c.id, day)
                dm_t, pm_t = event_multipliers(events, dest.dest_id, c.id, day)
                sig_d, sig_p = mp.noise_sigma(c.id)
                eps_d = rng.normal(-0.5 * sig_d**2, sig_d)   # mean-preserving lognormal noise
                eps_p = rng.normal(-0.5 * sig_p**2, sig_p)
                rows.append({
                    "day": day, "date": d.isoformat(), "dest_id": dest.dest_id,
                    "commodity_id": c.id,
                    "demand_full": full.demand_kg * dm_a, "price_full": full.price_per_kg * pm_a,
                    "demand_nocal": nocal.demand_kg * dm_a,
                    "price_nocal": nocal.price_per_kg * pm_a,
                    "sigma_log_demand": sig_d, "sigma_log_price": sig_p,
                    "fdi": full.fdi, "pu": full.pu, "festival": full.festival or "",
                    "demand_true": full.demand_kg * max(calendar_ratio, 0.0) * dm_t
                    * math.exp(eps_d),
                    "price_true": full.price_per_kg * pm_t * math.exp(eps_p),
                })
    df = pd.DataFrame(rows)
    num = df.select_dtypes("number").columns.difference(["day"])
    df[num] = df[num].round(4)
    return df


def _batches(cfg: AppConfig, mp: MarketParams, sc: ScenarioCfg, start: date, days: int,
             seed: int) -> pd.DataFrame:
    g = cfg.default.generator
    ov = sc.overrides
    hub_temp = cfg.default.cost.hub_storage_temp_c + ov.ambient_delta_c
    chill = cfg.default.shelf_life.chill_penalty
    dispatch = time(cfg.default.simulation.dispatch_hour)
    grades = list(g.grade_probs)
    gprobs = np.array([g.grade_probs[x] for x in grades])
    mandi_ids = [d.dest_id for d in cfg.network.destinations if d.type == "mandi"]
    rows = []
    for day in range(days):
        d = start + timedelta(days=day)
        morning = datetime.combine(d, dispatch, tzinfo=IST)
        for ci, c in enumerate(cfg.commodities):
            rng = rng_for(seed, "batches", day, ci)
            life_rng = rng_for(seed, "life", day, ci)
            _, _, s_supply = mp.season_factor(c.id, d)
            _, r_supply, _ = mp.regime_factor(effective_regime(ov))
            vol = (g.base_daily_supply_kg[c.id] * s_supply * r_supply * ov.supply_mult
                   * math.exp(rng.normal(-0.5 * g.supply_noise_sd**2, g.supply_noise_sd)))
            base_price = float(np.mean([mp.base_level(m, c.id)[0] for m in mandi_ids]))
            rate = consumption_rate(hub_temp, c, chill)
            total, i = 0.0, 0
            while total < vol:
                qty = float(np.clip(rng.lognormal(math.log(g.qty_median_kg), g.qty_sigma),
                                    g.qty_min_kg, g.qty_max_kg))
                qty = min(qty, vol - total)
                if qty < g.qty_min_kg:
                    break
                grade = grades[int(rng.choice(len(grades), p=gprobs))]
                age = float(rng.uniform(0.0, c.harvest_age_max_days))
                mean = c.shelf_life_ref_days * g.grade_life_mult[grade] - age * rate
                sd = g.grade_sd_frac[grade] * max(mean, 0.0) * ov.life_sd_mult
                actual = max(0.0, float(life_rng.normal(mean, sd)))
                rows.append({
                    "day": day,
                    "batch_id": f"{c.id[:2].upper()}-{day:02d}-{i:03d}",
                    "commodity_id": c.id,
                    "qty_kg": round(qty, 1),
                    "grade": grade,
                    "harvest_ts": (morning - timedelta(days=age)).isoformat(timespec="minutes"),
                    "remaining_life_mean_days": round(mean, 4),
                    "remaining_life_sd_days": round(sd, 4),
                    "actual_life_days": round(actual, 4),
                    "cost_basis_per_kg": round(base_price * rng.uniform(*g.cost_basis_frac), 2),
                })
                total += qty
                i += 1
    return pd.DataFrame(rows)


def generate(
    scenario_id: str,
    seed: int,
    start_date: date | str | None = None,
    days: int | None = None,
    cfg: AppConfig | None = None,
    market_params: MarketParams | None = None,
) -> Instance:
    """Build the instance for ``scenario_id`` with ``seed``. All randomness is seeded."""
    cfg = cfg or load_config()
    mp = market_params or load_market_params()
    sc = cfg.scenarios.resolved(scenario_id)
    start = date.fromisoformat(str(start_date or sc.start_date))
    n_days = days or sc.days or cfg.default.simulation.days
    ov = sc.overrides
    delay_prob = ov.delay_prob if ov.delay_prob is not None else cfg.default.travel.delay_prob
    lanes = build_lanes(cfg, ov.travel_time_mult, delay_prob)
    events = _resolve_events(cfg, sc, lanes, seed)
    # Market table spans day −1 (yesterday's observation for the flat forecast) through the
    # planning horizon and the post-run settlement window.
    extra = cfg.default.market.horizon_days + cfg.default.simulation.settle_extra_days + 2
    market = _market_table(cfg, mp, sc, start, n_days + extra + 1, -1, events, seed)
    batches = _batches(cfg, mp, sc, start, n_days, seed)
    base_amb = cfg.default.simulation.ambient_temp_c + ov.ambient_delta_c
    amb_rng = rng_for(seed, "ambient", 0)
    amb_sd = cfg.default.generator.ambient_noise_sd_c
    ambient_true = [round(base_amb + float(amb_rng.normal(0, amb_sd)), 2) for _ in range(n_days)]
    return Instance(
        scenario_id=scenario_id, seed=seed, start_date=start, days=n_days,
        hub_id=cfg.network.hub.id,
        destinations=[to_destination(d) for d in cfg.network.destinations],
        lanes=lanes, vehicles=build_fleet(cfg),
        commodities=[to_commodity(c) for c in cfg.commodities],
        batches=batches, market=market,
        ambient_forecast_c=[base_amb] * n_days, ambient_true_c=ambient_true,
        events=events,
        overrides={**ov.model_dump(), "delay_prob": delay_prob},
        placeholder_data=list(mp.placeholder_files),
    )


def parse_seeds(text: str) -> list[int]:
    """"1-5,9" → [1,2,3,4,5,9]."""
    out: list[int] = []
    for part in text.split(","):
        if "-" in part:
            lo, hi = part.split("-")
            out.extend(range(int(lo), int(hi) + 1))
        elif part.strip():
            out.append(int(part))
    return out


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Generate seeded FreshRoute instances")
    ap.add_argument("--scenario", default="normal")
    ap.add_argument("--seeds", default="1-3")
    args = ap.parse_args(argv)
    cfg = load_config()
    for seed in parse_seeds(args.seeds):
        inst = generate(args.scenario, seed, cfg=cfg)
        out = inst.save()
        b = inst.batches
        print(f"{args.scenario} seed {seed}: {len(b)} batches, {b.qty_kg.sum() / 1000:.1f} t over "
              f"{inst.days} days → {out}  [{inst.fingerprint()[:12]}]")


if __name__ == "__main__":
    main()
