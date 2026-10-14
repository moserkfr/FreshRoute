"""Discount engine: expected value of each ladder step for every planned shipment.

For an allocation of q kg to (destination d, commodity c), its fair share of forecast demand is
D · q / (all kg planned to (d, c)). A discount raises sell-through by the elasticity (capped):

    sell_through(k) = min(1, share / q · (1 + min(cap, e · disc_k)))
    EV(k) = sell_through · q · P · m(q_arr) · (1 − disc_k)
            − (1 − sell_through) · q · (writeoff + disposal)

The engine only looks at discounts when a trigger fires (each recorded as a reason):
  * arrival quality between mid_q and full_q (0.3–0.6) → 10% / 20% candidates
  * planned kg exceed the expected full-price sell-through at d → any ladder step
  * festival guard: forecast festival uplift at d within the next N days above the threshold
    and the batch can still be sold later → no discount (keep it for the festival)
Held stock that will fall below ``planner.clearance_quality`` before the next dispatch gets a
local-clearance recommendation instead.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta

from freshroute.config import AppConfig
from freshroute.feasibility import Option, forecast_lookup
from freshroute.schemas import State
from freshroute.shelf_life import price_multiplier


@dataclass(frozen=True)
class DiscountDecision:
    pct: float
    ev_by_pct: dict[float, float]       # ₹ for this allocation at each considered step
    triggers: list[str]                 # human-readable reasons, with numbers
    guarded: bool = False


def sell_through(share: float, qty: float, pct: float, cfg: AppConfig) -> float:
    c = cfg.default.cost
    uplift = min(c.discount_uplift_cap, c.discount_elasticity * pct / 100.0)
    return min(1.0, share / qty * (1.0 + uplift)) if qty > 0 else 1.0


def expected_value(share: float, qty: float, price: float, mult: float, pct: float,
                   cfg: AppConfig) -> float:
    c = cfg.default.cost
    st = sell_through(share, qty, pct, cfg)
    return (st * qty * price * mult * (1 - pct / 100.0)
            - (1 - st) * qty * (c.writeoff_per_kg + c.disposal_per_kg))


def decide(state: State, cfg: AppConfig, shipments: dict[tuple[str, str, str], float],
           options: dict[tuple[str, str, str], Option], hold_value: dict[str, float]
           ) -> dict[tuple[str, str, str], DiscountDecision]:
    """Discount per shipment key (batch, dest, vehicle)."""
    pc = cfg.default.planner
    bands = cfg.default.shelf_life.price_bands
    ladder = list(cfg.default.cost.discount_ladder_pct)
    disposal = cfg.default.cost.disposal_per_kg
    fc = forecast_lookup(state)
    planned: dict[tuple[str, str], float] = defaultdict(float)
    for (bid, did, _vid), kg in shipments.items():
        planned[(did, options[(bid, did, _vid)].commodity_id)] += kg

    out: dict[tuple[str, str, str], DiscountDecision] = {}
    for key, kg in shipments.items():
        o = options[key]
        dem = fc[(o.dest_id, o.commodity_id, o.arrival_date)][0]
        total = planned[(o.dest_id, o.commodity_id)]
        share = dem * kg / total if total > 0 else 0.0
        mult = price_multiplier(o.q_mean, bands)
        triggers: list[str] = []
        candidates = {0.0}
        if bands.mid_q <= o.q_mean < bands.full_q:
            candidates |= {float(p) for p in ladder if 0 < p <= 20}
            triggers.append(f"arrives at {o.q_mean:.0%} freshness (mid band "
                            f"{bands.mid_q:.0%}–{bands.full_q:.0%})")
        if total > dem * 1.0001:
            candidates |= {float(p) for p in ladder}
            triggers.append(f"{total:,.0f} kg planned to {o.dest_id} vs {dem:,.0f} kg forecast "
                            f"demand")
        guarded = False
        if len(candidates) > 1:
            horizon = [state.date + timedelta(days=k) for k in range(pc.festival_guard_days + 1)]
            fdi = max((fc.get((o.dest_id, o.commodity_id, d), (0, 0, 0, 0.0))[3]
                       for d in horizon), default=0.0)
            can_wait = hold_value.get(o.batch_id, -disposal) > -disposal
            if fdi > pc.festival_guard_fdi and can_wait:
                guarded = True
                candidates = {0.0}
                triggers.append(f"festival guard: +{fdi:.0%} demand expected within "
                                f"{pc.festival_guard_days} days, batch can wait")
        evs = {p: expected_value(share, kg, o.price, mult, p, cfg) for p in sorted(candidates)}
        best = max(evs, key=lambda p: (round(evs[p], 6), -p))
        out[key] = DiscountDecision(pct=best, ev_by_pct=evs, triggers=triggers, guarded=guarded)
    return out


def clearance_needed(state: State, cfg: AppConfig, batch_id: str) -> bool:
    """Held batch that will fall below the clearance quality before tomorrow's dispatch."""
    from freshroute.milp import hub_temperature
    from freshroute.shelf_life import consumption_rate, quality

    b = next(x for x in state.batches if x.batch_id == batch_id)
    c = next(x for x in state.commodities if x.id == b.commodity_id)
    rate = consumption_rate(hub_temperature(state, cfg), c, cfg.default.shelf_life.chill_penalty)
    return quality(b.remaining_life_mean_days - rate, c) < cfg.default.planner.clearance_quality
