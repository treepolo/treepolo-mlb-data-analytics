from __future__ import annotations

import sqlite3

import pytest

from state_fixtures import make_state_db
from treepolo_mlb_data.research.synthetic import WORLDS, simulate, write_world_db
from treepolo_mlb_data.config import AppConfig
from treepolo_mlb_data.research.config_schema import ConfigError
from treepolo_mlb_data.research.service import ResearchService

SCOPE = {"game_years": [2024], "game_types": ["R"]}


def make_service(tmp_path, db_builder, *, backend="duckdb"):
    data = tmp_path / "data"
    data.mkdir()
    db_builder(data / "statcast.sqlite3")
    return ResearchService(AppConfig(data_dir=str(data), analysis_backend=backend), None)


@pytest.fixture()
def states_service(tmp_path):
    return make_service(tmp_path, make_state_db)


def _rows(service, run, index):
    return service.result_page(run["id"], index, 0, 1000)["rows"]


def test_run_expectancy_known_answer(states_service):
    out = states_service.run("run_expectancy", {"scope": SCOPE})
    run = out["run"]
    assert run["status"] == "success" and run["summary"]["section_row_counts"][0] == 288
    table = _rows(states_service, run, 0)
    assert all(r["n"] == 2 and r["re"] == 1.0 and r["se"] == 1.0 and r["low_n"] == 1 for r in table)   # n=2 < min_state_n=30
    values = _rows(states_service, run, 1)
    assert len(values) == 288 and all(r["outcome"] == "ball" and r["n"] == 2 and r["mean_value"] == 0.0 for r in values)
    assert states_service.store.load_result(run["id"])["extras"]["states_missing"] == 0


def test_outcome_table_default_settings(states_service):
    out = states_service.run("outcome_table", {"scope": SCOPE})
    run = out["run"]
    titles = run["summary"]["section_titles"]
    assert titles[0].startswith("Cells (four_groups)") and titles[1].startswith("Outcome probabilities (four_groups)")
    cells = _rows(states_service, run, 0)
    cell = next(r for r in cells if r["balls"] == 0 and r["strikes"] == 0 and r["pitch_type"] == "FF")
    assert cell["hand_group"] == "RvR" and cell["n"] == 48 and cell["c_ball"] == 48 and cell["mean_value"] == 0.0 and cell["low_n"] == 1
    probs = [r for r in _rows(states_service, run, 1) if r["balls"] == 0 and r["strikes"] == 0]
    assert {r["outcome"] for r in probs} == {"ball"} and probs[0]["p"] == 1.0 and probs[0]["hi"] == 1.0


def test_same_run_is_reused_and_equivalent_re_scope_spellings_share_a_key(states_service):
    first = states_service.run("outcome_table", {"scope": SCOPE})
    second = states_service.run("outcome_table", {"scope": SCOPE, "re_scope": {"game_types": ["R"], "game_years": [2023, 2024]}})
    assert not first["reused"] and second["reused"] and first["run"]["id"] == second["run"]["id"]


def test_changed_re_data_outside_the_scope_is_not_reused(tmp_path):
    service = make_service(tmp_path, make_state_db)
    config = {"scope": SCOPE, "re_scope": {"game_years": [2023], "game_types": ["R"]}}
    first = service.run("outcome_table", config)
    assert first["run"]["data_fingerprint"]["method_inputs"]["re_scope"]["game_years"] == [2023]
    conn = sqlite3.connect(service.config.database_path)
    conn.execute("UPDATE pitches SET _ingested_at = '2026-06-01T00:00:00+00:00' WHERE game_year = 2023 AND game_pk = (SELECT MIN(game_pk) FROM pitches)")
    conn.execute("UPDATE settings SET value = 'rev-2' WHERE key = 'data_revision'")
    conn.commit(); conn.close()
    second = service.run("outcome_table", config)
    assert not second["reused"] and second["run"]["id"] != first["run"]["id"]


def test_reserved_seasons_are_guarded(tmp_path):
    service = make_service(tmp_path, lambda p: make_state_db(p, years=(2023, 2024, 2025)))
    with pytest.raises(ConfigError, match="reserved"):
        service.run("outcome_table", {"scope": {"game_years": [2025], "game_types": ["R"]}})             # tuning on a test season
    with pytest.raises(ConfigError, match="final_test"):
        service.run("outcome_table", {"scope": SCOPE, "purpose": "final_test"})                          # final_test on a modeling season
    with pytest.raises(ConfigError, match="reserved"):
        service.run("outcome_table", {"scope": SCOPE, "re_scope": {"game_years": [2024, 2025], "game_types": ["R"]}})
    ok = service.run("outcome_table", {"scope": {"game_years": [2025], "game_types": ["R"]}, "purpose": "final_test"})
    assert ok["run"]["status"] == "success"
    extra = service.run("outcome_table", {"scope": {"game_years": [2024, 2025], "game_types": ["R"]}, "purpose": "extra_study",
                                          "re_scope": {"game_years": [2023, 2024, 2025], "game_types": ["R"]}})
    assert extra["run"]["status"] == "success"


@pytest.mark.parametrize("bad", [
    {"situation_fields": ["plate_x"]},                                   # continuous column
    {"choice_fields": ["balls"]},                                        # repeats a situation field
    {"choice_fields": ["prev3_pitch_type"], "memory": 2},                # needs a longer memory
    {"choice_fields": ["loc_x_bin"], "loc_x_edges": []},                 # bins without edges
    {"loc_x_edges": [0.5, -0.5]},                                        # not increasing
    {"hand_views": ["same_opposite"]},                                   # needs location_frame=mirrored
    {"hand_views": ["same_opposite"], "location_frame": "mirrored", "cluster_by": "game"},
    {"hand_views": ["both"]},
    {"filters": [{"field": "pitcher", "op": "like", "value": 1}]},
    {"re_scope": {"game_years": [2023]}, "unknown_setting": 1},
])
def test_outcome_table_rejects_invalid_settings(states_service, bad):
    with pytest.raises(ConfigError):
        states_service.run("outcome_table", {"scope": SCOPE, **bad})


def test_max_cells_is_an_error_not_a_truncation(states_service):
    with pytest.raises(ConfigError, match="max_cells"):
        states_service.run("outcome_table", {"scope": SCOPE, "max_cells": 5})


def test_re_scope_that_misses_states_is_rejected(tmp_path):
    service = make_service(tmp_path, lambda p: make_state_db(p, years=(2024,)))
    from pitch_fixtures import make_row
    conn = sqlite3.connect(service.config.database_path)
    conn.execute("DELETE FROM pitches WHERE balls = 3 AND strikes = 2")
    conn.commit(); conn.close()
    with pytest.raises(ConfigError, match="288"):
        service.run("outcome_table", {"scope": SCOPE, "re_scope": SCOPE})


def test_same_opposite_view_and_symmetry_check(states_service):
    out = states_service.run("outcome_table", {
        "scope": SCOPE, "hand_views": ["four_groups", "same_opposite"], "location_frame": "mirrored", "min_samples": 1,
        "choice_fields": ["pitch_type", "zone"], "symmetry_top": 5})
    titles = out["run"]["summary"]["section_titles"]
    assert any(t.startswith("Cells (same_opposite)") for t in titles) and any(t.startswith("Symmetry check 對稱性檢查") for t in titles)


def test_sqlite_only_configuration_runs_without_duckdb(tmp_path):
    service = make_service(tmp_path, make_state_db, backend="sqlite")
    assert service.run("run_expectancy", {"scope": SCOPE, "outcome_values": False, "savant_comparison": False})["run"]["status"] == "success"


def test_a_silent_duckdb_fallback_is_an_error(tmp_path):
    service = make_service(tmp_path, make_state_db)
    service.config.analytics_database_path.mkdir()          # a directory where the DuckDB file should be: the mirror cannot be built
    with pytest.raises(RuntimeError, match="SQLite"):
        service.run("run_expectancy", {"scope": SCOPE})


def test_streak_curve_through_the_service_on_a_synthetic_world(tmp_path):
    def build(path):
        write_world_db(path, simulate(n_pa=40_000, seed=2, **WORLDS["T1"]))
    service = make_service(tmp_path, build)
    out = service.run("streak_curve", {
        "scope": SCOPE, "hand_view": "pooled", "pitch_types": ["SL"], "min_type_pitches": 100, "kmax": 4, "se_method": "cluster_bootstrap",
        "cluster_by": "game", "bootstrap_reps": 30, "placebo_shuffles": 3})
    run = out["run"]
    assert run["summary"]["section_titles"][0].startswith("Naive curve") and len(run["summary"]["section_titles"]) == 3
    same = _rows(service, run, 1)
    assert [r["k"] for r in same] == [2, 3, 4] and all(r["boot_se"] is not None for r in same) and same[0]["estimate"] < 0
    placebo = _rows(service, run, 2)
    assert len(placebo) == 3 and all(r["placebo_n"] == 3 for r in placebo)
    again = service.rerun(run["id"])                        # same settings, same data -> identical result (seeded, rounded)
    assert again["reproduced"] is True


def test_streak_curve_rejects_strata_that_describe_the_previous_pitches(tmp_path):
    service = make_service(tmp_path, lambda p: write_world_db(p, simulate(n_pa=2_000, seed=2, **WORLDS["T1"])))
    for bad in (["prev1_pitch_type"], ["history_key"], ["streak_pos"], ["plate_x"]):
        with pytest.raises(ConfigError):
            service.run("streak_curve", {"scope": SCOPE, "strata_fields": bad})
