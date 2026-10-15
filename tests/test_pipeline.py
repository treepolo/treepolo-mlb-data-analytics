"""The setting search, the one-click pipeline (job, steps, reuse, summary) and its API, on a synthetic world with known player effects."""
from __future__ import annotations

import sqlite3

import numpy as np
import pytest

from treepolo_mlb_data.config import AppConfig
from treepolo_mlb_data.research import anchor_methods as am, api, gbm, pipeline as pl, policy_methods as pm, policy_worlds as pw, tune_methods as tm
from treepolo_mlb_data.research.config_schema import ConfigError
from treepolo_mlb_data.research.service import ResearchService
from treepolo_mlb_data.research.synthetic import simulate_players, write_player_world_db

BACKEND = "xgboost" if gbm.xgboost_available() else "hgb"
SCOPE = {"game_years": [2023, 2024], "game_types": ["R"]}
GRID = {"max_depth": [3, 4], "min_leaf": [200, 600], "learning_rate": [0.15], "l2": [1.0, 5.0]}
OVERRIDES = {"search": {"grid": GRID, "min_class_count": 20},
             "model": {"levels": ["L0", "L3"], "min_class_count": 20, "min_cell_swings": 15, "min_gain": 0.003},
             "policy": {"min_count": 100, "lambda_grid": [0.0], "min_class_count": 20, "cross_eval": False},
             "query": {"check_situations": 6, "check_samples": 6, "check_members": 2}}


class FakeRe:
    def __init__(self, re_cnt):
        self.re_by_state = {b * 1000 + k * 100 + o * 10 + B: float(re_cnt[b, k, o, B]) for b in range(4) for k in range(3) for o in range(3) for B in range(8)}


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    data = tmp_path_factory.mktemp("pipe") / "data"; data.mkdir()
    d = simulate_players("P7", n_pitchers=40, n_batters=60, n_games=900, pitches_per_game=60, seed=5)
    write_player_world_db(data / "statcast.sqlite3", d)
    conn = sqlite3.connect(data / "statcast.sqlite3")
    conn.execute("ALTER TABLE pitches ADD COLUMN player_name TEXT"); conn.execute("UPDATE pitches SET player_name = 'Pitcher' || pitcher || ', Test'")
    conn.commit(); conn.close()
    service = ResearchService(AppConfig(data_dir=str(data)), None)
    same = d["prev1_pitch_type"] == d["pitch_type"]
    cols = dict(d, prior_streak=np.where(same, d["streak_pos"] - 1, np.where(d["prev1_pitch_type"] != "none", 1.0, 0.0)), pitch_value=np.zeros(len(d["game_pk"])))
    env = pw.build_world("W0").env
    mp = pytest.MonkeyPatch()
    for module in (pm, am):          # the player-world database has no plate-appearance structure: the policy and anchor steps read the same pitches as columns
        mp.setattr(module, "fetch_columns", lambda ctx, config, groups, years, re_by_state=None: cols)
        mp.setattr(module, "get_re_table", lambda ctx, scope: FakeRe(env.re_cnt))
    yield service
    mp.undo()


def settings(**extra):
    return {"tier": "smoke", "groups": ["RvR"], "backend": BACKEND, "device": "cpu", "overrides": OVERRIDES, **extra}


@pytest.fixture(scope="module")
def finished(world):
    manager = world.pipeline()
    manager.start(settings())
    manager.wait(900)
    return manager.status()


def test_sample_trials_are_unique_deterministic_and_inside_the_grid():
    a = tm.sample_trials(tm.DEFAULT_GRID, 8, 1); b = tm.sample_trials(tm.DEFAULT_GRID, 8, 1)
    assert a == b and len({tuple(sorted(t.items())) for t in a}) == 8 and all(t["max_depth"] in (3, 5, 8) for t in a)
    assert len(tm.sample_trials(GRID, 50, 1)) == 8                             # a small grid is tried completely
    assert tm.sample_trials(tm.DEFAULT_GRID, 8, 2) != a


def test_param_search_scores_on_unseen_players_and_ranks(world):
    run = world.run("param_search", {"scope": SCOPE, "groups": ["RvR"], "trials": 3, "folds": 3, "tune_rows": 40_000, "max_iter": 40, "backend": BACKEND, "device": "cpu",
                                     "grid": GRID, "min_class_count": 20})["run"]
    result = world.store.load_result(run["id"])
    trials = next(s["rows"] for s in result["sections"] if s["title"].startswith("Trials"))
    ranking = next(s["rows"] for s in result["sections"] if s["title"].startswith("Ranking"))
    assert len(trials) == 3 and all(np.isfinite(t["logloss"]) and 1.0 < t["logloss"] < 3.0 for t in trials)
    assert [r["logloss"] for r in ranking] == sorted(r["logloss"] for r in ranking) and ranking[0]["rank"] == 1
    best = result["extras"]["best_params"]
    assert best["max_depth"] in GRID["max_depth"] and best["min_leaf"] in GRID["min_leaf"] and result["extras"]["best_logloss"] == pytest.approx(ranking[0]["logloss"])
    with pytest.raises(ConfigError, match="grid"):
        world.run("param_search", {"scope": SCOPE, "grid": {"depth": [3]}})
    again = world.run("param_search", {"scope": SCOPE, "groups": ["RvR"], "trials": 3, "folds": 3, "tune_rows": 40_000, "max_iter": 40, "backend": BACKEND, "device": "cpu",
                                       "grid": GRID, "min_class_count": 20})
    assert again["reused"]


def test_pipeline_runs_every_step_and_summarises(finished):
    job = finished
    assert job["state"] == "done" and job["error"] is None and job["query_run_id"]
    assert [s["id"] for s in job["steps"]] == ["search", "model", "anchor", "policy", "query"] and all(s["status"] == "done" and s["run_id"] for s in job["steps"])
    g = job["summary"]["groups"]["RvR"]
    assert set(g) == {"r1", "policies", "gates", "actions"} and {p["policy"] for p in g["policies"]} >= {"observed", "dp_trust", "dp_unrestricted"}
    assert any(x["gate"] == "G1a" for x in g["gates"]) and len(g["actions"]) >= 2
    assert job["summary"]["best_params"]["max_depth"] in GRID["max_depth"]


def test_pipeline_result_is_queryable_and_a_second_start_reuses_everything(world, finished):
    status = world.pitch_query_meta(finished["query_run_id"])
    assert status["groups"] == ["RvR"] and status["players_available"]
    answer = world.pitch_query(finished["query_run_id"], {"group": "RvR", "balls": "1", "strikes": "1", "outs": "0", "bases": "000", "prev": "SL", "streak": "1"})
    assert answer["mode"] == "M0" and len(answer["rows"]) == 9
    manager = world.pipeline()
    manager.start(settings())
    manager.wait(300)
    again = manager.status()
    assert again["state"] == "done" and all(s["reused"] for s in again["steps"])
    assert [s["run_id"] for s in again["steps"]] == [s["run_id"] for s in finished["steps"]]


def test_pipeline_api_and_validation(world, finished):
    status, body = api.handle_get(world, "/api/research/pipeline/info", "")
    assert status == 200 and {t["id"] for t in body["tiers"]} == {"fast", "standard", "smoke"} and body["groups"] == ["RvR", "RvL", "LvR", "LvL"] and len(body["steps"]) == 5
    assert set(body["environment"]) == {"xgboost", "cuda", "default_backend", "default_device"}
    status, body = api.handle_get(world, "/api/research/pipeline/status", "")
    assert status == 200 and body["state"] == "done"
    with pytest.raises(ConfigError, match="tier"):
        api.handle_post(world, "/api/research/pipeline/start", {"tier": "huge"})
    with pytest.raises(ConfigError, match="groups"):
        api.handle_post(world, "/api/research/pipeline/start", {"tier": "fast", "groups": ["XvX"]})


def test_failing_step_is_reported_and_keeps_earlier_steps(world):
    manager = world.pipeline()
    bad = {"search": {"grid": GRID, "min_class_count": 20}, "model": {"levels": ["L0", "L3"], "min_class_count": 10_000_000}}
    manager.start({"tier": "smoke", "groups": ["RvR"], "backend": BACKEND, "device": "cpu", "overrides": bad, "seed": 7})
    manager.wait(600)
    job = manager.status()
    assert job["state"] == "error" and "訓練全聯盟模型" in job["error"]
    assert [s["status"] for s in job["steps"]] == ["done", "error", "pending", "pending", "pending"]


def test_cancel_stops_before_the_next_step(world):
    manager = world.pipeline()
    manager.start({"tier": "smoke", "groups": ["RvR"], "backend": BACKEND, "device": "cpu", "overrides": OVERRIDES, "seed": 11})
    manager.cancel()
    with pytest.raises(ConfigError, match="already running"):
        manager.start(settings())
    manager.wait(900)
    assert manager.status()["state"] in ("cancelled", "done")
