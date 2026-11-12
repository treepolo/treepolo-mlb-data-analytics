"""Comparing several models on the same data, the time estimate, and the comparison as a background job."""
from __future__ import annotations

import pytest

from treepolo_mlb_data.config import AppConfig
from treepolo_mlb_data.research import api, estimate as est, gbm
from treepolo_mlb_data.research.config_schema import ConfigError
from treepolo_mlb_data.research.service import ResearchService
from treepolo_mlb_data.research.synthetic import simulate_players, write_player_world_db

HAS_XGB = gbm.xgboost_available()
SCOPE = {"game_years": [2023, 2024], "game_types": ["R"]}
MODELS = ["logistic", "gbm"] if HAS_XGB else ["logistic"]
RUN = {"scope": SCOPE, "groups": ["RvR"], "models": MODELS, "train_years": [2023], "test_years": [2024], "cold_by": ["pitcher"], "cold_folds": 3, "max_iter": 40, "backend": "xgboost" if HAS_XGB else "hgb",
       "device": "cpu", "max_depth": 3, "min_leaf": 100, "min_class_count": 20, "effect_sample": 3000, "overrides": {"logistic": {"control_pitcher": "effect", "max_iter": 60}}}


@pytest.fixture(scope="module")
def players(tmp_path_factory):
    data = tmp_path_factory.mktemp("cmp_world") / "data"; data.mkdir()
    d = simulate_players("P7", n_pitchers=50, n_batters=70, n_games=700, pitches_per_game=40, seed=9)
    write_player_world_db(data / "statcast.sqlite3", d)
    return ResearchService(AppConfig(data_dir=str(data)), None)


def _section(service, run, prefix):
    return next(s["rows"] for s in service.store.load_result(run["id"])["sections"] if s["title"].startswith(prefix))


def test_the_models_are_listed_side_by_side_on_the_same_rows_and_nothing_is_judged(players):
    out = players.run("model_compare", RUN)
    run = out["run"]
    summary = _section(players, run, "Comparison")
    assert {(r["model"], r["mode"]) for r in summary} == {(m, mode) for m in MODELS for mode in (("season_specific", "season_population", "cold_pitcher") if m == "logistic" else ("season_specific", "cold_pitcher"))}
    assert all(r["n_eval"] > 0 and r["logloss"] > 0 for r in summary) and not any(k in r for r in summary for k in ("best", "winner", "rank", "passed"))
    same = {m: {(r["mode"], r["n_eval"]) for r in summary if r["model"] == m and r["mode"] != "season_population"} for m in MODELS}
    assert len({frozenset(v) for v in same.values()}) == 1                                  # the same held-out rows and the same players left out for every model
    by_mode: dict[str, list[float]] = {}
    for r in summary:
        by_mode.setdefault(r["mode"], []).append(r["logloss"])
    assert all(v == sorted(v) for v in by_mode.values())                                    # ordered by the chosen column (default log-loss), no more than that
    effects = _section(players, run, "Pitch effects")
    keys = {m: {(r["phase"], r["action"], r["class"]) for r in effects if r["model"] == m} for m in MODELS}
    assert len({frozenset(v) for v in keys.values()}) == 1 and all(keys.values())            # the same cells for every model
    if len(MODELS) == 2:
        diff = _section(players, run, "Effect differences")
        assert len(diff) == len(keys[MODELS[0]]) and {(r["model_a"], r["model_b"]) for r in diff} == {(MODELS[0], MODELS[1])}
        e = {(r["model"], r["phase"], r["action"], r["class"]): r["effect"] for r in effects}
        r0 = diff[0]
        assert r0["difference"] == pytest.approx(e[(r0["model_a"], r0["phase"], r0["action"], r0["class"])] - e[(r0["model_b"], r0["phase"], r0["action"], r0["class"])])
    controls = _section(players, run, "Controls")
    assert next(r for r in controls if r["model"] == "logistic" and r["control"] == "control_pitcher")["setting"] == "effect"
    if HAS_XGB:
        assert next(r for r in controls if r["model"] == "gbm" and r["control"] == "control_pitcher")["setting"] == "none"       # each model has its own settings
    assert players.run("model_compare", RUN)["reused"]


def test_the_rows_can_be_ordered_by_another_column_and_wrong_settings_are_refused(players):
    run = players.run("model_compare", {**RUN, "sort_by": "fit_seconds"})["run"]
    secs = [r["fit_seconds"] for r in _section(players, run, "Comparison") if r["mode"] == "season_specific"]
    assert secs == sorted(secs)
    with pytest.raises(ConfigError, match="models"):
        players.run("model_compare", {**RUN, "models": []})
    with pytest.raises(ConfigError, match="overrides"):
        players.run("model_compare", {**RUN, "overrides": {"mixed": {"max_iter": 3}}})                                       # an override for a model that is not in the comparison
    with pytest.raises(ConfigError, match="unknown settings"):
        players.run("model_compare", {**RUN, "overrides": {"logistic": {"no_such": 1}}})
    with pytest.raises(ConfigError, match="control_batter"):
        players.run("model_compare", {**RUN, "control_survival": "batter_and_count"})
    with pytest.raises(ConfigError, match="disjoint"):
        players.run("model_compare", {**RUN, "test_years": [2023]})
    with pytest.raises(ConfigError, match="unknown model"):
        players.run("model_compare", {**RUN, "models": ["no_such_model"], "overrides": {}})


def test_the_cost_rules_scale_the_trial_time_as_documented():
    st = {"max_iter": 300, "bagging_reps": 2, "epochs": 10}
    assert est.scale("rounds", 2.0, 1000, 1_000_000, st, 20) == pytest.approx(2.0 / 1000 * 1_000_000 * 15 * 2)               # rows x rounds x members
    assert est.scale("epochs", 2.0, 1000, 1_000_000, st, 1) == pytest.approx(2.0 / 1000 * 1_000_000 * 10 * 2)               # rows x epochs x members
    assert est.scale("linear", 2.0, 1000, 500_000, {"bagging_reps": 1}, 0) == pytest.approx(1000.0)                          # the trial ran to the end: only the rows grow
    assert est._fits(["pitcher", "batter"], 3, False) == 1 + 2 * 2 and est._fits([], 3, False) == 1 and est._fits(["pitcher"], 5, True) == 2 * (1 + 4)


def test_the_estimate_times_a_short_trial_and_reports_what_it_assumed(players):
    out = est.estimate(players, {"models": MODELS + ["no_such_model"], "groups": ["RvR"], "years": [2023, 2024], "train_years": [2023], "cold_by": ["pitcher"], "cold_folds": 3,
                                 "settings": {"max_iter": 60, "backend": "xgboost" if HAS_XGB else "hgb", "device": "cpu"}, "overrides": {"logistic": {"control_pitcher": "effect"}}})
    assert [r["model"] for r in out["estimates"]] == MODELS and [s["model"] for s in out["skipped"]] == ["no_such_model"]
    for r in out["estimates"]:
        assert r["probe_seconds"] > 0 and r["seconds"] > 0 and r["low"] == pytest.approx(r["seconds"] * 0.5) and r["high"] == pytest.approx(r["seconds"] * 2.0)
        assert r["rows_train"] >= r["probe_rows"] > 0 and r["fits"] == 1 + 2                       # the main fit and three folds of two thirds each
    assert out["total_seconds"] == pytest.approx(sum(r["seconds"] for r in out["estimates"])) and out["trial_seconds"] > 0 and "0.5" in out["note"] and out["assumptions"]["cold_folds"] == 3
    with pytest.raises(ConfigError, match="models"):
        est.estimate(players, {"models": []})


def test_a_comparison_runs_as_a_background_job_with_times_and_an_api(players):
    manager = players.pipeline()
    started = manager.start_compare({"years": [2023, 2024], "groups": ["RvR"], "config": {**{k: v for k, v in RUN.items() if k not in ("scope", "groups")}, "sort_by": "brier"}})
    assert started["state"] == "running" and started["kind"] == "compare" and started["steps"][0]["id"] == "compare" and "server_time" in started
    manager.wait(900)
    job = manager.status()
    assert job["state"] == "done" and job["summary"]["compare_run_id"] and job["steps"][0]["status"] == "done" and job["steps"][0]["started_at"] > 0 and job["server_time"] >= job["steps"][0]["started_at"]
    with pytest.raises(ConfigError, match="models"):
        manager.start_compare({"years": [2023, 2024], "config": {"models": []}})                                              # a wrong setting is refused before anything starts
    status, body = api.handle_post(players, "/api/research/pipeline/estimate", {"models": ["logistic"], "groups": ["RvR"], "years": [2023, 2024], "train_years": [2023], "cold_by": []})
    assert status == 200 and body["estimates"][0]["fits"] == 1
    status, info = api.handle_get(players, "/api/research/pipeline/info", "")
    assert "model_compare" not in info["step_methods"] and {"logistic", "gbm"} <= set(info["models"]) and set(info["model_choices"]) >= {"mixed", "nn_embed", "sequence"}
