"""FreshRoute HTTP API (FastAPI). Run: ``python tasks.py api`` → http://localhost:8000/docs

Endpoints
  GET  /health          liveness + whether placeholder market data is in use
  GET  /scenarios       scenarios and seed sets
  POST /instance        generate (or reuse) an instance → day-0 State
  POST /plan            State + planner → Plan, independent validator findings, plan summary
  POST /whatif          State + event → new State, new Plan, diff vs the previous plan
  POST /simulate        run planners over a scenario × seed → RunResults + metrics
  GET  /eval/latest     latest evaluation report (reports/final/ first, then reports/)
  GET  /market/pulse    price vs seasonal norm, momentum, festival radar, regime flag

Rules carried over from the evaluation: seeds ≥ ``seeds.hidden_min`` are refused (the hidden
set stays locked until the final run), every plan goes through the same validator as in the
simulator, and a planner failure on a valid State falls back to the FEFO heuristic (flagged in
``solver_meta``) instead of an error.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import date
from functools import cache, lru_cache
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field

from freshroute import __version__
from freshroute.baselines import PLANNERS, FefoGreedy, get_planner
from freshroute.config import AppConfig, ConfigError, load_config
from freshroute.eval_runner import REPORTS_DIR, festival_dates
from freshroute.generator import Instance, generate
from freshroute.market_model import MarketParams, load_market_params
from freshroute.metrics import run_metrics
from freshroute.pulse import PulseResponse, market_pulse
from freshroute.schemas import Plan, RunResult, SolverMeta, State
from freshroute.simulator import initial_state, run
from freshroute.validator import validate
from freshroute.whatif import (
    PlanDiff,
    PlanSummary,
    WhatIfError,
    WhatIfEvent,
    apply_event,
    diff_plans,
    summarise,
)

log = logging.getLogger(__name__)

PlannerName = Literal["freshroute", "ours_no_calendar", "fefo_greedy", "chase_price",
                      "fifo_nearest"]
ForecastMode = Literal["full", "no_calendar", "flat"]
RiskP = Annotated[float | None, Field(default=None, gt=0.5, lt=1.0,
                                      description="chance-constraint probability; None = config")]


# ------------------------------------------------------------------ shared resources


@cache
def get_cfg() -> AppConfig:
    return load_config()


@cache
def get_market() -> MarketParams:
    return load_market_params()


@cache
def _instance_cache():
    @lru_cache(maxsize=get_cfg().default.api.instance_cache_size)
    def build(scenario_id: str, seed: int, start: str | None, days: int | None) -> Instance:
        return generate(scenario_id, seed, start_date=start, days=days, cfg=get_cfg(),
                        market_params=get_market())
    return build


def get_instance(scenario_id: str, seed: int, start: date | None = None,
                 days: int | None = None) -> Instance:
    cfg = get_cfg()
    if seed >= cfg.default.seeds.hidden_min:
        raise HTTPException(403, f"seeds ≥ {cfg.default.seeds.hidden_min} are the locked hidden "
                                 f"set; use tuning or validation seeds")
    try:
        cfg.scenarios.resolved(scenario_id)
    except ConfigError as exc:
        raise HTTPException(404, str(exc)) from exc
    return _instance_cache()(scenario_id, seed, start.isoformat() if start else None, days)


# ------------------------------------------------------------------ models


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Health(_Model):
    status: Literal["ok"]
    version: str
    planners: list[str]
    placeholder_data: list[str] = Field(description="market files still synthetic (empty = fitted)")


class ScenarioInfo(_Model):
    id: str
    description: str
    start_date: date
    days: int
    events: list[dict[str, Any]]


class SeedSets(_Model):
    tuning: tuple[int, int]
    validation: tuple[int, int]
    hidden_min: int = Field(description="seeds ≥ this are locked (refused by the API)")


class ScenariosResponse(_Model):
    scenarios: list[ScenarioInfo]
    seeds: SeedSets


class InstanceRequest(_Model):
    scenario_id: str = "normal"
    seed: int = Field(default=51, ge=0)
    start_date: date | None = Field(default=None, description="None = the scenario's start")
    forecast_mode: ForecastMode = "full"
    risk_p: RiskP = None


class InstanceResponse(_Model):
    scenario_id: str
    seed: int
    start_date: date
    days: int
    fingerprint: str = Field(description="sha256 of every generated value (reproducibility)")
    announced_events: list[dict[str, Any]] = Field(
        description="events the planner may know about; unannounced ones stay hidden")
    placeholder_data: list[str]
    state: State


class PlanRequest(_Model):
    state: State
    planner: PlannerName = "freshroute"
    risk_p: RiskP = None


class PlanResponse(_Model):
    plan: Plan
    summary: PlanSummary


class WhatIfRequest(_Model):
    state: State
    event: WhatIfEvent
    planner: PlannerName = "freshroute"
    risk_p: RiskP = None
    previous_plan: Plan | None = Field(
        default=None, description="plan to diff against; None = plan the original State first")


class WhatIfResponse(_Model):
    state: State
    plan: Plan
    previous_plan: Plan
    diff: PlanDiff


class SimulateRequest(_Model):
    scenario_id: str = "normal"
    seed: int = Field(default=51, ge=0)
    planners: list[PlannerName] = Field(default=["freshroute", "fifo_nearest"], min_length=1)
    days: int | None = Field(default=None, ge=1, description="None = the scenario's length")
    replan: bool = False
    risk_p: RiskP = None


class SimRun(_Model):
    planner: str
    metrics: dict[str, Any]
    result: RunResult
    wall_s: float


class SimulateResponse(_Model):
    scenario_id: str
    seed: int
    days: int
    fingerprint: str
    runs: list[SimRun]


# ------------------------------------------------------------------ helpers


def check_state(state: State) -> None:
    """Reject States whose ids do not line up (planners assume they do)."""
    comms = {c.id for c in state.commodities}
    dests = {d.dest_id for d in state.destinations}
    problems = []
    if len({b.batch_id for b in state.batches}) != len(state.batches):
        problems.append("duplicate batch_id")
    if len({v.vehicle_id for v in state.vehicles}) != len(state.vehicles):
        problems.append("duplicate vehicle_id")
    if bad := sorted({b.commodity_id for b in state.batches} - comms):
        problems.append(f"batches reference unknown commodities {bad}")
    if missing := sorted(dests - {ln.dest_id for ln in state.lanes}):
        problems.append(f"no lane to destinations {missing}")
    if bad := sorted({m.dest_id for m in state.market_forecast} - dests):
        problems.append(f"forecast references unknown destinations {bad}")
    have = {(m.dest_id, m.commodity_id) for m in state.market_forecast if m.date == state.date}
    if missing := sorted({(d, c) for d in dests for c in comms} - have):
        problems.append(f"no forecast for {state.date} at {missing[:3]}"
                        + (" …" if len(missing) > 3 else ""))
    if problems:
        raise HTTPException(422, "invalid state: " + "; ".join(problems))


def with_risk(state: State, risk_p: float | None) -> State:
    if risk_p is None:
        return state
    return state.model_copy(update={"config_snapshot": {**state.config_snapshot,
                                                        "risk_p": risk_p}})


def make_plan(state: State, planner: str) -> Plan:
    """Plan, then attach the independent validator's findings. Never raises on a valid State."""
    cfg = get_cfg()
    t0 = time.perf_counter()
    try:
        plan = get_planner(planner).plan(state, cfg)
    except Exception as exc:  # graceful degradation: heuristic plan, flagged
        log.exception("planner %s failed; falling back to fefo_greedy", planner)
        plan = FefoGreedy().plan(state, cfg)
        plan = plan.model_copy(update={"planner_name": planner, "solver_meta": SolverMeta(
            status=f"fallback (error: {exc})", time_s=time.perf_counter() - t0,
            fallback_used=True)})
    return plan.model_copy(update={"violations": validate(plan, state, cfg)})


# ------------------------------------------------------------------ app

app = FastAPI(
    title="FreshRoute API",
    version=__version__,
    description="Shelf-life-aware, explainable distribution planning for perishable produce. "
                "Units: kg, km, hours, ₹, °C, days.",
)
app.add_middleware(CORSMiddleware, allow_origins=get_cfg().default.api.cors_origins,
                   allow_methods=["*"], allow_headers=["*"])


@app.get("/health", response_model=Health, tags=["meta"])
def health() -> Health:
    return Health(status="ok", version=__version__, planners=sorted(PLANNERS),
                  placeholder_data=list(get_market().placeholder_files))


@app.get("/scenarios", response_model=ScenariosResponse, tags=["meta"])
def scenarios() -> ScenariosResponse:
    cfg = get_cfg()
    out = []
    for sid in cfg.scenarios.scenarios:
        sc = cfg.scenarios.resolved(sid)
        out.append(ScenarioInfo(id=sid, description=sc.description,
                                start_date=date.fromisoformat(str(sc.start_date)),
                                days=sc.days or cfg.default.simulation.days,
                                events=[e.model_dump() for e in sc.events]))
    s = cfg.default.seeds
    return ScenariosResponse(scenarios=out, seeds=SeedSets(tuning=s.tuning,
                                                           validation=s.validation,
                                                           hidden_min=s.hidden_min))


@app.post("/instance", response_model=InstanceResponse, tags=["planning"])
def instance(req: InstanceRequest) -> InstanceResponse:
    inst = get_instance(req.scenario_id, req.seed, req.start_date)
    state = initial_state(inst, get_cfg(), req.forecast_mode, req.risk_p)
    return InstanceResponse(scenario_id=inst.scenario_id, seed=inst.seed,
                            start_date=inst.start_date, days=inst.days,
                            fingerprint=inst.fingerprint(),
                            announced_events=[e for e in inst.events if e["announced"]],
                            placeholder_data=inst.placeholder_data, state=state)


@app.post("/plan", response_model=PlanResponse, tags=["planning"])
def plan(req: PlanRequest) -> PlanResponse:
    check_state(req.state)
    state = with_risk(req.state, req.risk_p)
    p = make_plan(state, req.planner)
    return PlanResponse(plan=p, summary=summarise(p, state))


@app.post("/whatif", response_model=WhatIfResponse, tags=["planning"])
def whatif(req: WhatIfRequest) -> WhatIfResponse:
    check_state(req.state)
    before_state = with_risk(req.state, req.risk_p)
    try:
        after_state = apply_event(before_state, req.event, get_cfg())
    except WhatIfError as exc:
        raise HTTPException(422, str(exc)) from exc
    before = req.previous_plan or make_plan(before_state, req.planner)
    after = make_plan(after_state, req.planner)
    return WhatIfResponse(state=after_state, plan=after, previous_plan=before,
                          diff=diff_plans(before, before_state, after, after_state))


@app.post("/simulate", response_model=SimulateResponse, tags=["evaluation"])
def simulate(req: SimulateRequest) -> SimulateResponse:
    cfg = get_cfg()
    limit = cfg.default.api.simulate_max_days
    if req.days is not None and req.days > limit:
        raise HTTPException(422, f"days must be ≤ {limit} (api.simulate_max_days)")
    inst = get_instance(req.scenario_id, req.seed, days=req.days)
    if inst.days > limit:
        raise HTTPException(422, f"scenario has {inst.days} days; pass days ≤ {limit}")
    fests = festival_dates(cfg, inst.start_date, inst.days)
    runs = []
    for name in dict.fromkeys(req.planners):  # de-duplicate, keep order
        t0 = time.perf_counter()
        r = run(inst, get_planner(name), cfg, replan=req.replan, risk_p=req.risk_p)
        runs.append(SimRun(planner=name, metrics=run_metrics(r, fests), result=r,
                           wall_s=round(time.perf_counter() - t0, 3)))
    return SimulateResponse(scenario_id=inst.scenario_id, seed=inst.seed, days=inst.days,
                            fingerprint=inst.fingerprint(), runs=runs)


def latest_report(reports_dir: Path | None = None) -> Path | None:
    """Newest ``eval_*.json``; a frozen ``reports/final/`` report wins over working runs."""
    reports_dir = reports_dir or REPORTS_DIR
    for folder in (reports_dir / "final", reports_dir):
        found = sorted(folder.glob("eval_*.json"))
        if found:
            return found[-1]
    return None


@lru_cache(maxsize=4)
def _read_report(path: str, mtime: float) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


@app.get("/eval/latest", tags=["evaluation"])
def eval_latest(
    include_runs: Annotated[bool, Query(description="add every per-run row (large)")] = False,
) -> dict[str, Any]:
    path = latest_report()
    if path is None:
        raise HTTPException(404, "no evaluation report yet; run `python tasks.py eval`")
    report = _read_report(str(path), path.stat().st_mtime)
    out = {k: v for k, v in report.items() if include_runs or k != "runs"}
    out["source_file"] = path.name if path.parent.name != "final" else f"final/{path.name}"
    return out


@app.get("/market/pulse", response_model=PulseResponse, tags=["market"])
def pulse(
    scenario_id: str = "normal",
    seed: Annotated[int, Query(ge=0)] = 51,
    date_: Annotated[date | None, Query(alias="date", description="None = scenario start")] = None,
    dest_id: str | None = None,
) -> PulseResponse:
    inst = get_instance(scenario_id, seed)
    day = 0 if date_ is None else (date_ - inst.start_date).days
    if not 0 <= day < inst.days:
        raise HTTPException(422, f"date must be within {inst.start_date} … "
                                 f"{inst.date_of(inst.days - 1)}")
    if dest_id is not None and dest_id not in {d.dest_id for d in inst.destinations}:
        raise HTTPException(404, f"unknown destination '{dest_id}'")
    return market_pulse(inst, day, get_cfg(), get_market(), dest_id)
