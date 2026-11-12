"""The setting search, the one-click pipeline (job, steps, reuse, summary) and its API, on a synthetic world with known player effects."""
from __future__ import annotations

import copy
import json
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
             "model": {"min_class_count": 20},
             "policy": {"min_count": 100, "lambda_grid": [0.0], "cross_eval": False, "replication_members": 2},
             "query": {"check_situations": 6, "check_members": 2}}


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
    assert set(g) == {"r1", "policies", "effect_gains", "actions", "comparisons"} and {p["policy"] for p in g["policies"]} >= {"observed", "dp_trust", "dp_unrestricted"}
    assert set(g["r1"]) == {"median_abs_diff", "max_abs_diff", "start_diff"}                  # numbers only: no pass / fail
    assert g["effect_gains"] == [] and len(g["actions"]) >= 2                                # no controls were chosen, so there is nothing to compare with the no-controls baseline
    assert job["summary"]["model"] == {"kind": "gbm", "controls": {}}
    assert all("detected_share" not in a for a in g["actions"])
    chance = g["comparisons"]["direction"]
    assert chance["n"] > 0 and chance["expected_by_chance"] == pytest.approx(chance["n"] * (1 - chance["confidence"])) and 0 <= chance["found"] <= chance["n"]
    assert job["summary"]["best_params"]["max_depth"] in GRID["max_depth"]
    assert job["summary"]["replication"] == {"members_per_year": 2, "years": [2023, 2024]}      # each year was also trained alone and listed side by side


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
    bad = {"search": {"grid": GRID, "min_class_count": 20}, "model": {"min_class_count": 10_000_000}}
    manager.start({"tier": "smoke", "groups": ["RvR"], "backend": BACKEND, "device": "cpu", "overrides": bad, "seed": 7})
    manager.wait(600)
    job = manager.status()
    assert job["state"] == "error" and "訓練模型" in job["error"]
    assert [s["status"] for s in job["steps"]] == ["done", "error", "pending", "pending", "pending"]


def test_cancel_stops_before_the_next_step(world):
    manager = world.pipeline()
    manager.start({"tier": "smoke", "groups": ["RvR"], "backend": BACKEND, "device": "cpu", "overrides": OVERRIDES, "seed": 11})
    manager.cancel()
    with pytest.raises(ConfigError, match="already running"):
        manager.start(settings())
    manager.wait(900)
    assert manager.status()["state"] in ("cancelled", "done")


def test_the_model_and_its_controls_reach_every_step_and_the_search_can_be_skipped():
    settings_ = pl.normalize_settings({"tier": "smoke", "groups": ["RvR"], "model": "logistic", "controls": {"control_pitcher": "effect", "control_day": "pitcher_game_effect"}, "search": False})
    assert settings_["model"] == "logistic" and settings_["controls"]["control_pitcher"] == "effect" and settings_["search"] is False
    for step in ("search", "model"):
        cfg = pl.step_config(step, settings_, {})
        assert cfg["model"] == "logistic" and cfg["control_pitcher"] == "effect" and cfg["control_day"] == "pitcher_game_effect"
    policy = pl.step_config("policy", settings_, {"model_run_id": 5, "anchor_run_id": 6})
    assert policy["model_run_id"] == 5 and "members" not in policy and "backend" not in policy
    assert pl.step_config("query", settings_, {"policy_run_id": 7, "model_run_id": 5})["package_run_id"] == 5
    with pytest.raises(ConfigError, match="model"):
        pl.normalize_settings({"tier": "fast", "model": "no_such_model"})
    with pytest.raises(ConfigError, match="controls"):
        pl.normalize_settings({"tier": "fast", "controls": {"control_pitcher": "magic"}})


# ------------------------------------------------------------------------------------------------ settings files (full settings mode)
SIMPLE = {"tier": "smoke", "groups": ["RvR"], "model": "logistic", "controls": {"control_pitcher": "effect"}}
STEP_OF = {"search": "search", "model": "train", "anchor": "anchor", "policy": "policy", "query": "query"}


def test_the_full_settings_file_is_what_the_simple_mode_would_run(world):
    doc = pl.full_config(SIMPLE)
    assert doc["format"] == 1 and doc["groups"] == ["RvR"] and doc["years"] == [2023, 2024] and set(doc["steps"]) == set(STEP_OF.values()) and doc["enabled"] == {"search": True, "anchor": True}
    assert pl.validate_document(world, doc) == []
    again = json.loads(json.dumps(doc, ensure_ascii=False))                                  # a file written and read back is the same document
    assert again == doc and pl.validate_document(world, again) == []
    for step in doc["steps"].values():
        assert "scope" not in step and "groups" not in step and "re_scope" not in step
    assert "model_run_id" not in doc["steps"]["policy"] and "anchor_run_id" not in doc["steps"]["policy"] and "policy_run_id" not in doc["steps"]["query"]
    settings_ = pl.normalize_settings(SIMPLE)
    placeholders = {"model_run_id": 0, "anchor_run_id": 0, "policy_run_id": 0}
    for sid, key in STEP_OF.items():                                                         # every value the simple mode sets is written down, with the same value
        cfg = pl.step_config(sid, settings_, placeholders)
        for name, value in cfg.items():
            if name in ("scope", "groups", "re_scope") or name in pl.AUTO_FIELDS.get(sid, ()):
                continue
            assert doc["steps"][key][name] == value, (sid, name)
    from treepolo_mlb_data.research.methods import get_method

    for sid, kind, _ in pl.STEPS:                                                           # and every setting of the method is there: nothing is hidden
        names = {f.name for f in get_method(kind).fields} - {"scope", "groups", "re_scope"} - set(pl.AUTO_FIELDS.get(sid, ()))
        assert names <= set(doc["steps"][STEP_OF[sid]]), (sid, names - set(doc["steps"][STEP_OF[sid]]))


def test_a_bad_settings_file_says_which_setting_of_which_step_is_wrong(world):
    good = pl.full_config(SIMPLE)

    def problems(edit):
        doc = copy.deepcopy(good); edit(doc)
        return pl.validate_document(world, doc)

    e = problems(lambda d: d["steps"]["train"].update(max_depth="deep"))
    assert [(x["step"], x["field"]) for x in e] == [("train", "max_depth")] and "整數" in e[0]["message"]
    e = problems(lambda d: d["steps"]["train"].update(control_survival="batter_and_count", control_batter="none"))
    assert e and e[0]["step"] == "train" and "control_batter" in e[0]["message"]
    e = problems(lambda d: d["steps"]["policy"].update(no_such_setting=1))
    assert [(x["step"], x["field"]) for x in e] == [("policy", "no_such_setting")]
    e = problems(lambda d: d["steps"]["search"].update(scoring=["year"], grid={"no_such": [1]}))
    assert any(x["step"] == "search" for x in e)
    assert any(x["field"] == "format" for x in problems(lambda d: d.update(format=2)))
    assert any(x["field"] == "groups" for x in problems(lambda d: d.update(groups=["XvX"])))
    assert any(x["field"] == "years" for x in problems(lambda d: d.update(years=[])))
    assert any(x["field"] == "enabled" for x in problems(lambda d: d.update(enabled={"train": False})))                  # only the optional steps can be switched off
    assert any(x["step"] == "anchor" for x in problems(lambda d: d["steps"].pop("anchor")))
    assert pl.validate_document(world, ["not", "an", "object"])[0]["field"] == ""
    assert problems(lambda d: d["steps"]["policy"].update(model_run_id=999)) == []                                         # run numbers come from the earlier steps while it runs


def test_the_settings_file_api(world):
    status, body = api.handle_post(world, "/api/research/pipeline/full-config", SIMPLE)
    assert status == 200 and body["file"]["format"] == 1
    status, ok = api.handle_post(world, "/api/research/pipeline/validate", {"file": body["file"]})
    assert status == 200 and ok == {"ok": True, "errors": []}
    broken = copy.deepcopy(body["file"]); broken["steps"]["train"]["learning_rate"] = -1
    status, bad = api.handle_post(world, "/api/research/pipeline/validate", {"file": broken})
    assert status == 200 and not bad["ok"] and bad["errors"][0]["field"] == "learning_rate"
    status, info = api.handle_get(world, "/api/research/pipeline/info", "")
    assert info["step_methods"]["train"]["kind"] == "outcome_model_train" and info["step_methods"]["policy"]["auto_fields"] == ["model_run_id", "anchor_run_id"] and "logistic" in info["models"]
    with pytest.raises(ConfigError, match="not valid"):
        api.handle_post(world, "/api/research/pipeline/start", {"file": broken})                                        # an invalid file never starts


def test_a_pipeline_can_run_from_a_settings_file_with_optional_steps_switched_off(world):
    doc = pl.full_config({"tier": "smoke", "groups": ["RvR"], "model": "logistic"})
    doc["enabled"] = {"search": False, "anchor": False}
    doc["steps"]["train"].update(max_iter=40, min_class_count=20, cold_folds=2, bagging_reps=2, bootstrap_reps=20, compare_baseline=False, effect_sample=2000, control_pitcher="effect")
    doc["steps"]["policy"].update(min_count=100, lambda_grid=[0.0], cross_eval=False, replication_members=0)
    doc["steps"]["query"].update(check_situations=6, check_members=2)
    manager = world.pipeline()
    manager.start({"file": doc})
    manager.wait(900)
    job = manager.status()
    assert job["state"] == "done" and job["error"] is None and job["settings"]["tier"] == "custom"
    by_id = {s["id"]: s for s in job["steps"]}
    assert by_id["search"]["run_id"] is None and "略過" in by_id["search"]["message"] and by_id["anchor"]["run_id"] is None and "略過" in by_id["anchor"]["message"]
    assert all(by_id[k]["status"] == "done" and by_id[k]["run_id"] for k in ("model", "policy", "query"))
    model_extras = world.store.load_result(by_id["model"]["run_id"])["extras"]
    assert model_extras["model"] == "logistic" and model_extras["controls"]["control_pitcher"] == "effect"                # the file's values are what ran
    assert world.store.load_result(by_id["policy"]["run_id"])["extras"]["model_run_id"] == by_id["model"]["run_id"]       # and the run numbers were passed on
    assert job["summary"]["model"]["controls"] == {"control_pitcher": "effect"}
