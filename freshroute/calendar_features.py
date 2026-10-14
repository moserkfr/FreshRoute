"""Festival calendar → data/processed/festival_calendar.csv (festival, date, regions, type).

Dates come from kalagana's rule engine for the configured location. kalagana splits its rules
by tradition (the north set has Ganesh Chaturthi/Navratri/Dussehra/Diwali, the Kannada set has
Sankranti/Ugadi/Onam), so each configured festival names the tradition to read it from.
Varamahalakshmi has no kalagana rule; it is the Friday before Shravana Purnima, which kalagana
does give (Raksha Bandhan).

Rows in config/festivals_override.csv replace computed dates for the same festival and year —
use it for Islamic dates, which kalagana computes from the tabular Hijri calendar and which
can differ from the moon-sighted date by a day or two.

kalagana has no wedding-muhurat calendar, so the optional wedding-day feature is skipped.

Usage:  python -m freshroute.calendar_features
"""

from __future__ import annotations

import sys
import warnings
from concurrent.futures import ProcessPoolExecutor
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from freshroute.config import CONFIG_DIR, AppConfig, FestivalCfg, load_config

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "processed" / "festival_calendar.csv"
OVERRIDE = CONFIG_DIR / "festivals_override.csv"
FRIDAY = 4


def _year_dates(args: tuple[str, int, str]) -> tuple[str, int, dict[str, list[date]]]:
    """All festival dates kalagana gives for one (tradition, year): name → dates."""
    import kalagana

    tradition, year, location = args
    loc = kalagana.city_lookup(location)
    out: dict[str, list[date]] = {}
    for f in kalagana.festival_dates(year, loc, tradition=tradition, include_monthly=False):
        out.setdefault(f.name, []).append(f.date)
    return tradition, year, out


def resolve(fest: FestivalCfg, found: dict[str, list[date]]) -> date | None:
    """Date of ``fest`` in one year, or None if kalagana has no such occurrence."""
    dates = sorted(found.get(fest.match, []))
    if not dates:
        return None
    d = dates[0]
    if fest.rule == "friday_before":
        d -= timedelta(days=(d.weekday() - FRIDAY - 1) % 7 + 1)
    return d


def build(cfg: AppConfig, jobs: int = 4) -> pd.DataFrame:
    cal = cfg.default.calendar
    years = range(cal.years[0], cal.years[1] + 1)
    traditions = sorted({f.tradition for f in cal.festivals})
    tasks = [(t, y, cal.location) for t in traditions for y in years]
    with ProcessPoolExecutor(max_workers=jobs) as ex:
        found = {(t, y): d for t, y, d in ex.map(_year_dates, tasks)}
    rows = []
    for y in years:
        for f in cal.festivals:
            d = resolve(f, found[(f.tradition, y)])
            if d is None:
                warnings.warn(f"kalagana has no '{f.match}' in {y} ({f.tradition})",
                              stacklevel=2)
                continue
            regions = "ALL" if "all" in f.regions else "|".join(f.regions)
            rows.append({"festival": f.id, "date": d.isoformat(), "regions": regions,
                         "type": f.type})
    df = pd.DataFrame(rows)
    return apply_overrides(df, OVERRIDE)


def apply_overrides(df: pd.DataFrame, path: Path) -> pd.DataFrame:
    """Override rows replace the computed date of the same festival in the same year."""
    if not path.exists():
        return df
    ov = pd.read_csv(path, dtype=str)
    if ov.empty:
        return df
    ov_keys = {(r.festival, str(r.date)[:4]) for r in ov.itertuples()}
    keep = [(r.festival, str(r.date)[:4]) not in ov_keys for r in df.itertuples()]
    return (pd.concat([df[keep], ov[df.columns]], ignore_index=True)
            .sort_values(["date", "festival"]).reset_index(drop=True))


def main() -> int:
    cfg = load_config()
    warnings.warn("kalagana exposes no wedding-muhurat calendar; wedding-day feature skipped",
                  stacklevel=1)
    df = build(cfg)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    df.sort_values(["date", "festival"]).to_csv(OUT, index=False, lineterminator="\n")
    print(f"wrote {OUT} ({len(df)} festival dates, {df.date.min()} … {df.date.max()})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
