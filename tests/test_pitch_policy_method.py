from __future__ import annotations

import numpy as np
import pytest

from state_fixtures import make_state_db
from treepolo_mlb_data.config import AppConfig
from treepolo_mlb_data.research import anchor_methods as am, gbm, outcome_model_methods as omm, policy_core as pc, policy_methods as pm, policy_worlds as pw
from treepolo_mlb_data.research.config_schema import ConfigError
from treepolo_mlb_data.research.profiles import PITCH_TYPES
from treepolo_mlb_data.research.service import ResearchService

SCOPE = {"game_years": [2024], "game_types": ["R"]}
BACKEND = "xgboost" if gbm.xgboost_available() else "hgb"
MODEL_SCOPE = {"game_years": [2023, 2024], "game_types": ["R"]}
# the model behind the situation table: trained on the same simulated pitches (the patched fetch hands them over), bagged members of boosted trees, no player effects
MODEL = {"scope": MODEL_SCOPE, "groups": ["RvR"], "model": "gbm", "backend": BACKEND, "device": "cpu", "max_depth": 4, "min_leaf": 1000, "learning_rate": 0.15, "max_iter": 50,
         "bagging_reps": 4, "split": "cold_only", "cold_by": ["pitcher"], "cold_folds": 2, "min_class_count": 5, "bootstrap_reps": 20, "compare_baseline": False, "replication_members": 0,
         "save_package": True}
BASE = {"scope": SCOPE, "re_scope": SCOPE, "groups": ["RvR"], "cross_members": 2, "replication_members": 2, "min_count": 100, "lambda_grid": [0.0, 1.0]}


class FakeRe:
    def __init__(self, re_cnt):
        self.re_by_state = {b * 1000 + k * 100 + o * 10 + B: float(re_cnt[b, k, o, B]) for b in range(4) for k in range(3) for o in range(3) for B in range(8)}


@pytest.fixture(scope="module")
def service(tmp_path_factory):
    data = tmp_path_factory.mktemp("policy") / "data"; data.mkdir()
    make_state_db(data / "statcast.sqlite3")
    return ResearchService(AppConfig(data_dir=str(data)), None)


@pytest.fixture(scope="module")
def w1(service):
    world = pw.build_world("W1")
    return world, pw.simulate_pitches(world, 70_000, 4)


@pytest.fixture()
def patched(monkeypatch, w1):
    world, cols = w1
    for module in (pm, omm):
        monkeypatch.setattr(module, "fetch_columns", lambda ctx, config, groups, years, re_by_state=None: cols)
    monkeypatch.setattr(pm, "get_re_table", lambda ctx, scope: FakeRe(world.env.re_cnt))


@pytest.fixture(scope="module")
def model_run(service, w1):
    mp = pytest.MonkeyPatch()
    world, cols = w1
    mp.setattr(omm, "fetch_columns", lambda ctx, config, groups, years, re_by_state=None: cols)
    try:
        run = service.run("outcome_model_train", MODEL)["run"]
    finally:
        mp.undo()
    return run


@pytest.fixture(scope="module")
def result(service, w1, model_run):
    mp = pytest.MonkeyPatch()
    world, cols = w1
    mp.setattr(pm, "fetch_columns", lambda ctx, config, groups, years, re_by_state=None: cols)
    mp.setattr(pm, "get_re_table", lambda ctx, scope: FakeRe(world.env.re_cnt))
    try:
        run = service.run("pitch_policy", {**BASE, "model_run_id": model_run["id"]})["run"]
    finally:
        mp.undo()
    return run


def section(service, run, prefix):
    res = service.store.load_result(run["id"])
    return next(s["rows"] for s in res["sections"] if s["title"].startswith(prefix))


def test_run_reproduces_run_expectancy_and_finds_the_slider(service, result, w1):
    r1 = section(service, result, "R1")
    states = next(r for r in r1 if r["item"].startswith("states"))
    assert states["n_states"] > 30 and states["median_abs_diff"] < 0.01 and states["max_abs_diff"] < 0.05           # plan 4.1
    start = next(r for r in r1 if r["item"].startswith("start"))
    assert start["median_abs_diff"] < 0.005
    values = {(r["policy"], r["kind"]): r for r in section(service, result, "Policy value")}
    same = values[("dp_trust", "same data")]
    assert same["lo"] > 0 and same["saving_pa"] > 0.004 and same["saving_per_team_season"] == pytest.approx(same["saving_pa"] * 6200)
    assert values[("dp_unrestricted", "same data")]["saving_pa"] >= same["saving_pa"]
    cross = [r for r in section(service, result, "Policy value") if r["kind"].startswith("cross") and r["policy"] == "dp_trust"][0]
    assert 0.5 * same["saving_pa"] < cross["saving_pa"] < 1.5 * same["saving_pa"]
    marg = {r["action"]: r for r in section(service, result, "Marginal value")}
    assert marg["SL"]["advantage"] < -0.01 and marg["SL"]["adv_hi"] < 0 and marg["SL"]["marginal"] < 0
    assert marg["FF"]["advantage"] > 0
    assert set(marg) == {"FF", "SL", "CH"}
    assert all("detected_share" not in r for r in marg.values())                  # numbers and intervals only: no detection share


def test_comparison_counts_and_per_year_replication_are_listed(service, result):
    res = service.store.load_result(result["id"])
    direction = section(service, result, "Direction map")
    assert all("not_detected" not in r for r in direction)
    lo = np.array([r["adv_lo"] for r in direction], dtype=float); hi = np.array([r["adv_hi"] for r in direction], dtype=float)
    ok = np.isfinite(lo) & np.isfinite(hi)
    chance = res["extras"]["comparisons"]["direction"]["RvR"]
    assert chance["n"] == int(ok.sum()) and chance["found"] == int((ok & ((lo > 0) | (hi < 0))).sum())              # N and M are counted from the listed cells, not written in
    assert chance["expected_by_chance"] == pytest.approx(chance["n"] * (1 - chance["confidence"])) and chance["confidence"] == 0.99
    assert res["extras"]["comparisons"]["marginal"]["RvR"]["n"] == 3
    rep = section(service, result, "Replication")
    years = sorted({r["year"] for r in rep})
    assert len(years) >= 2 and res["extras"]["replication"] == {"members_per_year": 2, "years": years}
    per_action = [r for r in rep if r["context"] == "ALL"]
    assert {(r["year"], r["action"]) for r in per_action} == {(y, a) for y in years for a in ("FF", "SL", "CH")}      # every year has every action side by side
    assert all(r["adv_lo"] <= r["advantage"] <= r["adv_hi"] for r in per_action if r["advantage"] is not None)
    assert any(r["context"] != "ALL" for r in rep)                                                                      # the direction cells are repeated per year too


def test_risk_frontier_trades_runs_for_home_runs(service, result):
    risk = {r["lambda"]: r for r in section(service, result, "Risk frontier")}
    assert risk[1.0]["hr_probability"] <= risk[0.0]["hr_probability"] + 1e-6
    assert risk[1.0]["expected_runs"] >= risk[0.0]["expected_runs"] - 1e-6


def test_situation_table_artifact_round_trips(service, result):
    art = service.store.load_artifact(result["id"], "situation_table_RvR")
    assert art is not None
    arrays = pm.unpack_arrays(art["rows"])
    shape = pc.Shape()
    assert arrays["q"].shape == (shape.size, 9) and arrays["p_outcome"].shape == (shape.size, 9, 8)
    assert np.allclose(arrays["p_outcome"].sum(axis=2), 1, atol=1e-4)
    unsupported = ~arrays["support"] & arrays["support"].any(axis=1, keepdims=True)      # a state with no supported action keeps the observed behaviour (every policy plays it there)
    assert (arrays["dp_trust"][unsupported] == 0).all()
    assert np.allclose(arrays["dp_trust"].sum(axis=1), 1.0, atol=1e-6)


def test_same_settings_are_reused_and_errors_are_clear(service, result, model_run, patched):
    again = service.run("pitch_policy", {**BASE, "model_run_id": model_run["id"]})
    assert again["reused"] and again["run"]["id"] == result["id"]
    with pytest.raises(ConfigError):
        service.run("pitch_policy", {**BASE, "model_run_id": model_run["id"], "policies": ["dp_trust"]})
    with pytest.raises(ConfigError):
        service.run("pitch_policy", {**BASE, "model_run_id": model_run["id"], "lambda_grid": [-1]})
    with pytest.raises(ConfigError, match="model_run_id"):
        service.run("pitch_policy", BASE)                                                       # the model must be trained first
    with pytest.raises(ConfigError, match="outcome_model_train"):
        service.run("pitch_policy", {**BASE, "model_run_id": result["id"]})                    # a run of another kind is refused
    with pytest.raises(ConfigError):
        service.run("pitch_policy", {**BASE, "model_run_id": model_run["id"], "backend": "hgb"})      # the model is not trained here any more: no backend setting
    run = service.run("pitch_policy", {**BASE, "model_run_id": model_run["id"], "scope": {"game_years": [2023, 2025], "game_types": ["R"]}})["run"]
    assert run["status"] == "success"                                                         # no reserved-season rule: any years can be used


def test_environment_recovers_the_world_values(w1):
    world, cols = w1
    env = pm.build_environment(cols, FakeRe(world.env.re_cnt).re_by_state, world.shape)
    assert np.allclose(env.re_pa, world.env.re_pa, atol=1e-9) and np.allclose(env.re_cnt, world.env.re_cnt)
    seen = np.isin(np.arange(9), [PITCH_TYPES.index(t) for t in ("FF", "SL", "CH")])
    assert np.abs(env.jbip[seen] - world.env.jbip[seen]).max() < 0.03
    assert np.abs(env.start / env.start.sum() - world.env.start / world.env.start.sum()).max() < 0.01


@pytest.fixture(scope="module")
def anchored(service, w1, model_run):
    """An action_effects run on the same world and a pitch_policy run that uses it as its anchor."""

    mp = pytest.MonkeyPatch()
    world, cols = w1
    for module in (pm, am):
        mp.setattr(module, "fetch_columns", lambda ctx, config, groups, years, re_by_state=None: cols)
        mp.setattr(module, "get_re_table", lambda ctx, scope: FakeRe(world.env.re_cnt))
    try:
        anchor = service.run("action_effects", {"scope": SCOPE, "re_scope": SCOPE, "groups": ["RvR"]})["run"]
        policy = service.run("pitch_policy", {**BASE, "model_run_id": model_run["id"], "anchor_run_id": anchor["id"], "cross_eval": False})["run"]
    finally:
        mp.undo()
    return anchor, policy


def test_action_effects_run_has_the_anchor_tables(service, anchored):
    anchor, _ = anchored
    contrasts = section(service, anchor, "Action contrasts")
    sl = next(r for r in contrasts if r["design"] == "S3" and r["action"] == "SL" and r["class"] == "whiff" and r["phase"] == "even")
    assert sl["lo"] > 0.02
    fit = section(service, anchor, "Fit summary")
    assert {r["design"] for r in fit} == {"S3", "S4"} and max(r["outside_01_share"] for r in fit if r["design"] == "S3") < 0.01       # (S4 cells are one or two pitches in this world, so only S3 is checked)
    jb = section(service, anchor, "J_BIP")
    assert len(jb) == 3 * 24 and {r["action"] for r in jb} == {"FF", "SL", "CH"}
    arrays = pm.unpack_arrays(service.store.load_artifact(anchor["id"], "anchor_RvR")["rows"])
    assert arrays["jbip"].shape == (9, 3, 8) and arrays["eff_S3"].shape == (3, 9, 8)


def test_policy_with_anchor_reports_consistency_and_direction_map(service, anchored):
    _, policy = anchored
    anchor_sec = section(service, policy, "Anchor 錨點對照彙總")
    assert {r["design"] for r in anchor_sec} == {"S3", "S4"} and all("passes" not in r for r in anchor_sec)             # numbers only: no pass / fail
    assert all(r["cells"] > 0 and 0 <= r["same_sign_share"] <= 1 and r["outside_anchor_interval"] <= r["cells"] for r in anchor_sec)
    cells = section(service, policy, "Anchor cells")
    assert {r["design"] for r in cells} == {"S3", "S4"} and sum(1 for r in cells if r["design"] == "S3") == next(r["cells"] for r in anchor_sec if r["design"] == "S3")      # every cell is listed
    assert all(r["anchor_lo"] <= r["anchor_effect"] <= r["anchor_hi"] and r["difference"] == pytest.approx(r["model_effect"] - r["anchor_effect"], abs=1e-6) for r in cells)
    anchor_chance = service.store.load_result(policy["id"])["extras"]["comparisons"]["anchor"]["RvR"]
    assert anchor_chance["what"] == "outside_anchor_interval" and anchor_chance["n"] == sum(r["cells"] for r in anchor_sec) and anchor_chance["found"] == sum(r["outside_anchor_interval"] for r in anchor_sec)
    direction = section(service, policy, "Direction map")
    sl = [r for r in direction if r["action"] == "SL" and r["context"].startswith("1-1 ")]
    assert sl and all(r["marginal"] < 0 for r in sl)
    assert {r["action"] for r in direction} == {"FF", "SL", "CH"} and all(r["context"].startswith("0-0 | none") for r in direction if r["balls"] == 0 and r["strikes"] == 0)


def test_anchor_must_be_an_action_effects_run(service, result, model_run, patched):
    with pytest.raises(ConfigError, match="action_effects"):
        service.run("pitch_policy", {**BASE, "model_run_id": model_run["id"], "anchor_run_id": result["id"]})
    with pytest.raises(ConfigError):
        service.run("pitch_policy", {**BASE, "model_run_id": model_run["id"], "anchor_run_id": 9999})


def test_the_table_records_which_model_and_controls_it_was_computed_under(service, result, model_run):
    extras = service.store.load_result(result["id"])["extras"]
    assert extras["model_run_id"] == model_run["id"] and extras["model"] == "gbm" and extras["model_controls"]["control_pitcher"] == "none"
