"""Explanations: one Reason per batch, with the runner-up option and the numbers behind it.

Per-kg value of an option = revenue/kg (forecast price × m(arrival quality) − handling) −
freight/kg (full-truck cost of the cheapest vehicle of that kind). The runner-up is the best
option at a *different* destination, or holding (V_hold) if that is better. The ₹ delta is
(chosen − runner-up) per kg × kg.
"""

from __future__ import annotations

from freshroute.discount import DiscountDecision
from freshroute.feasibility import Option, forecast_lookup
from freshroute.milp import freight_per_kg
from freshroute.schemas import Driver, Reason, State


def _kg(x: float) -> str:
    return f"{x:,.0f} kg" if x >= 10 else f"{x:.1f} kg"


def _net(state: State, o: Option, freight: dict[tuple[str, bool], float]) -> float:
    return o.revenue_per_kg - freight[(o.dest_id, o.group is not None)]


def explain(state: State, options: list[Option], shipped: dict[tuple[str, str, str], float],
            held: dict[str, float], hold_value: dict[str, float],
            discounts: dict[tuple[str, str, str], DiscountDecision],
            vehicle_types: dict[str, str]) -> dict[str, Reason]:
    dests = {d.dest_id: d.name for d in state.destinations}
    comms = {c.id: c.name.lower() for c in state.commodities}
    fc = forecast_lookup(state)
    freight = {(ln.dest_id, r): freight_per_kg(state, ln.dest_id, r)
               for ln in state.lanes for r in (False, True)}
    by_batch: dict[str, list[Option]] = {}
    for o in options:
        by_batch.setdefault(o.batch_id, []).append(o)
    opt_index = {(o.batch_id, o.dest_id, o.vehicle_id): o for o in options}
    reasons: dict[str, Reason] = {}

    for b in state.batches:
        name = f"{b.batch_id} {comms[b.commodity_id]}"
        opts = by_batch.get(b.batch_id, [])
        mine = [(k, kg) for k, kg in shipped.items() if k[0] == b.batch_id]
        vh = hold_value.get(b.batch_id)
        if mine:
            key, kg = max(mine, key=lambda t: t[1])
            o = opt_index[key]
            chosen = _net(state, o, freight)
            others = [x for x in opts if x.dest_id != o.dest_id]
            alt = max(others, key=lambda x: _net(state, x, freight), default=None)
            alt_val = _net(state, alt, freight) if alt else None
            alt_txt = dests[alt.dest_id] if alt else None
            if vh is not None and (alt_val is None or vh > alt_val):
                alt_val, alt_txt = vh, "holding at the hub"
            delta = (chosen - alt_val) * kg if alt_val is not None else 0.0
            vtype = vehicle_types.get(o.vehicle_id, "")
            grp = f", {o.group}" if o.group else ""
            dec = discounts.get(key)
            disc = f", {dec.pct:.0f}% discount" if dec and dec.pct > 0 else ""
            fdi = fc.get((o.dest_id, o.commodity_id, o.arrival_date), (0, 0, 0, 0.0))[3]
            text = (f"{name}, {_kg(kg)} → {dests[o.dest_id]} on {o.vehicle_id} "
                    f"({vtype}{grp}){disc}. Arrives at {o.q_mean:.0%} freshness "
                    f"(≥{max(o.q_p10, 0):.0%} at 90% confidence).")
            if alt_txt == "holding at the hub":
                runner = f"+₹{delta:,.0f} vs holding at the hub (hold value ₹{vh:.1f}/kg)."
            elif alt is not None:
                price_gap = o.price - alt.price
                freight_gap = (freight[(o.dest_id, o.group is not None)]
                               - freight[(alt.dest_id, alt.group is not None)])
                runner = (f"+₹{delta:,.0f} vs {alt_txt}: ₹{price_gap:.1f}/kg price gap, "
                          f"₹{freight_gap:.2f}/kg freight gap.")
            else:
                runner = "No other destination is feasible for this batch today."
            drivers = [
                Driver(name="price", value=round(o.price, 2), unit="₹/kg",
                       text=f"forecast price ₹{o.price:.1f}/kg on {o.arrival_date:%d %b}"),
                Driver(name="freight", value=round(freight[(o.dest_id, o.group is not None)], 2),
                       unit="₹/kg", text="full-truck freight to this destination"),
                Driver(name="arrival_quality", value=round(o.q_mean, 3), unit="fraction",
                       text=f"arrival freshness {o.q_mean:.0%} (p10 {max(o.q_p10, 0):.0%})"),
            ]
            if fdi:
                drivers.append(Driver(name="festival_uplift", value=round(fdi, 3), unit="fraction",
                                      text=f"festival demand uplift {fdi:+.0%} in the forecast"))
            if dec:
                drivers.append(Driver(name="discount_ev", value=round(dec.ev_by_pct[dec.pct], 0),
                                      unit="₹", text="; ".join(dec.triggers) or "no trigger"))
            reasons[b.batch_id] = Reason(decision_text=text, runner_up_text=runner,
                                         delta_value_inr=round(delta, 0), drivers=drivers)
        else:
            best = max(opts, key=lambda x: _net(state, x, freight), default=None)
            if best is None:
                text = f"{name}: hold — no feasible destination today (shelf life / quality)."
                runner, delta = "Nothing else is feasible.", 0.0
            else:
                ship_val = _net(state, best, freight)
                delta = ((vh or 0.0) - ship_val) * held.get(b.batch_id, b.qty_kg)
                text = (f"{name}: hold {_kg(held.get(b.batch_id, b.qty_kg))} "
                        f"(hold value ₹{(vh or 0):.1f}/kg).")
                runner = (f"Shipping to {dests[best.dest_id]} would earn ₹{ship_val:.1f}/kg "
                          f"before demand limits; trucks and demand went to better uses.")
            reasons[b.batch_id] = Reason(
                decision_text=text, runner_up_text=runner, delta_value_inr=round(delta, 0),
                drivers=[Driver(name="hold_value", value=round(vh or 0.0, 2), unit="₹/kg",
                                text="best value of shipping on a later day, net of storage")])
    return reasons
