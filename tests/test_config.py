"""M0: configs load, validate, and fail with readable messages."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from freshroute.config import CONFIG_DIR, ConfigError, load_config
from freshroute.schemas import EXPORTED_MODELS, export_json_schema


def test_default_config_loads(cfg):
    assert {c.id for c in cfg.commodities} == {
        "spinach", "tomato", "okra", "mango", "cabbage", "onion"
    }
    assert 5 <= len(cfg.network.destinations) <= 7
    assert any(v.class_id.startswith("reefer") for v in cfg.fleet.default_fleet)
    assert "normal" in cfg.scenarios.scenarios


def test_every_commodity_has_source(cfg):
    for c in cfg.commodities:
        assert c.source.strip()


def test_scenarios_resolve_defaults(cfg):
    for sid in cfg.scenarios.scenarios:
        sc = cfg.scenarios.resolved(sid)
        assert sc.start_date and sc.days


def test_seed_splits_disjoint(cfg):
    s = cfg.default.seeds
    assert s.tuning[1] < s.validation[0]
    assert s.validation[1] < s.hidden_min


def _copy_config(tmp_path: Path) -> Path:
    dst = tmp_path / "config"
    shutil.copytree(CONFIG_DIR, dst)
    return dst


def _edit(path: Path, fn) -> None:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    fn(data)
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")


def test_out_of_range_value_is_readable(tmp_path):
    cdir = _copy_config(tmp_path)
    _edit(cdir / "default.yaml", lambda d: d["planner"].__setitem__("risk_p", 1.3))
    with pytest.raises(ConfigError) as exc:
        load_config(cdir)
    msg = str(exc.value)
    assert "default.yaml → planner.risk_p" in msg
    assert "1.3" in msg


def test_typo_key_rejected(tmp_path):
    cdir = _copy_config(tmp_path)
    _edit(cdir / "default.yaml", lambda d: d["planner"].__setitem__("time_limt_s", 5))
    with pytest.raises(ConfigError, match="time_limt_s"):
        load_config(cdir)


def test_bad_commodity_field_names_file(tmp_path):
    cdir = _copy_config(tmp_path)
    _edit(
        cdir / "commodities.yaml",
        lambda d: d["commodities"][1].__setitem__("temp_group", "frozen"),
    )
    with pytest.raises(ConfigError, match=r"commodities\.yaml → 1\.temp_group"):
        load_config(cdir)


def test_cross_reference_error(tmp_path):
    cdir = _copy_config(tmp_path)
    _edit(
        cdir / "scenarios.yaml",
        lambda d: d["scenarios"]["reefer_breakdown"]["events"][0]["params"].__setitem__(
            "vehicle_id", "R9"
        ),
    )
    with pytest.raises(ConfigError, match="unknown vehicle_id 'R9'"):
        load_config(cdir)


def test_missing_file(tmp_path):
    cdir = _copy_config(tmp_path)
    (cdir / "fleet.yaml").unlink()
    with pytest.raises(ConfigError, match="missing config file"):
        load_config(cdir)


def test_with_overrides_revalidates(cfg):
    new = cfg.with_overrides({"default.planner.risk_p": 0.95})
    assert new.default.planner.risk_p == 0.95
    assert cfg.default.planner.risk_p == 0.9
    with pytest.raises(ConfigError):
        cfg.with_overrides({"default.planner.risk_p": 2.0})
    with pytest.raises(ConfigError, match="does not exist"):
        cfg.with_overrides({"default.planner.nope": 1})


def test_json_schema_export(tmp_path):
    out = export_json_schema(tmp_path / "schema.json")
    text = out.read_text(encoding="utf-8")
    for model in EXPORTED_MODELS:
        assert f'"{model.__name__}"' in text
