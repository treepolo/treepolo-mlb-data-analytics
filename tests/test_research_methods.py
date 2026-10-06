import pytest

from research_fixtures import make_research_db
from treepolo_mlb_data.config import AppConfig
from treepolo_mlb_data.research import builtin_methods
from treepolo_mlb_data.research.config_schema import ConfigError, ConfigField
from treepolo_mlb_data.research.methods import (
    ResearchContext, ResearchMethod, get_method, list_methods, register_method,
)
from treepolo_mlb_data.web_analysis import AnalysisFacade


class Dummy(ResearchMethod):
    kind = "dummy_for_methods_test"
    fields = (ConfigField("x", "int", default=1),)


def test_registry_rules():
    first = Dummy()
    register_method(first)
    assert register_method(first) is first  # same object is idempotent
    assert get_method("dummy_for_methods_test") is first
    with pytest.raises(ValueError, match="already registered"):
        register_method(Dummy())
    with pytest.raises(ConfigError, match="Unknown research method"):
        get_method("nope")
    with pytest.raises(ValueError):
        register_method(ResearchMethod())
    assert "analysis_payload" in [m.kind for m in list_methods()]


def test_describe_shape():
    info = get_method("analysis_payload").describe()
    assert info["kind"] == "analysis_payload" and info["version"] == 1 and info["requires_scope"] is False
    assert info["fields"][0]["name"] == "payload" and info["fields"][0]["required"] is True


def make_ctx(tmp_path):
    db = make_research_db(tmp_path / "statcast.sqlite3")
    config = AppConfig(data_dir=str(tmp_path), analysis_backend="sqlite")
    return ResearchContext(config=config, facade=AnalysisFacade(db, backend="sqlite"), scope={}), db


def test_analysis_payload_validation(tmp_path):
    ctx, _ = make_ctx(tmp_path)
    method = builtin_methods.ANALYSIS_PAYLOAD
    method.validate({"payload": {"mode": "basic"}}, ctx)
    for bad in ({"payload": []}, {"payload": {}}, {"payload": {"mode": "basic", "result_limit": 10}}):
        with pytest.raises(ConfigError):
            method.validate(bad, ctx)


def test_analysis_payload_runs_a_real_analysis(tmp_path):
    ctx, _ = make_ctx(tmp_path)
    payload = {"mode": "basic", "group_by": ["pitch_type"], "metrics": [{"function": "count"}], "limit": 50}
    result = builtin_methods.ANALYSIS_PAYLOAD.run(ctx, {"payload": payload})
    section = result.sections[0]
    assert section["title"] == "basic" and section["columns"] == ["pitch_type", "row_count"]
    counts = {row["pitch_type"]: row["row_count"] for row in section["rows"]}
    assert counts == {"FF": 6, "SL": 6}
    assert section["row_count"] == 2 and section["grain"]["keys"] == ["pitch_type"]
    assert result.extras == {"payload_mode": "basic"} and result.artifacts == ()


def test_context_source_node_applies_scope(tmp_path):
    ctx, _ = make_ctx(tmp_path)
    assert type(ctx.source_node()).__name__ == "Source"
    ctx.scope = {"game_years": [2024], "game_types": ["R"]}
    rows = ctx.engine().execute(ctx.source_node()).rows
    assert len(rows) == 4 and {r["game_year"] for r in rows} == {2024}
