"""Demand + price model and the forecast interface used by planners.

    D[d,c,t] = B_D[d,c] · S_D[c](month) · W_D[d](weekday) · Π_f(1 + FDI_f(t − t_f)) · R_D · exp(ε_D)
    P[d,c,t] = B_P[d,c] · S_P[c](month) · W_P[d](weekday) · Π_f(1 + PU_f(t − t_f))
               · R_P · shock · exp(ε_P)

``expected_*`` functions return the multiplicative part without noise; the generator adds
noise and scenario shocks to make the simulator's truth. Fitted parameters come from
``data/processed`` (written by ``fit_market.py``). When a fitted file is absent its
``*_PLACEHOLDER.csv`` twin is used and a loud warning is printed: placeholder numbers are
clearly synthetic and must never reach a final report (``MarketParams.is_placeholder``).
"""

from __future__ import annotations

import logging
import math
import warnings
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import pandas as pd

from freshroute.config import REPO_ROOT, AppConfig

log = logging.getLogger(__name__)

PROCESSED_DIR = REPO_ROOT / "data" / "processed"

# Fitted file name → columns. Placeholders share the columns.
FILES: dict[str, list[str]] = {
    "market_base": ["dest_id", "commodity_id", "base_price_per_kg", "base_demand_kg"],
    "weekday": ["dest_id", "weekday", "w_demand", "w_price"],
    "seasonality": ["commodity_id", "month", "s_demand", "s_price", "s_supply"],
    "festival_effects": [
        "festival", "commodity", "region", "tau", "fdi_mean", "fdi_lo", "fdi_hi", "pu_mean",
        "n_years",
    ],
    "festival_calendar": ["festival", "date", "regions", "type"],
    "regime": ["regime", "r_price", "r_supply", "r_demand"],
    "noise": ["commodity_id", "sigma_log_demand", "sigma_log_price"],
}


class PlaceholderDataWarning(UserWarning):
    """Emitted whenever synthetic placeholder market data is in use."""


@dataclass
class MarketParams:
    base: pd.DataFrame
    weekday: pd.DataFrame
    seasonality: pd.DataFrame
    festival_effects: pd.DataFrame
    festival_calendar: pd.DataFrame
    regime: pd.DataFrame
    noise: pd.DataFrame
    placeholder_files: list[str] = field(default_factory=list)

    @property
    def is_placeholder(self) -> bool:
        return bool(self.placeholder_files)

    # Fast lookups built once -------------------------------------------------
    def __post_init__(self) -> None:
        self._base = {
            (r.dest_id, r.commodity_id): (r.base_price_per_kg, r.base_demand_kg)
            for r in self.base.itertuples()
        }
        self._wd = {(r.dest_id, int(r.weekday)): (r.w_demand, r.w_price)
                    for r in self.weekday.itertuples()}
        self._season = {(r.commodity_id, int(r.month)): (r.s_demand, r.s_price, r.s_supply)
                        for r in self.seasonality.itertuples()}
        self._regime = {r.regime: (r.r_price, r.r_supply, r.r_demand)
                        for r in self.regime.itertuples()}
        self._noise = {r.commodity_id: (r.sigma_log_demand, r.sigma_log_price)
                       for r in self.noise.itertuples()}
        # Festival effects keyed by (commodity, region) → {festival: {tau: (fdi, pu, lo, hi)}}
        FxTerm = tuple[float, float, float, float]  # (fdi, pu, lo, hi)
        self._fx: dict[tuple[str, str], dict[str, dict[int, FxTerm]]] = {}
        for r in self.festival_effects.itertuples():
            self._fx.setdefault((r.commodity, r.region), {}).setdefault(r.festival, {})[
                int(r.tau)
            ] = (r.fdi_mean, r.pu_mean, r.fdi_lo, r.fdi_hi)
        self._cal: list[tuple[str, date, set[str]]] = []
        for r in self.festival_calendar.itertuples():
            regions = {s.strip() for s in str(r.regions).split("|") if s.strip()}
            self._cal.append((r.festival, pd.Timestamp(r.date).date(), regions))

    # Components ---------------------------------------------------------------
    def base_level(self, dest_id: str, commodity_id: str) -> tuple[float, float]:
        """(B_P ₹/kg, B_D kg/day)."""
        return self._base.get((dest_id, commodity_id), (0.0, 0.0))

    def weekday_factor(self, dest_id: str, d: date) -> tuple[float, float]:
        """(W_D, W_P). Missing → 1."""
        return self._wd.get((dest_id, d.weekday()), (1.0, 1.0))

    def season_factor(self, commodity_id: str, d: date) -> tuple[float, float, float]:
        """(S_D, S_P, S_supply). Missing → 1."""
        return self._season.get((commodity_id, d.month), (1.0, 1.0, 1.0))

    def regime_factor(self, regime: str | None) -> tuple[float, float, float]:
        """(R_P, R_S, R_D). None / missing → 1."""
        if regime is None:
            return (1.0, 1.0, 1.0)
        return self._regime.get(regime, (1.0, 1.0, 1.0))

    def noise_sigma(self, commodity_id: str) -> tuple[float, float]:
        """(σ log demand, σ log price)."""
        return self._noise.get(commodity_id, (0.0, 0.0))

    def festival_terms(
        self, commodity_id: str, region: str, d: date
    ) -> list[tuple[str, int, float, float, float, float]]:
        """Active festival effects on day ``d``: (festival, tau, fdi, pu, fdi_lo, fdi_hi)."""
        effects = {}
        for key in ((commodity_id, region), (commodity_id, "ALL")):
            for fest, prof in self._fx.get(key, {}).items():
                effects.setdefault(fest, prof)
        out = []
        for fest, fdate, regions in self._cal:
            if fest not in effects or not (region in regions or "ALL" in regions):
                continue
            tau = (d - fdate).days
            if tau in effects[fest]:
                fdi, pu, lo, hi = effects[fest][tau]
                out.append((fest, tau, fdi, pu, lo, hi))
        return out


def _read(name: str, directory: Path, missing: list[str]) -> pd.DataFrame:
    real = directory / f"{name}.csv"
    ph = directory / f"{name}_PLACEHOLDER.csv"
    if real.exists():
        df = pd.read_csv(real)
    elif ph.exists():
        df = pd.read_csv(ph)
        missing.append(name)
    else:
        df = pd.DataFrame(columns=FILES[name])
        missing.append(name)
    absent = set(FILES[name]) - set(df.columns)
    if absent:
        raise ValueError(f"{real.name}: missing columns {sorted(absent)}")
    return df


_warned: set[tuple[str, ...]] = set()


def load_market_params(directory: Path | None = None) -> MarketParams:
    """Load fitted market parameters, falling back to placeholders with a loud warning."""
    directory = directory or PROCESSED_DIR
    missing: list[str] = []
    frames = {name: _read(name, directory, missing) for name in FILES}
    params = MarketParams(**{k if k != "market_base" else "base": v for k, v in frames.items()},
                          placeholder_files=missing)
    key = tuple(sorted(missing))
    if missing and key not in _warned:
        _warned.add(key)
        msg = (
            "\n" + "!" * 78 + "\n"
            f"!! PLACEHOLDER MARKET DATA in use for: {', '.join(missing)}\n"
            "!! These values are synthetic. Run `python tasks.py fit` with Agmarknet data in\n"
            "!! data/raw/ before producing any number for the final report.\n" + "!" * 78
        )
        warnings.warn(msg, PlaceholderDataWarning, stacklevel=2)
        log.warning(msg)
    return params


# ------------------------------------------------------------------ expectation


@dataclass(frozen=True)
class Expectation:
    demand_kg: float
    price_per_kg: float
    fdi: float          # combined festival demand uplift Π(1+FDI) − 1
    pu: float           # combined festival price uplift Π(1+PU) − 1
    festival: str | None


def expected(
    params: MarketParams,
    *,
    dest_id: str,
    region: str,
    commodity_id: str,
    d: date,
    regime: str | None = None,
    price_mult: float = 1.0,
    include_calendar: bool = True,
) -> Expectation:
    """Noise-free D and P for one destination × commodity × day.

    ``include_calendar=False`` drops festival terms — the ``no_calendar`` ablation.
    ``price_mult`` carries run-wide scenario price levels (e.g. glut −25%).
    """
    b_p, b_d = params.base_level(dest_id, commodity_id)
    s_d, s_p, _ = params.season_factor(commodity_id, d)
    w_d, w_p = params.weekday_factor(dest_id, d)
    r_p, _, r_d = params.regime_factor(regime)
    f_d = f_p = 1.0
    names: list[str] = []
    if include_calendar:
        for fest, _tau, fdi, pu, _lo, _hi in params.festival_terms(commodity_id, region, d):
            f_d *= 1.0 + fdi
            f_p *= 1.0 + pu
            names.append(fest)
    demand = b_d * s_d * w_d * f_d * r_d
    price = b_p * s_p * w_p * f_p * r_p * price_mult
    return Expectation(
        demand_kg=max(0.0, demand),
        price_per_kg=max(0.0, price),
        fdi=f_d - 1.0,
        pu=f_p - 1.0,
        festival=", ".join(sorted(set(names))) or None,
    )


def lognormal_sd(mean: float, sigma_log: float) -> float:
    """sd of mean·exp(ε − σ²/2), ε~N(0,σ²) — i.e. a mean-preserving lognormal."""
    return mean * math.sqrt(math.exp(sigma_log**2) - 1.0)


# ------------------------------------------------------------------ placeholders


def write_placeholders(cfg: AppConfig, directory: Path | None = None) -> list[Path]:
    """Write clearly synthetic placeholder parameter files (used until Agmarknet is fitted).

    Demand is sized from the hub's configured supply so the system is roughly balanced;
    prices are round-number wholesale levels with destination premia. None of these numbers
    are data — they exist so the pipeline runs end-to-end.
    """
    directory = directory or PROCESSED_DIR
    directory.mkdir(parents=True, exist_ok=True)
    base_price = {"spinach": 22.0, "tomato": 20.0, "okra": 32.0, "mango": 48.0,
                  "cabbage": 14.0, "onion": 26.0}
    dest_price_mult = {"blr_kr": 1.00, "blr_dc": 1.08, "mysuru": 0.97,
                       "chennai_koyambedu": 1.12, "hyd_bowenpally": 1.15, "hubballi": 1.03,
                       "malur_proc": 0.60}
    g = cfg.default.generator
    demand_to_supply = g.placeholder_demand_to_supply
    dests = cfg.network.destinations
    wsum = sum(d.demand_weight for d in dests)
    rows = []
    for c in cfg.commodities:
        supply = cfg.default.generator.base_daily_supply_kg[c.id]
        for d in dests:
            rows.append({
                "dest_id": d.dest_id,
                "commodity_id": c.id,
                "base_price_per_kg": round(base_price.get(c.id, 20.0)
                                           * dest_price_mult.get(d.dest_id, 1.0), 2),
                "base_demand_kg": round(supply * demand_to_supply * d.demand_weight / wsum, 1),
            })
    out = []
    out.append(_write(pd.DataFrame(rows), directory / "market_base_PLACEHOLDER.csv"))
    wd_rows = []
    for d in dests:
        for wd in range(7):
            w_d = 1.0
            if d.type == "mandi" and wd == 6:
                w_d = 0.8
            elif d.type == "retail_dc" and wd >= 5:
                w_d = 1.15
            wd_rows.append({"dest_id": d.dest_id, "weekday": wd, "w_demand": w_d, "w_price": 1.0})
    out.append(_write(pd.DataFrame(wd_rows), directory / "weekday_PLACEHOLDER.csv"))
    out.append(_write(pd.DataFrame([
        {"regime": "glut", "r_price": 1.0, "r_supply": 1.0, "r_demand": 1.0},
        {"regime": "normal", "r_price": 1.0, "r_supply": 1.0, "r_demand": 1.0},
        {"regime": "scarcity", "r_price": 1.0, "r_supply": 1.0, "r_demand": 1.0},
    ]), directory / "regime_PLACEHOLDER.csv"))
    out.append(_write(pd.DataFrame([
        {"commodity_id": c.id, "sigma_log_demand": g.placeholder_sigma_log_demand,
         "sigma_log_price": g.placeholder_sigma_log_price}
        for c in cfg.commodities
    ]), directory / "noise_PLACEHOLDER.csv"))
    return out


def _write(df: pd.DataFrame, path: Path) -> Path:
    df.to_csv(path, index=False, lineterminator="\n")
    return path


if __name__ == "__main__":
    from freshroute.config import load_config

    for p in write_placeholders(load_config()):
        print(f"wrote {p}")


# ------------------------------------------------------------------ forecasting


def forecast(
    market: pd.DataFrame,
    day: int,
    horizon_days: int,
    mode: str,
    revealed_events: list[dict] | None = None,
) -> list:
    """Planner-visible MarketDay rows for days ``day … day + horizon_days + 1``.

    The extra +1 day covers long lanes whose loads are sold the morning after dispatch.
    Modes:
      full        — seasonality + weekday + festivals + regime (+ announced/revealed events)
      no_calendar — the same without festival terms (ablation)
      flat        — yesterday's observed demand/price repeated (what baselines use)
    Never returns a ``*_true`` value for a future day: only ``flat`` reads truth, and only
    for day − 1, which has already happened.
    """
    from freshroute.generator import event_multipliers  # local: avoid import cycle
    from freshroute.schemas import MarketDay

    revealed_events = revealed_events or []
    days = range(day, day + horizon_days + 2)
    sub = market[market["day"].isin(days)]
    if mode == "flat":
        y = market[market["day"] == day - 1]
        prev = {(d, c): (float(dm), float(p)) for d, c, dm, p in
                zip(y["dest_id"], y["commodity_id"], y["demand_true"], y["price_true"],
                    strict=True)}
    out = []
    for r in sub.itertuples(index=False):
        if mode == "flat":
            dem, price = prev[(r.dest_id, r.commodity_id)]
            fdi, fest = 0.0, None
        else:
            suffix = "full" if mode == "full" else "nocal"
            dem, price = getattr(r, f"demand_{suffix}"), getattr(r, f"price_{suffix}")
            fdi = r.fdi if mode == "full" else 0.0
            fest = (r.festival or None) if mode == "full" else None
            dm, pm = event_multipliers(revealed_events, r.dest_id, r.commodity_id, r.day)
            dem, price = dem * dm, price * pm
        out.append(MarketDay(
            dest_id=r.dest_id, commodity_id=r.commodity_id, date=date.fromisoformat(r.date),
            demand_kg_mean=round(dem, 3),
            demand_kg_sd=round(lognormal_sd(dem, r.sigma_log_demand), 3),
            price_per_kg_forecast=round(price, 4),
            price_sd=round(lognormal_sd(price, r.sigma_log_price), 4),
            fdi=round(float(fdi), 4), festival=fest,
        ))
    return out
