from __future__ import annotations

import pytest

from state_fixtures import make_state_db
from treepolo_mlb_data.config import AppConfig
from treepolo_mlb_data.research.config_schema import ConfigError
from treepolo_mlb_data.research.service import ResearchService


@pytest.fixture()
def service(tmp_path):
    data = tmp_path / "data"; data.mkdir()
    make_state_db(data / "statcast.sqlite3")
    return ResearchService(AppConfig(data_dir=str(data)), None)


def _key(out):
    return out["run"]["run_key"]


def test_compare_two_re_tables_with_known_differences(service):
    a = service.run("run_expectancy", {"scope": {"game_years": [2023], "game_types": ["R"]}, "outcome_values": False, "savant_comparison": False})
    b = service.run("run_expectancy", {"scope": {"game_years": [2024], "game_types": ["R"]}, "outcome_values": False, "savant_comparison": False})
    out = service.run("run_compare", {"run_key_a": _key(a), "run_key_b": _key(b), "section": "Run expectancy", "estimate": "re", "se": "se"})["run"]
    titles = out["summary"]["section_titles"]
    assert titles[0].startswith("Compare") and titles[1].startswith("Unmatched") and titles[2].startswith("Config differences")
    rows = service.result_page(out["id"], 0, 0, 1000)["rows"]
    assert len(rows) == 288 and all(r["diff"] == 0 and r["z"] == 0 for r in rows)             # both seasons are built identically
    assert service.result_page(out["id"], 1, 0, 10)["total"] == 0
    assert service.result_page(out["id"], 2, 0, 10)["total"] == 0                              # only scope differs, and scope is ignored
    extras = service.store.load_result(out["id"])["extras"]
    assert extras["n_matched"] == 288 and extras["direction"] == "diff = b - a"


def test_compare_detects_unmatched_rows_config_differences_and_sorts_by_z(service):
    scope = {"game_years": [2024], "game_types": ["R"]}
    a = service.run("outcome_table", {"scope": scope, "use_values": False, "min_samples": 1})
    b = service.run("outcome_table", {"scope": scope, "use_values": False, "min_samples": 2, "filters": [{"field": "balls", "op": "eq", "value": 0}]})
    out = service.run("run_compare", {"run_key_a": _key(a), "run_key_b": _key(b), "section": "Cells", "estimate": "swing_rate"})["run"]
    diffs = {r["key"] for r in service.result_page(out["id"], 2, 0, 50)["rows"]}
    assert diffs == {"min_samples", "filters"}
    unmatched = service.result_page(out["id"], 1, 0, 500)["rows"]
    assert unmatched and all(r["side"] == "only_a" for r in unmatched)           # b only has balls = 0 cells
    matched = service.result_page(out["id"], 0, 0, 500)["rows"]
    assert matched and all(r["diff"] == 0 for r in matched)
    with pytest.raises(ConfigError, match="Duplicate key"):
        service.run("run_compare", {"run_key_a": _key(a), "run_key_b": _key(b), "section": "Cells", "estimate": "swing_rate", "key_columns": ["hand_group", "pitch_type"]})


def test_compare_hand_computed_difference_and_z(service):
    import sqlite3

    conn = sqlite3.connect(service.config.database_path)
    conn.execute("UPDATE pitches SET post_bat_score = 3, post_away_score = 3 WHERE game_year = 2024 AND post_bat_score = 2")
    conn.execute("UPDATE settings SET value = 'rev-2' WHERE key = 'data_revision'")
    conn.commit(); conn.close()
    quiet = {"outcome_values": False, "savant_comparison": False}
    a = service.run("run_expectancy", {"scope": {"game_years": [2023], "game_types": ["R"]}, **quiet})
    b = service.run("run_expectancy", {"scope": {"game_years": [2024], "game_types": ["R"]}, **quiet})
    out = service.run("run_compare", {"run_key_a": _key(a), "run_key_b": _key(b), "section": "Run expectancy", "estimate": "re", "se": "se", "min_abs_diff": 0.1})["run"]
    rows = service.result_page(out["id"], 0, 0, 1000)["rows"]
    assert len(rows) == 288
    # 2023: runs 0 and 2 -> RE 1.0, SE 1.0.  2024: runs 0 and 3 -> RE 1.5, SE 1.5 -> diff 0.5, se_diff sqrt(1 + 2.25), z 0.27735
    assert rows[0]["a"] == 1.0 and rows[0]["b"] == 1.5 and rows[0]["diff"] == 0.5
    assert rows[0]["se_diff"] == pytest.approx(1.8027756, abs=1e-6) and rows[0]["z"] == pytest.approx(0.2773501, abs=1e-6)
    assert service.rerun(out["id"])["reproduced"] is True


def test_unknown_run_key_and_section_are_errors(service):
    with pytest.raises(ConfigError, match="run_key_a"):
        service.run("run_compare", {"run_key_a": "x", "run_key_b": "y", "section": "s", "estimate": "e"})
    a = service.run("run_expectancy", {"scope": {"game_years": [2024], "game_types": ["R"]}, "outcome_values": False, "savant_comparison": False})
    with pytest.raises(ConfigError, match="section"):
        service.run("run_compare", {"run_key_a": _key(a), "run_key_b": _key(a), "section": "Nope", "estimate": "re"})
    with pytest.raises(ConfigError, match="column"):
        service.run("run_compare", {"run_key_a": _key(a), "run_key_b": _key(a), "section": "Run expectancy", "estimate": "nope"})
