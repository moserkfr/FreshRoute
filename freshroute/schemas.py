"""Domain data model (spec §5).

Units: kg, km, hours, ₹ (INR), °C, days. Timestamps are timezone-aware datetimes in IST.
Shelf life is tracked in *reference-days*: days of life at the commodity's ideal storage
temperature ``t_ref_c`` (see ``shelf_life.py``).

Export JSON Schema for the frontend with ``python -m freshroute.schemas --export``.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

IST = timezone(timedelta(hours=5, minutes=30), name="IST")

TempGroup = Literal["chilled", "cool"]
DestType = Literal["mandi", "retail_dc", "processor"]
VehicleType = Literal["ambient", "reefer"]
Grade = Literal["A", "B", "C"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ------------------------------------------------------------------ static data


class Commodity(_Model):
    id: str
    name: str
    t_ref_c: float = Field(description="ideal storage temperature, °C")
    shelf_life_ref_days: float = Field(gt=0, description="total life at t_ref from harvest, days")
    q10: float = Field(default=2.0, gt=1)
    chill_threshold_c: float | None = Field(description="below this → chilling injury, °C")
    temp_group: TempGroup = Field(description="reefer compatibility group")
    handling_cost_per_kg: float = Field(ge=0, description="₹/kg")
    storage_cost_per_kg_day: float = Field(ge=0, description="₹/kg/day")
    min_sellable_quality: float = Field(default=0.15, ge=0, le=1)
    source: str


class Batch(_Model):
    batch_id: str
    commodity_id: str
    qty_kg: float = Field(gt=0)
    grade: Grade
    harvest_ts: datetime
    location_id: str
    remaining_life_mean_days: float = Field(description="reference-days of life left (mean)")
    remaining_life_sd_days: float = Field(ge=0, description="reference-days, 1 σ")
    cost_basis_per_kg: float = Field(ge=0, description="₹/kg paid to procure")


class Destination(_Model):
    dest_id: str
    name: str
    type: DestType
    lat: float
    lon: float
    region: str
    receive_open_h: int = Field(ge=0, le=24, description="local hour")
    receive_close_h: int = Field(ge=0, le=24, description="local hour (exclusive)")
    handling_cost_per_kg: float = Field(ge=0, description="₹/kg")
    min_quality_accepted: float = Field(ge=0, le=1)


class Lane(_Model):
    origin_id: str
    dest_id: str
    distance_km: float = Field(gt=0)
    travel_time_h_mean: float = Field(gt=0)
    travel_time_h_sd: float = Field(ge=0)
    open: bool = True


class Vehicle(_Model):
    vehicle_id: str
    type: VehicleType
    class_id: str
    capacity_kg: float = Field(gt=0)
    cost_per_km: float = Field(ge=0, description="₹/km, charged on the round trip")
    fixed_cost_per_trip: float = Field(ge=0, description="₹ per dispatch")
    cargo_temp_c: float | None = Field(
        default=None, description="None for ambient (follows ambient temp)"
    )
    available: bool = True


class MarketDay(_Model):
    """Forecast (planner) or truth (simulator) for one destination × commodity × date."""

    dest_id: str
    commodity_id: str
    date: date
    demand_kg_mean: float = Field(ge=0)
    demand_kg_sd: float = Field(ge=0)
    price_per_kg_forecast: float = Field(ge=0, description="₹/kg at full quality")
    price_sd: float = Field(ge=0)
    fdi: float = Field(default=0.0, description="festival demand uplift in force (0 = none)")
    festival: str | None = None


class State(_Model):
    """Everything a planner may see on one morning. Contains expectations only (spec §2.1)."""

    date: date
    ambient_temp_c: float
    batches: list[Batch]
    vehicles: list[Vehicle]
    destinations: list[Destination]
    lanes: list[Lane]
    market_forecast: list[MarketDay]
    commodities: list[Commodity]
    hub_id: str
    config_snapshot: dict[str, Any] = Field(default_factory=dict)


# ------------------------------------------------------------------- plan output


class Driver(_Model):
    """One numeric fact behind a decision; ``text`` is generated from ``value``."""

    name: str
    value: float
    unit: str
    text: str


class Reason(_Model):
    decision_text: str
    runner_up_text: str
    delta_value_inr: float
    drivers: list[Driver] = []


class Allocation(_Model):
    batch_id: str
    dest_id: str
    vehicle_id: str
    qty_kg: float = Field(gt=0)
    discount_pct: float = Field(default=0.0, ge=0, lt=100)
    depart_ts: datetime
    eta_ts: datetime
    arrival_quality_mean: float
    arrival_quality_p10: float
    expected_net_value: float = Field(description="₹, before shared vehicle costs")


class Hold(_Model):
    batch_id: str
    qty_kg: float = Field(gt=0)
    reason: str
    discount_pct: float = Field(default=0.0, ge=0, lt=100)


class SolverMeta(_Model):
    status: str
    objective: float | None = None
    best_bound: float | None = None
    gap: float | None = None
    time_s: float = 0.0
    fallback_used: bool = False
    pruning_ratio: float | None = None


class Violation(_Model):
    type: str
    batch_id: str | None = None
    vehicle_id: str | None = None
    detail: str


class Plan(_Model):
    date: date
    planner_name: str
    allocations: list[Allocation] = []
    holds: list[Hold] = []
    vehicle_assignments: dict[str, str] = Field(
        default_factory=dict, description="vehicle_id → dest_id"
    )
    vehicle_setpoints: dict[str, TempGroup] = Field(
        default_factory=dict, description="reefer vehicle_id → temperature group run today"
    )
    reasons: dict[str, Reason] = Field(default_factory=dict)
    solver_meta: SolverMeta = SolverMeta(status="n/a")
    violations: list[Violation] = []


EXPORTED_MODELS: tuple[type[BaseModel], ...] = (
    Commodity,
    Batch,
    Destination,
    Lane,
    Vehicle,
    MarketDay,
    State,
    Driver,
    Reason,
    Allocation,
    Hold,
    SolverMeta,
    Violation,
    Plan,
)


def export_json_schema(path: Path) -> Path:
    """Write one JSON Schema document with every exported model under ``$defs``."""
    defs: dict[str, Any] = {}
    for model in EXPORTED_MODELS:
        schema = model.model_json_schema(ref_template="#/$defs/{model}")
        defs.update(schema.pop("$defs", {}))
        defs[model.__name__] = schema
    doc = {"$schema": "https://json-schema.org/draft/2020-12/schema", "$defs": defs}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2, default=str), encoding="utf-8")
    return path


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="FreshRoute schemas")
    ap.add_argument("--export", nargs="?", const="web/src/types/freshroute.schema.json")
    args = ap.parse_args()
    if args.export:
        out = export_json_schema(Path(args.export))
        print(f"wrote {out}")
    else:
        ap.print_help()
