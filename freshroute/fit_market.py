"""Fit the market model from Agmarknet → data/processed/*.csv + fit_report.json.

Input: data/raw/agmarknet_south.csv (written by fetch_agmarknet) and the festival calendar
(calendar_features). Arrivals (tonnes) are the demand proxy; modal price is converted from
₹/quintal to ₹/kg. Everything is fitted on ``data.fit_train_years``; ``data.fit_holdout_year``
is kept back to test whether festival terms improve the forecast.

What is fitted (model in market_model.py):
  market_base   B_P from real prices at each destination's markets (× price_premium);
                B_D is the hub's share of demand (generator.demand_to_supply), not the whole
                mandi's arrivals — a mid-size aggregator sells a small slice of KR market.
  seasonality   S(month) per commodity: STL (period 12) on monthly log series of every market
                with enough history, seasonal component averaged across markets. s_demand
                from all destination-region markets, s_supply from the hub's district (Kolar).
  weekday       W(weekday) per destination: median ratio to a centred 4-week median; a
                weekday with too few reports (a reporting gap) gets factor 1.
  festival      event study per (festival, commodity, region), τ ∈ market.fdi_window:
                baseline = median of the same weekday ±1..4 weeks outside festival windows,
                AU = arrivals/baseline − 1, PU = price/baseline − 1, FDI = AU + |ε_d|·PU,
                averaged over years with a bootstrap CI over years; kept if the CI excludes 0.
  regime        monthly arrivals deviation from STL trend+season, terciles → glut / normal /
                scarcity, R_S and R_P = mean ratio in each tercile (R_D = 1).
  noise         robust (MAD) sd of daily log deviations from a centred 4-week median.

Usage:  python -m freshroute.fit_market
"""

from __future__ import annotations

import json
import sys
import warnings
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
from statsmodels.tsa.seasonal import STL

from freshroute.config import AppConfig, load_config
from freshroute.market_model import FILES, PROCESSED_DIR

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw" / "agmarknet_south.csv"
CALENDAR = PROCESSED_DIR / "festival_calendar.csv"
REPORT = PROCESSED_DIR / "fit_report.json"
ROLL_DAYS = 28            # centred window for local level (weekday factor, noise)
BASELINE_WEEKS = 4        # festival baseline: same weekday ±1..4 weeks
MAD_TO_SD = 1.4826
PRICE_OUTLIER = 5.0       # drop modal prices > 5× or < 1/5 of the series median


# ------------------------------------------------------------------ loading


def load_raw(cfg: AppConfig, path: Path = RAW) -> pd.DataFrame:
    """Daily (date, market, commodity_id) panel: arrivals (t), price (₹/kg), region."""
    if not path.exists():
        raise FileNotFoundError(f"{path} missing — run `python -m freshroute.fetch_agmarknet`")
    raw = pd.read_csv(path, dtype={"variety": str, "grade": str})
    name_to_id = {n: c.id for c in cfg.commodities for n in c.agmarknet_names}
    raw = raw[raw["commodity"].isin(name_to_id)].copy()
    raw["commodity_id"] = raw["commodity"].map(name_to_id)
    raw["region"] = raw["state"].map(cfg.default.data.region_of_state)
    raw["date"] = pd.to_datetime(raw["date"])
    raw["price"] = pd.to_numeric(raw["modal_price"], errors="coerce") / 100.0  # ₹/q → ₹/kg
    raw["arrival_t"] = pd.to_numeric(raw["arrival_t"], errors="coerce")
    raw = raw[(raw["price"] > 0) & (raw["arrival_t"] >= 0)]
    med = raw.groupby(["market", "commodity_id"])["price"].transform("median")
    raw = raw[(raw["price"] < med * PRICE_OUTLIER) & (raw["price"] > med / PRICE_OUTLIER)]
    # One row per market × commodity × day: arrivals summed over varieties, price weighted.
    raw["pw"] = raw["price"] * raw["arrival_t"].clip(lower=1e-3)
    raw["w"] = raw["arrival_t"].clip(lower=1e-3)
    g = raw.groupby(["date", "region", "district", "market", "commodity_id"], as_index=False)
    df = g.agg(arrival_t=("arrival_t", "sum"), pw=("pw", "sum"), w=("w", "sum"))
    df["price"] = df["pw"] / df["w"]
    return df.drop(columns=["pw", "w"]).sort_values(["market", "commodity_id", "date"])


def years(df: pd.DataFrame, lo: int, hi: int) -> pd.DataFrame:
    y = df["date"].dt.year
    return df[(y >= lo) & (y <= hi)]


def load_calendar(path: Path = CALENDAR) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"{path} missing — run `python -m freshroute.calendar_features`")
    cal = pd.read_csv(path)
    cal["date"] = pd.to_datetime(cal["date"])
    return cal


def regions_of(row_regions: str, all_regions: list[str]) -> list[str]:
    return all_regions if row_regions == "ALL" else row_regions.split("|")


# ------------------------------------------------------------------ components


def local_level(s: pd.Series) -> pd.Series:
    """Centred rolling median over ROLL_DAYS calendar days (needs a DatetimeIndex)."""
    return s.rolling(f"{ROLL_DAYS}D", center=True, min_periods=5).median()


def monthly_stl(df: pd.DataFrame, value: str, min_months: int) -> pd.DataFrame:
    """STL seasonal + trend of log monthly medians for every series with enough history."""
    out = []
    for (m, c), g in df.groupby(["market", "commodity_id"]):
        s = g.set_index("date")[value]
        s = s[s > 0]
        mon = np.log(s.resample("MS").median()).dropna()
        if len(mon) < min_months:
            continue
        mon = mon.asfreq("MS").interpolate(limit_direction="both")
        res = STL(mon, period=12, robust=True).fit()
        out.append(pd.DataFrame({"market": m, "commodity_id": c, "month_start": mon.index,
                                 "log": mon.values, "seasonal": res.seasonal.values,
                                 "trend": res.trend.values}))
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


def monthly_mean_factor(df: pd.DataFrame, value: str) -> pd.DataFrame:
    """Fallback for seasonal crops (mango trades ~Apr–Jul, too gappy for STL): per series,
    log monthly median minus the series' mean, averaged by calendar month. Months with no
    trade have no estimate (factor 1 later)."""
    parts = []
    for _, g in df.groupby(["market", "commodity_id"]):
        s = g.set_index("date")[value]
        mon = np.log(s[s > 0].resample("MS").median()).dropna()
        if len(mon) < 6:
            continue
        parts.append(pd.DataFrame({"commodity_id": g["commodity_id"].iloc[0],
                                   "month_start": mon.index, "seasonal": mon - mon.mean()}))
    if not parts:
        return pd.DataFrame(columns=["commodity_id", "month", "factor"])
    return seasonal_factor(pd.concat(parts, ignore_index=True))


def seasonal_factor(stl: pd.DataFrame) -> pd.DataFrame:
    """Average STL seasonal component by (commodity, month) → multiplicative, geo-mean 1."""
    if stl.empty:
        return pd.DataFrame(columns=["commodity_id", "month", "factor"])
    stl = stl.assign(month=stl["month_start"].dt.month)
    f = stl.groupby(["commodity_id", "month"])["seasonal"].mean().reset_index()
    f["seasonal"] -= f.groupby("commodity_id")["seasonal"].transform("mean")
    f["factor"] = np.exp(f["seasonal"])
    return f[["commodity_id", "month", "factor"]]


def fit_seasonality(cfg: AppConfig, train: pd.DataFrame, dest_regions: set[str],
                    hub_district: str) -> tuple[pd.DataFrame, dict]:
    mm = cfg.default.data.fit_min_months
    dem = train[train["region"].isin(dest_regions)]
    hub = train[train["district"] == hub_district]

    def factor(src: pd.DataFrame, value: str, name: str) -> pd.DataFrame:
        f = seasonal_factor(monthly_stl(src, value, mm))
        # Commodities STL could not fit (seasonal crops) fall back to monthly means.
        rest = src[~src["commodity_id"].isin(set(f["commodity_id"]))]
        if not rest.empty:
            f = pd.concat([f, monthly_mean_factor(rest, value)], ignore_index=True)
        return f.rename(columns={"factor": name})

    grid = pd.DataFrame([(c.id, m) for c in cfg.commodities for m in range(1, 13)],
                        columns=["commodity_id", "month"])
    out = (grid.merge(factor(dem, "arrival_t", "s_demand"), how="left")
           .merge(factor(dem, "price", "s_price"), how="left")
           .merge(factor(hub, "arrival_t", "s_supply"), how="left"))
    missing = {col: sorted(out.loc[out[col].isna(), "commodity_id"].unique())
               for col in ("s_demand", "s_price", "s_supply")}
    # Hub-district supply seasonality falls back to the demand-side arrivals pattern, then 1.
    out["s_supply"] = out["s_supply"].fillna(out["s_demand"])
    out = out.fillna(1.0)
    return out.round(4), {k: v for k, v in missing.items() if v}


def fit_weekday(cfg: AppConfig, train: pd.DataFrame) -> pd.DataFrame:
    share = cfg.default.data.fit_weekday_closed_share
    rows = []
    for d in cfg.network.destinations:
        sub = train[train["market"].isin(d.agmarknet_markets)]
        if sub.empty:
            rows += [{"dest_id": d.dest_id, "weekday": w, "w_demand": 1.0, "w_price": 1.0}
                     for w in range(7)]
            continue
        parts = []
        for _, g in sub.groupby(["market", "commodity_id"]):
            g = g.set_index("date")
            parts.append(pd.DataFrame({
                "weekday": g.index.weekday,
                "r_d": g["arrival_t"] / local_level(g["arrival_t"]),
                "r_p": g["price"] / local_level(g["price"])}))
        r = pd.concat(parts).replace([np.inf, -np.inf], np.nan).dropna()
        counts = r.groupby("weekday").size().reindex(range(7), fill_value=0)
        med = r.groupby("weekday")[["r_d", "r_p"]].median().reindex(range(7))
        # A weekday with few rows is a reporting gap (Agmarknet rarely has Sunday reports for
        # Bengaluru), not a closed market: it gets the average factor, not zero demand.
        enough = counts >= share * counts.max()
        w_d = med["r_d"].where(enough, np.nan)
        w_p = med["r_p"].where(enough, np.nan)
        w_d = (w_d / w_d.mean()).fillna(1.0)
        w_p = (w_p / w_p.mean()).fillna(1.0)
        rows += [{"dest_id": d.dest_id, "weekday": w, "w_demand": round(float(w_d[w]), 4),
                  "w_price": round(float(w_p[w]), 4)} for w in range(7)]
    return pd.DataFrame(rows)


def festival_window_days(cal: pd.DataFrame, lo: int, hi: int) -> set[pd.Timestamp]:
    days: set[pd.Timestamp] = set()
    for t in cal["date"]:
        days.update(t + pd.Timedelta(days=k) for k in range(lo, hi + 1))
    return days


def baseline_ratio(g: pd.DataFrame, value: str, excluded: set[pd.Timestamp]) -> pd.Series:
    """value / median(same weekday ±1..4 weeks, outside festival windows) for one series."""
    s = g.set_index("date")[value]
    clean = s[~s.index.isin(excluded)]
    lookup = clean.to_dict()
    out = {}
    for t, v in s.items():
        ref = [lookup[t + pd.Timedelta(weeks=k)] for k in range(-BASELINE_WEEKS,
                                                                  BASELINE_WEEKS + 1)
               if k != 0 and (t + pd.Timedelta(weeks=k)) in lookup]
        if len(ref) >= 3:
            b = float(np.median(ref))
            if b > 0:
                out[t] = v / b - 1.0
    return pd.Series(out, dtype=float)


def fit_festivals(cfg: AppConfig, train: pd.DataFrame, cal: pd.DataFrame,
                  seed: int = 0) -> pd.DataFrame:
    """Event study → festival_effects rows (only effects whose bootstrap CI excludes 0)."""
    lo, hi = cfg.default.market.fdi_window
    eps = abs(cfg.default.market.demand_price_elasticity)
    nboot = cfg.default.market.fdi_bootstrap_resamples
    y0, y1 = cfg.default.data.fit_train_years
    excluded = festival_window_days(cal, lo, hi)
    # Per-market daily uplift vs baseline, then the median across a region's markets.
    parts = []
    for (_m, c, reg), g in train.groupby(["market", "commodity_id", "region"]):
        au = baseline_ratio(g, "arrival_t", excluded)
        pu = baseline_ratio(g, "price", excluded)
        parts.append(pd.DataFrame({"date": au.index, "region": reg, "commodity_id": c,
                                   "au": au.values}).merge(
            pd.DataFrame({"date": pu.index, "pu": pu.values}), on="date", how="inner"))
    daily = (pd.concat(parts).groupby(["region", "commodity_id", "date"])[["au", "pu"]]
             .median().reset_index())
    idx = daily.set_index(["region", "commodity_id", "date"])
    all_regions = sorted(train["region"].dropna().unique())
    rng = np.random.default_rng(seed)
    rows = []
    cal_y = cal[(cal["date"].dt.year >= y0) & (cal["date"].dt.year <= y1)]
    for fest, fc in cal_y.groupby("festival"):
        for reg in all_regions:
            occ = [r for r in fc.itertuples() if reg in regions_of(r.regions, all_regions)]
            if not occ:
                continue
            for c in cfg.commodities:
                for tau in range(lo, hi + 1):
                    au_y, pu_y = [], []
                    for r in occ:
                        key = (reg, c.id, r.date + pd.Timedelta(days=tau))
                        if key in idx.index:
                            au_y.append(float(idx.at[key, "au"]))
                            pu_y.append(float(idx.at[key, "pu"]))
                    if len(au_y) < cfg.default.market.fdi_min_years:
                        continue
                    fdi = np.array(au_y) + eps * np.array(pu_y)
                    boots = fdi[rng.integers(0, len(fdi), size=(nboot, len(fdi)))].mean(axis=1)
                    f_lo, f_hi = np.quantile(boots, [0.025, 0.975])
                    if f_lo <= 0 <= f_hi:
                        continue
                    rows.append({"festival": fest, "commodity": c.id, "region": reg,
                                 "tau": tau, "fdi_mean": round(float(fdi.mean()), 4),
                                 "fdi_lo": round(float(f_lo), 4), "fdi_hi": round(float(f_hi), 4),
                                 "pu_mean": round(float(np.mean(pu_y)), 4),
                                 "n_years": len(fdi)})
    return pd.DataFrame(rows, columns=FILES["festival_effects"])


def fit_regime(cfg: AppConfig, train: pd.DataFrame) -> pd.DataFrame:
    mm = cfg.default.data.fit_min_months
    arr = monthly_stl(train, "arrival_t", mm)
    pr = monthly_stl(train, "price", mm)
    if arr.empty or pr.empty:
        return pd.DataFrame([{"regime": r, "r_price": 1.0, "r_supply": 1.0, "r_demand": 1.0}
                             for r in ("glut", "normal", "scarcity")])
    arr["dev"] = arr["log"] - arr["trend"] - arr["seasonal"]
    pr["pdev"] = pr["log"] - pr["trend"] - pr["seasonal"]
    m = arr.merge(pr[["market", "commodity_id", "month_start", "pdev"]],
                  on=["market", "commodity_id", "month_start"])
    q1, q2 = m["dev"].quantile([1 / 3, 2 / 3])
    m["regime"] = np.where(m["dev"] >= q2, "glut", np.where(m["dev"] <= q1, "scarcity", "normal"))
    g = m.groupby("regime")[["dev", "pdev"]].mean()
    return pd.DataFrame([{"regime": r, "r_price": round(float(np.exp(g.at[r, "pdev"])), 4),
                          "r_supply": round(float(np.exp(g.at[r, "dev"])), 4), "r_demand": 1.0}
                         for r in ("glut", "normal", "scarcity")])


def robust_sd(values: list[float]) -> float:
    """sd estimated from the median absolute deviation (insensitive to market-day outliers)."""
    if len(values) < 30:
        return float("nan")
    v = np.asarray(values)
    return float(MAD_TO_SD * np.median(np.abs(v - np.median(v))))


def fit_noise(cfg: AppConfig, train: pd.DataFrame, dest_markets: set[str]) -> pd.DataFrame:
    sub = train[train["market"].isin(dest_markets)]
    rows = []
    for c in cfg.commodities:
        devs_d, devs_p = [], []
        for _, g in sub[sub["commodity_id"] == c.id].groupby("market"):
            g = g.set_index("date")
            for col, acc in (("arrival_t", devs_d), ("price", devs_p)):
                s = g[col][g[col] > 0]
                d = np.log(s / local_level(s)).replace([np.inf, -np.inf], np.nan).dropna()
                acc.extend(d.tolist())
        rows.append({"commodity_id": c.id, "sigma_log_demand": robust_sd(devs_d),
                     "sigma_log_price": robust_sd(devs_p), "n_obs": len(devs_d)})
    df = pd.DataFrame(rows)
    for col in ("sigma_log_demand", "sigma_log_price"):
        df[col] = df[col].fillna(df[col].median()).round(4)
    return df


def fit_base(cfg: AppConfig, train: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """B_P from real prices; B_D = the hub's share of demand (see module docstring)."""
    g = cfg.default.generator
    last = cfg.default.data.fit_train_years[1]
    recent = train[train["date"].dt.year > last - cfg.default.data.fit_price_years]
    wsum = sum(d.demand_weight for d in cfg.network.destinations)
    rows, fallbacks = [], []
    for c in cfg.commodities:
        csub = recent[recent["commodity_id"] == c.id]
        overall = float(csub["price"].median()) if not csub.empty else np.nan
        for d in cfg.network.destinations:
            p = csub.loc[csub["market"].isin(d.agmarknet_markets), "price"]
            if len(p) >= 30:
                price = float(p.median())
            else:
                reg = csub.loc[csub["region"] == d.region, "price"]
                price = float(reg.median()) if len(reg) >= 30 else overall
                fallbacks.append(f"{d.dest_id}/{c.id}: "
                                 f"{'region' if len(reg) >= 30 else 'all-market'} median")
            rows.append({
                "dest_id": d.dest_id, "commodity_id": c.id,
                "base_price_per_kg": round(price * d.price_premium, 2),
                "base_demand_kg": round(g.base_daily_supply_kg[c.id] * g.demand_to_supply
                                        * d.demand_weight / wsum, 1)})
    return pd.DataFrame(rows), fallbacks


# ------------------------------------------------------------------ hold-out test


def holdout_wape(cfg: AppConfig, df: pd.DataFrame, season: pd.DataFrame,
                 weekday: pd.DataFrame, fx: pd.DataFrame, cal: pd.DataFrame) -> dict:
    """WAPE of daily arrivals at destination markets in the hold-out year, full vs no_calendar.

    Both forecasts use the same train-year base level, seasonality and weekday factors; `full`
    adds the fitted festival terms. Festival window = ±7 days around applicable festivals.
    """
    y0, y1 = cfg.default.data.fit_train_years
    hy = cfg.default.data.fit_holdout_year
    s_d = {(r.commodity_id, r.month): r.s_demand for r in season.itertuples()}
    w_d = {(r.dest_id, r.weekday): r.w_demand for r in weekday.itertuples()}
    fxd: dict[tuple[str, str, str], dict[int, float]] = {}
    for r in fx.itertuples():
        fxd.setdefault((r.festival, r.commodity, r.region), {})[int(r.tau)] = r.fdi_mean
    all_regions = sorted(df["region"].dropna().unique())
    cal_h = cal[cal["date"].dt.year.between(hy - 1, hy + 1)]
    out = {"full": {"abs_err": 0.0, "fw_abs_err": 0.0}, "no_calendar": {"abs_err": 0.0,
                                                                          "fw_abs_err": 0.0}}
    tot = fw_tot = 0.0
    n = n_fw = 0
    for d in cfg.network.destinations:
        if not d.agmarknet_markets:
            continue
        sub = df[df["market"].isin(d.agmarknet_markets)]
        tr = years(sub, y0, y1)
        ho = years(sub, hy, hy)
        for c in cfg.commodities:
            trc, hoc = tr[tr["commodity_id"] == c.id], ho[ho["commodity_id"] == c.id]
            if len(trc) < 60 or hoc.empty:
                continue
            daily_tr = trc.groupby("date")["arrival_t"].sum()
            # base level de-seasonalised: median of arrivals / S(month)
            base = float(np.median(daily_tr.values / np.array(
                [s_d.get((c.id, t.month), 1.0) for t in daily_tr.index])))
            for t, a in hoc.groupby("date")["arrival_t"].sum().items():
                f_nc = base * s_d.get((c.id, t.month), 1.0) * w_d.get((d.dest_id, t.weekday()),
                                                                      1.0)
                mult, in_fw = 1.0, False
                for r in cal_h.itertuples():
                    if d.region not in regions_of(r.regions, all_regions):
                        continue
                    tau = (t - r.date).days
                    if abs(tau) <= 7:
                        in_fw = True
                    mult *= 1.0 + fxd.get((r.festival, c.id, d.region), {}).get(tau, 0.0)
                f_full = f_nc * mult
                out["full"]["abs_err"] += abs(f_full - a)
                out["no_calendar"]["abs_err"] += abs(f_nc - a)
                tot += a
                n += 1
                if in_fw:
                    out["full"]["fw_abs_err"] += abs(f_full - a)
                    out["no_calendar"]["fw_abs_err"] += abs(f_nc - a)
                    fw_tot += a
                    n_fw += 1
    res = {}
    for mode, e in out.items():
        res[mode] = {"WAPE_overall": round(e["abs_err"] / tot, 4) if tot else None,
                     "WAPE_festival_window": round(e["fw_abs_err"] / fw_tot, 4)
                     if fw_tot else None}
    res["n_days"], res["n_festival_window_days"], res["holdout_year"] = n, n_fw, hy
    return res


# ------------------------------------------------------------------ main


def main() -> int:
    cfg = load_config()
    data = cfg.default.data
    df = load_raw(cfg)
    cal = load_calendar()
    y0, y1 = data.fit_train_years
    train = years(df, y0, y1)
    print(f"loaded {len(df):,} market-days ({df['date'].min():%Y-%m-%d} … "
          f"{df['date'].max():%Y-%m-%d}); train {y0}–{y1}: {len(train):,}")
    coverage = (train.groupby("commodity_id").size().reindex([c.id for c in cfg.commodities],
                                                              fill_value=0))
    dest_regions = {d.region for d in cfg.network.destinations}
    dest_markets = {m for d in cfg.network.destinations for m in d.agmarknet_markets}
    present = set(df["market"].unique())
    missing_markets = sorted(dest_markets - present)

    season, season_missing = fit_seasonality(cfg, train, dest_regions, data.hub_district)
    weekday = fit_weekday(cfg, train)
    fx = fit_festivals(cfg, train, cal)
    regime = fit_regime(cfg, train)
    noise = fit_noise(cfg, train, dest_markets)
    base, fallbacks = fit_base(cfg, train)
    wape = holdout_wape(cfg, df, season, weekday, fx, cal)

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    outputs = {"market_base": base, "weekday": weekday, "seasonality": season,
               "festival_effects": fx, "regime": regime,
               "noise": noise[FILES["noise"]]}
    for name, frame in outputs.items():
        frame[FILES[name]].to_csv(PROCESSED_DIR / f"{name}.csv", index=False,
                                  lineterminator="\n")
    report = {
        "fitted_on": date.today().isoformat(),
        "raw_rows": int(len(df)), "date_range": [f"{df['date'].min():%Y-%m-%d}",
                                                 f"{df['date'].max():%Y-%m-%d}"],
        "train_years": [y0, y1], "rows_per_commodity": coverage.to_dict(),
        "destination_markets_missing": missing_markets,
        "base_price_fallbacks": fallbacks,
        "seasonality_missing": season_missing,
        "festival_effects_kept": int(len(fx)),
        "festival_effects_by_festival": fx.groupby("festival").size().to_dict() if len(fx)
        else {},
        "regime": regime.to_dict(orient="records"),
        "noise": noise.to_dict(orient="records"),
        "holdout_wape": wape,
    }
    REPORT.write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("rows_per_commodity", "destination_markets_missing",
                                             "base_price_fallbacks", "seasonality_missing",
                                             "festival_effects_kept", "holdout_wape")},
                     indent=1, default=str))
    print(f"wrote {', '.join(outputs)} and {REPORT.name} to {PROCESSED_DIR}")
    return 0


if __name__ == "__main__":
    warnings.filterwarnings("ignore", category=RuntimeWarning)
    sys.exit(main())
