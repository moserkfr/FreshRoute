"""Load and validate the YAML configuration in ``config/``.

Every tunable number lives in YAML ; this module only gives it types and checks.
Models use ``extra="forbid"`` so a typo in a YAML key is an error, not a silently ignored value.
Validation failures are re-raised as :class:`ConfigError` with ``file → path: message`` lines.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = REPO_ROOT / "config"


class ConfigError(Exception):
    """Raised when a config file is missing, malformed or inconsistent."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=False)


Sources = dict[str, str]

# ---------------------------------------------------------------- default.yaml


class SeedsCfg(_Strict):
    tuning: tuple[int, int]
    validation: tuple[int, int]
    hidden_min: int
    sources: Sources = {}

    @model_validator(mode="after")
    def _disjoint(self) -> SeedsCfg:
        for name, (lo, hi) in (("tuning", self.tuning), ("validation", self.validation)):
            if lo > hi:
                raise ValueError(f"{name} range is reversed: {lo} > {hi}")
            if hi >= self.hidden_min:
                raise ValueError(
                    f"{name} range reaches the locked hidden set (>= {self.hidden_min})"
                )
        if not (self.tuning[1] < self.validation[0] or self.validation[1] < self.tuning[0]):
            raise ValueError("tuning and validation seed ranges overlap")
        return self


class SimulationCfg(_Strict):
    days: int = Field(gt=0)
    dispatch_hour: int = Field(ge=0, le=23)
    load_hours: float = Field(ge=0)
    unload_hours: float = Field(ge=0)
    ambient_temp_c: float
    replan: bool = False
    drop_violations: Literal["all", "hard"] = "all"
    settle_extra_days: int = Field(default=3, ge=0)
    sources: Sources = {}


Grade = Literal["A", "B", "C"]


class GeneratorCfg(_Strict):
    base_daily_supply_kg: dict[str, float]
    supply_noise_sd: float = Field(ge=0)
    qty_median_kg: float = Field(gt=0)
    qty_sigma: float = Field(ge=0)
    qty_min_kg: float = Field(gt=0)
    qty_max_kg: float = Field(gt=0)
    grade_probs: dict[Grade, float]
    grade_life_mult: dict[Grade, float]
    grade_sd_frac: dict[Grade, float]
    cost_basis_frac: tuple[float, float]
    ambient_noise_sd_c: float = Field(ge=0)
    demand_to_supply: float = Field(gt=0)
    placeholder_sigma_log_demand: float = Field(ge=0)
    placeholder_sigma_log_price: float = Field(ge=0)
    sources: Sources = {}

    @model_validator(mode="after")
    def _checks(self) -> GeneratorCfg:
        if abs(sum(self.grade_probs.values()) - 1.0) > 1e-6:
            raise ValueError("grade_probs must sum to 1")
        if self.qty_min_kg > self.qty_max_kg:
            raise ValueError("qty_min_kg > qty_max_kg")
        return self


class PriceBandsCfg(_Strict):
    """Quality → price multiplier bands m(q)."""

    full_q: float = Field(gt=0, le=1)
    mid_q: float = Field(gt=0, le=1)
    mid_low_mult: float = Field(ge=0, le=1)
    clearance_q: float = Field(ge=0, le=1)
    clearance_mult: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def _ordered(self) -> PriceBandsCfg:
        if not self.clearance_q < self.mid_q < self.full_q:
            raise ValueError("need clearance_q < mid_q < full_q")
        return self


class ShelfLifeCfg(_Strict):
    q10_default: float = Field(gt=1)
    chill_penalty: float = Field(ge=1)
    price_bands: PriceBandsCfg
    sources: Sources = {}


class TravelCfg(_Strict):
    speed_kmph: float = Field(gt=0)
    road_factor: float = Field(ge=1)
    delay_prob: float = Field(ge=0, le=1)
    delay_mean_h_per_100km: float = Field(ge=0)
    delay_sd_h_per_100km: float = Field(ge=0)
    sources: Sources = {}


class CostCfg(_Strict):
    disposal_per_kg: float = Field(ge=0)
    writeoff_per_kg: float = Field(default=0.0, ge=0)
    unmet_demand_penalty_per_kg: float = Field(ge=0)
    discount_ladder_pct: list[float]
    discount_elasticity: float = Field(ge=0)
    discount_uplift_cap: float = Field(ge=0)
    hub_storage_temp_c: float
    sources: Sources = {}

    @model_validator(mode="after")
    def _ladder(self) -> CostCfg:
        ladder = self.discount_ladder_pct
        if not ladder or ladder[0] != 0 or ladder != sorted(ladder) or ladder[-1] >= 100:
            raise ValueError("discount_ladder_pct must start at 0, be ascending and stay < 100")
        return self


ForecastMode = Literal["full", "no_calendar", "flat"]


class MarketCfg(_Strict):
    demand_price_elasticity: float = Field(lt=0)
    forecast_mode: ForecastMode
    horizon_days: int = Field(ge=1)
    fdi_window: tuple[int, int]
    fdi_bootstrap_resamples: int = Field(ge=100)
    fdi_min_years: int = Field(ge=2)
    sources: Sources = {}


class PlannerCfg(_Strict):
    risk_p: float = Field(gt=0.5, lt=1.0)
    solver: Literal["SCIP", "CBC", "CP-SAT", "HIGHS"]
    time_limit_s: float = Field(gt=0)
    mip_gap: float = Field(ge=0, lt=1)
    lp_fix_threshold: float = Field(ge=0, lt=1)  # truck keeps destinations with LP y above
    split_batches: bool
    fairness_enabled: bool
    fairness_phi: float = Field(ge=0, le=1)
    festival_guard_fdi: float = Field(ge=0)
    festival_guard_days: int = Field(ge=0)
    safety_margin_days: float = Field(ge=0)       # slack inside the validator's life rules
    demand_breakpoints: list[float] = Field(min_length=2)  # × forecast demand: sell-prob segments
    min_alloc_kg: float = Field(gt=0)             # smaller MILP shares are dropped
    clearance_quality: float = Field(ge=0, le=1)  # held stock below this tomorrow → clear
    clearance_discount_pct: float = Field(ge=0, lt=100)
    b3_discount_quality: float = Field(ge=0, le=1)  # B3: discount if arrival quality below this
    b3_discount_pct: float = Field(ge=0, lt=100)
    sources: Sources = {}


class EvaluationCfg(_Strict):
    bootstrap_resamples: int = Field(ge=100)
    ci_level: float = Field(gt=0.5, lt=1.0)
    baseline: str
    sources: Sources = {}


class AgmarknetSourceCfg(_Strict):
    name: str
    url: str
    size_bytes: int = Field(gt=0)


class DataCfg(_Strict):
    """Where real market data comes from and which slice of it we keep."""

    agmarknet_sources: list[AgmarknetSourceCfg] = Field(min_length=1)
    agmarknet_states: list[str] = Field(min_length=1)
    agmarknet_keywords: list[str] = Field(min_length=1)
    osrm_url: str
    region_of_state: dict[str, str]
    hub_district: str                         # Agmarknet district of the hub (supply seasonality)
    fit_train_years: tuple[int, int]
    fit_min_months: int = Field(ge=24)        # monthly points a market series needs for STL
    fit_price_years: int = Field(ge=1)        # base price = median of the last N train years
    fit_weekday_closed_share: float = Field(gt=0, lt=1)  # weekday with < share of rows → closed
    fit_holdout_year: int
    sources: Sources = {}


class FestivalCfg(_Strict):
    """One festival: how to find it in kalagana and which regions observe it."""

    id: str
    tradition: str                      # kalagana rule set that contains the festival
    match: str                          # kalagana festival name (exact)
    rule: Literal["on", "friday_before"] = "on"   # friday_before: last Friday before `match`
    regions: list[str] = Field(min_length=1)       # region codes, or ["all"]
    type: Literal["hindu", "islamic", "christian", "harvest"]


class CalendarCfg(_Strict):
    location: str
    years: tuple[int, int]
    festivals: list[FestivalCfg] = Field(min_length=1)
    sources: Sources = {}


class DefaultCfg(_Strict):
    seeds: SeedsCfg
    simulation: SimulationCfg
    generator: GeneratorCfg
    shelf_life: ShelfLifeCfg
    travel: TravelCfg
    cost: CostCfg
    market: MarketCfg
    planner: PlannerCfg
    evaluation: EvaluationCfg
    data: DataCfg
    calendar: CalendarCfg


# ------------------------------------------------------------ commodities.yaml


class CommodityCfg(_Strict):
    id: str
    name: str
    t_ref_c: float
    shelf_life_ref_days: float = Field(gt=0)
    q10: float = Field(gt=1)
    chill_threshold_c: float | None
    temp_group: Literal["chilled", "cool"]
    handling_cost_per_kg: float = Field(ge=0)
    storage_cost_per_kg_day: float = Field(ge=0)
    min_sellable_quality: float = Field(ge=0, le=1)
    harvest_age_max_days: float = Field(ge=0)
    agmarknet_names: list[str] = []   # Agmarknet commodity names (exact) pooled for fitting
    source: str = Field(min_length=3)


class CommoditiesFile(_Strict):
    commodities: list[CommodityCfg] = Field(min_length=1)


# ---------------------------------------------------------------- network.yaml


class HubCfg(_Strict):
    id: str
    name: str
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    region: str
    source: str


class DestinationCfg(_Strict):
    dest_id: str
    name: str
    type: Literal["mandi", "retail_dc", "processor"]
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    region: str
    receive_open_h: int = Field(ge=0, le=24)
    receive_close_h: int = Field(ge=0, le=24)
    handling_cost_per_kg: float = Field(ge=0)
    min_quality_accepted: float = Field(ge=0, le=1)
    demand_weight: float = Field(gt=0)
    agmarknet_markets: list[str] = []   # Agmarknet market names whose prices this destination sees
    price_premium: float = Field(default=1.0, gt=0)  # × market price (retail DC, processor)

    @model_validator(mode="after")
    def _window(self) -> DestinationCfg:
        if self.receive_open_h >= self.receive_close_h:
            raise ValueError(
                f"receive window [{self.receive_open_h}, {self.receive_close_h}) is empty"
            )
        return self


class NetworkFile(_Strict):
    hub: HubCfg
    destinations: list[DestinationCfg] = Field(min_length=1)
    sources: Sources = {}


# ------------------------------------------------------------------ fleet.yaml


class VehicleClassCfg(_Strict):
    class_id: str
    type: Literal["ambient", "reefer"]
    capacity_kg: float = Field(gt=0)
    cost_per_km: float = Field(ge=0)
    fixed_cost_per_trip: float = Field(ge=0)


class FleetEntryCfg(_Strict):
    vehicle_id: str
    class_id: str


class FleetFile(_Strict):
    reefer_setpoints_c: dict[Literal["chilled", "cool"], float]
    classes: list[VehicleClassCfg] = Field(min_length=1)
    default_fleet: list[FleetEntryCfg] = Field(min_length=1)
    sources: Sources = {}


# -------------------------------------------------------------- scenarios.yaml


class ScenarioOverrides(_Strict):
    """Run-wide generator changes. Multipliers default to 1 (no change)."""

    supply_mult: float = Field(default=1.0, gt=0)
    price_mult: float = Field(default=1.0, gt=0)
    regime: Literal["glut", "normal", "scarcity"] | None = None
    ambient_delta_c: float = 0.0
    travel_time_mult: float = Field(default=1.0, gt=0)
    delay_prob: float | None = Field(default=None, ge=0, le=1)
    life_sd_mult: float = Field(default=1.0, gt=0)


EventType = Literal["lane_closure", "vehicle_down", "demand_spike", "price_shock", "new_batches"]


class ScenarioEvent(_Strict):
    type: EventType
    day: int = Field(ge=0)
    announced: bool = False
    params: dict[str, Any] = {}


class ScenarioCfg(_Strict):
    description: str
    start_date: str | None = None
    days: int | None = None
    overrides: ScenarioOverrides = ScenarioOverrides()
    events: list[ScenarioEvent] = []


class ScenarioDefaults(_Strict):
    days: int = Field(gt=0)
    start_date: str


class ScenariosFile(_Strict):
    defaults: ScenarioDefaults
    scenarios: dict[str, ScenarioCfg] = Field(min_length=1)
    sources: Sources = {}

    def resolved(self, scenario_id: str) -> ScenarioCfg:
        """Scenario with defaults filled in (start_date, days)."""
        if scenario_id not in self.scenarios:
            raise ConfigError(f"unknown scenario '{scenario_id}'; known: {sorted(self.scenarios)}")
        sc = self.scenarios[scenario_id].model_copy(deep=True)
        sc.start_date = sc.start_date or self.defaults.start_date
        sc.days = sc.days or self.defaults.days
        return sc


# ------------------------------------------------------------------ AppConfig


class AppConfig(_Strict):
    """Everything in ``config/``, validated and cross-checked."""

    default: DefaultCfg
    commodities: list[CommodityCfg]
    network: NetworkFile
    fleet: FleetFile
    scenarios: ScenariosFile

    # convenience accessors -------------------------------------------------
    def commodity(self, commodity_id: str) -> CommodityCfg:
        for c in self.commodities:
            if c.id == commodity_id:
                return c
        raise KeyError(commodity_id)

    def vehicle_class(self, class_id: str) -> VehicleClassCfg:
        for k in self.fleet.classes:
            if k.class_id == class_id:
                return k
        raise KeyError(class_id)

    def with_overrides(self, overrides: dict[str, Any]) -> AppConfig:
        """Return a re-validated copy with dotted-path overrides applied.

        Example: ``cfg.with_overrides({"default.planner.risk_p": 0.95})``. Used by the API
        settings, the sensitivity (tornado) runs and tests.
        """
        data = self.model_dump()
        for dotted, value in overrides.items():
            node = data
            keys = dotted.split(".")
            for k in keys[:-1]:
                node = node[int(k)] if isinstance(node, list) else node[k]
            last = keys[-1]
            if isinstance(node, list):
                node[int(last)] = value
            else:
                if last not in node:
                    raise ConfigError(f"override path '{dotted}' does not exist")
                node[last] = value
        return _validate_app(data, origin="overrides")

    @model_validator(mode="after")
    def _cross_checks(self) -> AppConfig:
        errors: list[str] = []
        _unique(errors, "commodities.yaml commodity id", [c.id for c in self.commodities])
        dest_ids = [d.dest_id for d in self.network.destinations]
        _unique(errors, "network.yaml dest_id", dest_ids)
        class_ids = [k.class_id for k in self.fleet.classes]
        _unique(errors, "fleet.yaml class_id", class_ids)
        vehicle_ids = [v.vehicle_id for v in self.fleet.default_fleet]
        _unique(errors, "fleet.yaml vehicle_id", vehicle_ids)
        for v in self.fleet.default_fleet:
            if v.class_id not in class_ids:
                errors.append(f"fleet.yaml vehicle {v.vehicle_id}: unknown class '{v.class_id}'")
        groups = {c.temp_group for c in self.commodities}
        if any(k.type == "reefer" for k in self.fleet.classes):
            for g in groups - set(self.fleet.reefer_setpoints_c):
                errors.append(f"fleet.yaml reefer_setpoints_c: missing setpoint for group '{g}'")
        commodity_ids = {c.id for c in self.commodities}
        missing = commodity_ids - set(self.default.generator.base_daily_supply_kg)
        if missing:
            errors.append(f"default.yaml generator.base_daily_supply_kg: missing {sorted(missing)}")
        selectors = set(dest_ids) | {"farthest", "nearest"}
        for sid, sc in self.scenarios.scenarios.items():
            for i, ev in enumerate(sc.events):
                where = f"scenarios.yaml {sid}.events[{i}] ({ev.type})"
                p = ev.params
                if "dest" in p and p["dest"] not in selectors:
                    errors.append(f"{where}: unknown dest '{p['dest']}'")
                if "vehicle_id" in p and p["vehicle_id"] not in vehicle_ids:
                    errors.append(f"{where}: unknown vehicle_id '{p['vehicle_id']}'")
                if "commodity" in p and p["commodity"] not in commodity_ids:
                    errors.append(f"{where}: unknown commodity '{p['commodity']}'")
        if errors:
            raise ValueError("; ".join(errors))
        return self


def _unique(errors: list[str], label: str, values: list[str]) -> None:
    seen: set[str] = set()
    for v in values:
        if v in seen:
            errors.append(f"duplicate {label} '{v}'")
        seen.add(v)


# -------------------------------------------------------------------- loading

_FILES = {
    "default": "default.yaml",
    "commodities": "commodities.yaml",
    "network": "network.yaml",
    "fleet": "fleet.yaml",
    "scenarios": "scenarios.yaml",
}


def _read_yaml(path: Path) -> Any:
    if not path.exists():
        raise ConfigError(f"missing config file: {path}")
    try:
        with path.open(encoding="utf-8") as fh:
            return yaml.safe_load(fh)
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path.name}: YAML syntax error: {exc}") from exc


def _format_validation_error(exc: ValidationError, origin: str) -> str:
    lines = [f"invalid configuration ({origin}):"]
    for err in exc.errors():
        loc = list(err["loc"])
        # Map the top-level key back to the file it came from.
        file = _FILES.get(str(loc[0]), origin) if loc else origin
        path = ".".join(str(p) for p in loc[1:]) if len(loc) > 1 else "(root)"
        got = err.get("input")
        got_txt = f" (got {got!r})" if not isinstance(got, dict | list) else ""
        lines.append(f"  - {file} → {path}: {err['msg']}{got_txt}")
    return "\n".join(lines)


def _validate_app(data: dict[str, Any], origin: str) -> AppConfig:
    try:
        return AppConfig.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(_format_validation_error(exc, origin)) from None


def load_config(config_dir: Path | str | None = None) -> AppConfig:
    """Load and validate all config files from ``config_dir`` (default: repo ``config/``)."""
    cdir = Path(config_dir) if config_dir else CONFIG_DIR
    raw = {key: _read_yaml(cdir / fname) for key, fname in _FILES.items()}
    commodities = raw["commodities"]
    if not isinstance(commodities, dict) or "commodities" not in commodities:
        raise ConfigError("commodities.yaml: expected a top-level 'commodities:' list")
    try:
        CommoditiesFile.model_validate(commodities)
    except ValidationError as exc:
        raise ConfigError(_format_validation_error(exc, "commodities.yaml")) from None
    data = copy.deepcopy(raw)
    data["commodities"] = commodities["commodities"]
    return _validate_app(data, origin=str(cdir))


if __name__ == "__main__":  # python -m freshroute.config → validate and summarise
    import sys

    try:
        cfg = load_config()
    except ConfigError as e:
        print(e, file=sys.stderr)
        sys.exit(1)
    print(
        f"config OK: {len(cfg.commodities)} commodities, "
        f"{len(cfg.network.destinations)} destinations, "
        f"{len(cfg.fleet.default_fleet)} vehicles, {len(cfg.scenarios.scenarios)} scenarios"
    )
