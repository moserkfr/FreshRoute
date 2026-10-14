"""Download the Agmarknet slice we need (daily arrivals + prices per market).

The source files are multi-GB national CSVs, so they are streamed and filtered row by row:
only the configured states and commodity keywords are kept. Both source layouts (old and new
India Data Portal schemas) are normalised to one set of columns:

  date, state, district, market, commodity, variety, grade,
  arrival_t (tonnes), min_price, max_price, modal_price (₹/quintal), source

A dropped connection resumes from the last byte read (HTTP Range), so a long download does not
have to restart from zero.

Usage:  python -m freshroute.fetch_agmarknet [--out data/raw/agmarknet_south.csv]
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
import urllib.request
from collections.abc import Iterator
from pathlib import Path

from freshroute.config import AgmarknetSourceCfg, load_config

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"
OUT_COLUMNS = ["date", "state", "district", "market", "commodity", "variety", "grade",
               "arrival_t", "min_price", "max_price", "modal_price", "source"]
# Source column → output column, per layout (detected from the header).
LAYOUTS = {
    "old": {"report_date": "date", "state_name": "state", "district_name": "district",
            "market_center": "market", "commodity_name": "commodity", "variety": "variety",
            "origin": "grade", "arrival_quantity": "arrival_t", "min_price": "min_price",
            "max_price": "max_price", "modal_price": "modal_price"},
    "new": {"date": "date", "state_name": "state", "district_name": "district",
            "market_center_name": "market", "commodity_name": "commodity", "variety": "variety",
            "grade": "grade", "arrival_quantity": "arrival_t", "min_price": "min_price",
            "max_price": "max_price", "modal_price": "modal_price"},
}
CHUNK = 1 << 20
MAX_RETRIES = 20


def stream_lines(url: str, size: int) -> Iterator[str]:
    """Yield decoded text lines, resuming with a Range request after a dropped connection."""
    pos, retries, tail = 0, 0, b""
    while pos < size:
        req = urllib.request.Request(url, headers={"User-Agent": "FreshRoute-fetch/1.0",
                                                   "Range": f"bytes={pos}-"})
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                while True:
                    block = resp.read(CHUNK)
                    if not block:
                        break
                    pos += len(block)
                    lines = (tail + block).split(b"\n")
                    tail = lines.pop()
                    for ln in lines:
                        yield ln.decode("utf-8", errors="replace")
            retries = 0
        except OSError as exc:
            retries += 1
            if retries > MAX_RETRIES:
                raise
            print(f"\n  connection dropped at {pos / 1e6:.0f} MB ({exc}); resuming",
                  file=sys.stderr)
            time.sleep(min(60, 2 ** retries))
    if tail:
        yield tail.decode("utf-8", errors="replace")


def filter_source(src: AgmarknetSourceCfg, states: set[str], keywords: list[str],
                  writer: csv.DictWriter) -> int:
    lines = stream_lines(src.url, src.size_bytes)
    header = next(csv.reader([next(lines)]))
    layout = "old" if "report_date" in header else "new"
    cols = LAYOUTS[layout]
    idx = {h: i for i, h in enumerate(header)}
    i_state, i_comm = idx["state_name"], idx["commodity_name"]
    kept, read, t0 = 0, 0, time.perf_counter()
    for row in csv.reader(lines):
        read += 1
        if read % 500_000 == 0:
            print(f"\r  {src.name}: {read / 1e6:.1f} M rows read, {kept:,} kept "
                  f"({time.perf_counter() - t0:.0f} s)", end="", flush=True)
        if len(row) != len(header) or row[i_state] not in states:
            continue
        name = row[i_comm].lower()
        if not any(k in name for k in keywords):
            continue
        out = {dst: row[idx[s]] for s, dst in cols.items()}
        out["source"] = src.name
        writer.writerow(out)
        kept += 1
    print(f"\r  {src.name}: {read:,} rows read, {kept:,} kept "
          f"({time.perf_counter() - t0:.0f} s)        ")
    return kept


def main(argv: list[str] | None = None) -> int:
    cfg = load_config().default.data
    ap = argparse.ArgumentParser(description="Fetch the Agmarknet slice used by fit_market")
    ap.add_argument("--out", type=Path, default=RAW_DIR / "agmarknet_south.csv")
    args = ap.parse_args(argv)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    part = args.out.with_suffix(".part")
    states = set(cfg.agmarknet_states)
    keywords = [k.lower() for k in cfg.agmarknet_keywords]
    total = sum(s.size_bytes for s in cfg.agmarknet_sources)
    print(f"streaming {len(cfg.agmarknet_sources)} file(s), {total / 1e9:.1f} GB; keeping "
          f"{', '.join(sorted(states))} × {', '.join(keywords)}")
    with open(part, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=OUT_COLUMNS)
        writer.writeheader()
        kept = sum(filter_source(s, states, keywords, writer) for s in cfg.agmarknet_sources)
    part.replace(args.out)  # only a complete download replaces the previous file
    print(f"wrote {args.out} ({kept:,} rows)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
