"""Road distances hub → destinations from OSRM → data/processed/distance_matrix.csv.

Only the distance is used: truck travel time stays distance / travel.speed_kmph, because
OSRM's durations are for cars on free-flowing roads. The OSRM duration is kept in the file
for reference.

Usage:  python -m freshroute.fetch_osrm
"""

from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

import pandas as pd

from freshroute.config import load_config

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "processed" / "distance_matrix.csv"


def fetch(base_url: str, points: list[tuple[float, float]]) -> dict:
    """OSRM table service: row 0 (the hub) to every point. Coordinates are lon,lat."""
    coords = ";".join(f"{lon:.5f},{lat:.5f}" for lat, lon in points)
    url = (f"{base_url}/table/v1/driving/{coords}?sources=0"
           f"&annotations=distance,duration")
    req = urllib.request.Request(url, headers={"User-Agent": "FreshRoute-fetch/1.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.load(resp)
    if data.get("code") != "Ok":
        raise RuntimeError(f"OSRM error: {data.get('code')} {data.get('message', '')}")
    return data


def main() -> int:
    cfg = load_config()
    hub, dests = cfg.network.hub, cfg.network.destinations
    data = fetch(cfg.default.data.osrm_url,
                 [(hub.lat, hub.lon)] + [(d.lat, d.lon) for d in dests])
    rows = []
    for i, d in enumerate(dests, start=1):
        rows.append({"origin_id": hub.id, "dest_id": d.dest_id,
                     "distance_km": round(data["distances"][0][i] / 1000.0, 1),
                     "osrm_duration_h": round(data["durations"][0][i] / 3600.0, 2),
                     "source": "osrm"})
    df = pd.DataFrame(rows)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT, index=False, lineterminator="\n")
    print(df.to_string(index=False))
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
