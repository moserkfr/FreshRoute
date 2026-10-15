"""A seller's own stock as a planning State.

The seller lists batches (commodity, kg, grade, harvest date, optionally what they paid). The
fleet, lanes and market forecast for the planning date come from the ``normal`` scenario world;
only the batches are replaced. Remaining life follows the generator's rule — the grade's share of
reference life, minus the days since harvest aged at hub temperature — so a seller batch behaves
exactly like a generated one of the same age.

Batches already below the commodity's sellable quality are reported back and left out of the
State: there is nothing to plan for them.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from freshroute.config import AppConfig
from freshroute.logistics import dispatch_ts
from freshroute.schemas import Batch, Grade, State
from freshroute.shelf_life import batch_life, consumption_rate, quality
from freshroute.whatif import _default_cost_basis

MAX_BATCHES = 500
MAX_AGE_DAYS = 120
LABEL = re.compile(r"^[^\x00-\x1f<>]{1,24}$")

StockStatus = Literal["fresh", "sell_today", "spoiled"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StockBatch(_Model):
    commodity_id: str
    qty_kg: float = Field(gt=0, le=100_000)
    grade: Grade = "A"
    harvest_date: date | None = Field(default=None, description="None = harvested this morning")
    cost_per_kg: float | None = Field(default=None, ge=0,
                                      description="₹/kg paid; None = typical cost basis")
    label: str | None = Field(default=None, description="seller's lot name, used as batch id")


class StockRow(_Model):
    """How one listed batch enters the plan."""

    batch_id: str
    commodity_id: str
    qty_kg: float
    grade: Grade
    age_days: int
    remaining_life_mean_days: float = Field(description="reference-days")
    quality: float = Field(description="0–1 at the start of the planning day")
    days_left_at_hub: float = Field(description="real days until unsellable if kept at the hub")
    cost_per_kg: float
    cost_assumed: bool
    status: StockStatus
    planned: bool


class StockError(ValueError):
    """The listed stock cannot be turned into a State."""


def build_state(base: State, stock: list[StockBatch],
                cfg: AppConfig) -> tuple[State, list[StockRow]]:
    """``base`` with its batches replaced by the seller's stock, plus one row per listed batch."""
    if not stock:
        raise StockError("list at least one batch")
    if len(stock) > MAX_BATCHES:
        raise StockError(f"at most {MAX_BATCHES} batches per plan")
    comms = {c.id: c for c in base.commodities}
    hub_temp = cfg.default.cost.hub_storage_temp_c
    rate = {cid: consumption_rate(hub_temp, c, cfg.default.shelf_life.chill_penalty)
            for cid, c in comms.items()}
    morning = dispatch_ts(base.date, cfg)

    problems: list[str] = []
    ids: list[str] = []
    for i, s in enumerate(stock, start=1):
        where = f"row {i}"
        if s.commodity_id not in comms:
            problems.append(f"{where}: unknown commodity '{s.commodity_id}' "
                            f"(known: {', '.join(sorted(comms))})")
        if s.harvest_date and s.harvest_date > base.date:
            problems.append(f"{where}: harvest date {s.harvest_date} is after the planning "
                            f"date {base.date}")
        if s.harvest_date and (base.date - s.harvest_date).days > MAX_AGE_DAYS:
            problems.append(f"{where}: harvested more than {MAX_AGE_DAYS} days ago")
        label = (s.label or "").strip()
        if label and not LABEL.match(label):
            problems.append(f"{where}: lot name must be 1–24 characters without < or >")
        ids.append(label or f"MY-{i:03d}")
    dupes = sorted({x for x in ids if ids.count(x) > 1})
    if dupes:
        problems.append(f"duplicate lot names {dupes}")
    if problems:
        raise StockError("; ".join(problems))

    batches: list[Batch] = []
    rows: list[StockRow] = []
    for bid, s in zip(ids, stock, strict=True):
        c = comms[s.commodity_id]
        age = (base.date - s.harvest_date).days if s.harvest_date else 0
        mean, sd = batch_life(c, s.grade, age, hub_temp, cfg)
        l_min = c.min_sellable_quality * c.shelf_life_ref_days
        left = (mean - l_min) / rate[c.id]
        status: StockStatus = "spoiled" if left <= 0 else "sell_today" if left < 1 else "fresh"
        assumed = s.cost_per_kg is None
        cost = _default_cost_basis(base, c.id, cfg.default.generator.cost_basis_frac) \
            if assumed else float(s.cost_per_kg or 0.0)
        planned = status != "spoiled"
        if planned:
            batches.append(Batch(batch_id=bid, commodity_id=c.id, qty_kg=round(s.qty_kg, 1),
                                 grade=s.grade, harvest_ts=morning - timedelta(days=age),
                                 location_id=base.hub_id,
                                 remaining_life_mean_days=round(mean, 4),
                                 remaining_life_sd_days=round(sd, 4),
                                 cost_basis_per_kg=round(cost, 2)))
        rows.append(StockRow(batch_id=bid, commodity_id=c.id, qty_kg=round(s.qty_kg, 1),
                             grade=s.grade, age_days=age,
                             remaining_life_mean_days=round(mean, 2),
                             quality=round(max(0.0, quality(mean, c)), 3),
                             days_left_at_hub=round(max(left, 0.0), 1),
                             cost_per_kg=round(cost, 2), cost_assumed=assumed,
                             status=status, planned=planned))
    snapshot = {**base.config_snapshot, "source": "seller_stock"}
    return base.model_copy(update={"batches": batches, "config_snapshot": snapshot}), rows
