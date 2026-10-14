from __future__ import annotations

import numpy as np
import pytest

from state_fixtures import make_state_db
from treepolo_mlb_data.config import AppConfig
from treepolo_mlb_data.research import gbm, policy_core as pc, policy_methods as pm, policy_worlds as pw
from treepolo_mlb_data.research.config_schema import ConfigError
from treepolo_mlb_data.research.profiles import PITCH_TYPES
from treepolo_mlb_data.research.service import ResearchService

SCOPE = {"game_years": [2024], "game_types": ["R"]}
BACKEND = "xgboost" if gbm.xgboost_available() else "hgb"
BASE = {"scope": SCOPE, "re_scope": SCOPE, "groups": ["RvR"], "members": 4, "cross_members": 2, "backend": BACKEND, "device": "cpu", "max_depth": 4, "min_leaf": 1000,
        "learning_rate": 0.15, "max_iter": 50, "min_count": 100, "lambda_grid": [0.0, 1.0], "min_class_count": 5}


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
    monkeypatch.setattr(pm, "fetch_columns", lambda ctx, config, groups, years, re_by_state=None: cols)
    monkeypatch.setattr(pm, "get_re_table", lambda ctx, scope: FakeRe(world.env.re_cnt))


@pytest.fixture(scope="module")
def result(service, w1):
    mp = pytest.MonkeyPatch()
    world, cols = w1
    mp.setattr(pm, "fetch_columns", lambda ctx, config, groups, years, re_by_state=None: cols)
    mp.setattr(pm, "get_re_table", lambda ctx, scope: FakeRe(world.env.re_cnt))
    try:
        run = service.run("pitch_policy", BASE)["run"]
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
    assert marg["SL"]["advantage"] < -0.01 and marg["SL"]["adv_hi"] < 0 and marg["SL"]["marginal"] < 0 and marg["SL"]["detected_share"] > 0.2
    assert marg["FF"]["advantage"] > 0
    assert set(marg) == {"FF", "SL", "CH"}


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
    assert (arrays["dp_trust"][~arrays["support"]] == 0).all()


def test_same_settings_are_reused_and_errors_are_clear(service, result, patched):
    again = service.run("pitch_policy", BASE)
    assert again["reused"] and again["run"]["id"] == result["id"]
    with pytest.raises(ConfigError):
        service.run("pitch_policy", {**BASE, "policies": ["dp_trust"]})
    with pytest.raises(ConfigError):
        service.run("pitch_policy", {**BASE, "lambda_grid": [-1]})
    with pytest.raises(ConfigError):
        service.run("pitch_policy", {**BASE, "backend": "hgb", "device": "cuda"})
    with pytest.raises(ConfigError, match="reserved"):
        service.run("pitch_policy", {**BASE, "scope": {"game_years": [2023, 2025], "game_types": ["R"]}})


def test_environment_recovers_the_world_values(w1):
    world, cols = w1
    env = pm.build_environment(cols, FakeRe(world.env.re_cnt).re_by_state, world.shape)
    assert np.allclose(env.re_pa, world.env.re_pa, atol=1e-9) and np.allclose(env.re_cnt, world.env.re_cnt)
    seen = np.isin(np.arange(9), [PITCH_TYPES.index(t) for t in ("FF", "SL", "CH")])
    assert np.abs(env.jbip[seen] - world.env.jbip[seen]).max() < 0.03
    assert np.abs(env.start / env.start.sum() - world.env.start / world.env.start.sum()).max() < 0.01
