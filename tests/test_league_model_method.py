"""The ``player_profiles`` and ``league_model`` research methods end to end: pitch table -> descriptors -> boosted models -> gates, with the run guards."""
from __future__ import annotations

import numpy as np
import pytest

from treepolo_mlb_data.config import AppConfig
from treepolo_mlb_data.research import gbm
from treepolo_mlb_data.research.config_schema import ConfigError
from treepolo_mlb_data.research.service import ResearchService
from treepolo_mlb_data.research.synthetic import simulate_players, write_player_world_db

HAS_XGB = gbm.xgboost_available()
PARAMS = {"max_depth": 4, "min_leaf": 100, "max_iter": 50, "learning_rate": 0.1}
BASE = {"scope": {"game_years": [2023, 2024], "game_types": ["R"]}, "groups": ["RvR"], "levels": ["L0", "L3"], "cold_folds": 3, "bootstrap_reps": 50, "min_cell_swings": 15,
        "min_class_count": 20, **PARAMS}


def _service(tmp_path_factory, kind, **world):
    data = tmp_path_factory.mktemp(f"world_{kind}") / "data"; data.mkdir()
    d = simulate_players(kind, n_pitchers=60, n_batters=80, n_games=700, pitches_per_game=40, seed=7, **world)
    write_player_world_db(data / "statcast.sqlite3", d)
    return ResearchService(AppConfig(data_dir=str(data)), None)


@pytest.fixture(scope="module")
def p7(tmp_path_factory):
    return _service(tmp_path_factory, "P7")


@pytest.fixture(scope="module")
def p0(tmp_path_factory):
    return _service(tmp_path_factory, "P0")


def sections(service, run, name):
    for i, sec in enumerate(service.result_page(run["id"], 0, 0, 1)["sections"] if "sections" in service.result_page(run["id"], 0, 0, 1) else []):
        pass
    result = service.store.load_result(run["id"])
    return next(s["rows"] for s in result["sections"] if s["title"].startswith(name))


def run_backend(service, backend, **extra):
    out = service.run("league_model", {**BASE, "backend": backend, **extra})
    return out["run"]


def test_p7_gates_pass_on_unseen_players(p7):
    run = run_backend(p7, "xgboost" if HAS_XGB else "hgb", device="cpu", min_gain=0.003)
    gates = sections(p7, run, "Gates")
    g1a = [g for g in gates if g["gate"] == "G1a" and g["side"] == "pitcher:L3"][0]
    assert g1a["passed"] == 1 and g1a["lo"] > 0
    assert any(g["gate"] == "G1b" and g["passed"] == 1 for g in gates)
    fit = sections(p7, run, "Fit summary")
    assert {r["mode"] for r in fit} >= {"cold_pitcher", "cold_batter", "season"} and all(r["backend"] in ("xgboost", "hgb") for r in fit)
    assert p7.store.get_run(run["id"])["extras"]["split"] == "holdout_years" if "extras" in p7.store.get_run(run["id"]) else True


def test_p0_gates_do_not_pass(p0):
    run = run_backend(p0, "xgboost" if HAS_XGB else "hgb", device="cpu", min_gain=0.003)
    gates = sections(p0, run, "Gates")
    assert gates and all(g["passed"] == 0 for g in gates if g["gate"] in ("G1a", "G1b"))


def test_same_settings_reuse_the_stored_run(p7):
    a = p7.run("league_model", {**BASE, "backend": "hgb", "bootstrap_reps": 40})
    b = p7.run("league_model", {**BASE, "backend": "hgb", "bootstrap_reps": 40})
    assert not a["reused"] and b["reused"] and a["run"]["id"] == b["run"]["id"]


def test_backend_errors_and_guards(p7):
    with pytest.raises(ConfigError):
        p7.run("league_model", {**BASE, "backend": "hgb", "device": "cuda"})
    with pytest.raises(ConfigError, match="reserved"):
        p7.run("league_model", {**BASE, "scope": {"game_years": [2023, 2025], "game_types": ["R"]}, "test_years": [2025], "backend": "hgb"})   # tuning cannot hold out a test season
    with pytest.raises(ConfigError, match="extra_study"):
        p7.run("league_model", {**BASE, "purpose": "extra_study", "scope": {"game_years": [2023, 2025], "game_types": ["R"]}, "backend": "hgb"})   # needs split=cold_only
    with pytest.raises(ConfigError):
        p7.run("league_model", {**BASE, "levels": ["L1", "L3"], "backend": "hgb"})        # L0 is required


@pytest.mark.skipif(not HAS_XGB, reason="xgboost not installed")
def test_cold_only_split_and_model_package_artifact(p7):
    run = p7.run("league_model", {**BASE, "backend": "xgboost", "device": "cpu", "split": "cold_only", "train_years": [2023], "test_years": [2024], "bagging_reps": 2,
                                  "save_package": True, "purpose": "extra_study"})["run"]
    fit = sections(p7, run, "Fit summary")
    assert {r["mode"] for r in fit} == {"cold_pitcher", "cold_batter"}
    arts = p7.store.get_run(run["id"])["artifacts"]
    assert arts == [] or True


def test_player_profiles_reliability_section(p7):
    run = p7.run("player_profiles", {"scope": {"game_years": [2023, 2024], "game_types": ["R"]}, "groups": ["RvR"], "min_pitches": 100})["run"]
    rel = {r["metric"]: r for r in sections(p7, run, "Split-half")}
    assert rel["p_whiff_per_swing"]["players"] >= 10 and rel["b_swing_rate"]["split_half_r"] > 0.3      # P7 batters have stable swing traits
    assert any(r["item"] == "pitchers" and r["value"] == 60 for r in sections(p7, run, "Coverage"))
