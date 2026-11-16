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


def test_the_cross_season_rows_come_first_carry_intervals_and_the_notes_are_there(service, result):
    """The cross-season numbers (every bagged member chooses on one season and is judged on the other) are listed before the same-data numbers, which contain the optimism of the selection."""

    rows = [r for r in section(service, result, "Policy value") if r["group"] == "RvR"]
    kinds = [r["kind"] for r in rows]
    first_same = next(i for i, k in enumerate(kinds) if k == "same data")
    assert first_same > 0 and all(k.startswith("cross") for k in kinds[:first_same]) and all(k == "same data" for k in kinds[first_same:])
    cross = rows[:first_same]
    assert all(r["lo"] is not None and r["hi"] is not None and r["lo"] <= r["saving_pa"] <= r["hi"] for r in cross)                 # two members: an interval
    res = service.store.load_result(result["id"])
    assert "同資料" in next(s["title"] for s in res["sections"] if s["title"].startswith("Policy value"))
    notes = {n["control"]: n for n in res["extras"]["notes"]}
    assert {"same_data", "upper_bound"} <= set(notes) and "上限" in notes["upper_bound"]["zh"] and "upper bound" in notes["upper_bound"]["en"] and "樂觀" in notes["same_data"]["zh"]
    assert res["extras"]["pitcher_engine"]["groups"]["RvR"]["enabled"] is False                                                     # boosted trees have no pitcher x pitch-type effects


def test_one_member_gives_the_cross_season_rows_no_interval(service, model_run, patched):
    run = service.run("pitch_policy", {**BASE, "model_run_id": model_run["id"], "cross_members": 1, "replication_members": 0, "policies": ["observed", "dp_trust"], "lambda_grid": [0.0]})["run"]
    cross = [r for r in section(service, run, "Policy value") if r["kind"].startswith("cross")]
    assert cross and all(r["saving_pa"] is not None and r["lo"] is None and r["hi"] is None for r in cross)                         # no spread, no interval (not the estimate twice)


@pytest.fixture(scope="module")
def logistic_run(service, w1):
    pytest.importorskip("torch")
    mp = pytest.MonkeyPatch()
    world, cols = w1
    mp.setattr(omm, "fetch_columns", lambda ctx, config, groups, years, re_by_state=None: cols)
    try:
        run = service.run("outcome_model_train", {**MODEL, "model": "logistic", "control_pitcher_pitch": "within", "control_batter": "effect", "bagging_reps": 2, "max_iter": 300, "device": "cpu",
                                                   "cold_by": [], "compare_baseline": False, "cross_check": False})["run"]
    finally:
        mp.undo()
    return run


def test_the_members_of_the_trained_model_say_how_each_fit_ended(service, logistic_run):
    rows = section(service, logistic_run, "Members")
    assert [r["member"] for r in rows] == [0, 1, 2] and rows[0]["kind"].startswith("main fit") and all(r["converged"] == 1 and r["sweeps"] >= 1 for r in rows)
    fit = section(service, logistic_run, "Fit summary")
    assert fit[0]["n_iter"] == rows[0]["sweeps"] and fit[0]["hit_cap"] == 0                                                           # the fit row reports the main fit


def test_the_per_pitcher_engine_is_in_the_result_with_its_coverage(service, w1, logistic_run):
    from treepolo_mlb_data.research import pitcher_engine as pe

    mp = pytest.MonkeyPatch()
    world, cols_all = w1
    n_all = len(cols_all["game_pk"])
    keep = np.isin(cols_all["pitcher"], np.unique(cols_all["pitcher"])[:5])                                                              # five pitchers: the engine is solved once for each
    cols = {k: (v[keep] if isinstance(v, np.ndarray) and len(v) == n_all else v) for k, v in cols_all.items()}
    mp.setattr(pm, "fetch_columns", lambda ctx, config, groups, years, re_by_state=None: cols)
    mp.setattr(pm, "get_re_table", lambda ctx, scope: FakeRe(world.env.re_cnt))
    ids, pa, _ = pe.plate_appearances(cols)
    min_pa = int(pa.min())
    try:
        run = service.run("pitch_policy", {**BASE, "model_run_id": logistic_run["id"], "cross_eval": False, "replication_members": 0, "policies": ["observed", "dp_trust"], "lambda_grid": [0.0],
                                            "pitcher_min_pa": min_pa})["run"]
    finally:
        mp.undo()
    summary = section(service, run, "Pitcher engine 逐投手引擎彙總"); cov = section(service, run, "Pitcher engine coverage"); detail = section(service, run, "Pitcher engine detail")
    assert {r["policy"] for r in summary} == {"observed", "dp_trust"} and len(cov) == 1 and cov[0]["pitchers_used"] == len(detail) == len(ids) == 5 and 0 < cov[0]["pa_share"] <= 1
    assert cov[0]["pitchers_total"] == 5 and cov[0]["min_pa"] == min_pa
    trust = next(r for r in summary if r["policy"] == "dp_trust")
    assert trust["saving_pa"] == pytest.approx(sum(r["pa"] * r["saving_dp_trust"] for r in detail) / sum(r["pa"] for r in detail), rel=1e-6, abs=1e-8)
    res = service.store.load_result(run["id"])
    assert res["extras"]["pitcher_engine"]["groups"]["RvR"]["enabled"] and "pitcher_engine" in {n["control"] for n in res["extras"]["notes"]}
    check = section(service, run, "Pitcher engine check")                                                                              # every pitcher against what he really did, and the weighted row
    assert len(check) == 6 and check[-1]["pitcher"].startswith("ALL") and all(r["start_value_actual"] is not None and r["hr_actual"] is not None for r in check)
    # the direct check and the score read what the run stored (the score uses the same model as a stand-in for a model of another season)
    from treepolo_mlb_data.research import policy_check_methods as pcm, policy_score_methods as psm

    mp2 = pytest.MonkeyPatch()
    for module in (pcm, psm):
        mp2.setattr(module, "fetch_columns", lambda ctx, config, groups, years, re_by_state=None: cols)
        mp2.setattr(module, "get_re_table", lambda ctx, scope: FakeRe(world.env.re_cnt))
    mp2.setattr(psm.pm, "build_environment", pm.build_environment)
    try:
        checked = service.run("policy_check", {"scope": SCOPE, "re_scope": SCOPE, "policy_run_id": run["id"], "groups": ["RvR"]})["run"]
        scored = service.run("policy_score", {"scope": SCOPE, "re_scope": SCOPE, "policy_run_id": run["id"], "model_run_id": logistic_run["id"], "groups": ["RvR"]})["run"]
    finally:
        mp2.undo()
    assert {r["engine"] for r in section(service, checked, "Direct check")} == {"league", "pitcher"}
    score = section(service, scored, "Score")
    league = {r["policy"]: r for r in score if r["engine"] == "league"}; pit = {r["policy"]: r for r in score if r["engine"] == "pitcher"}
    assert set(pit) == {"dp_trust"} and {"dp_trust"} <= set(league) and all(np.isfinite(r["saving_pa"]) and r["pitchers"] == 5 for r in pit.values())
    assert league["dp_trust"]["saving_per_team_season"] == pytest.approx(league["dp_trust"]["saving_pa"] * 6200)


def test_the_direct_check_compares_followed_and_not_followed_pitches_with_game_clustered_intervals():
    from treepolo_mlb_data.research.policy_check_methods import compare

    rng = np.random.default_rng(1)
    n = 6000
    game = rng.integers(0, 300, n)
    followed = rng.random(n) < 0.4
    value = rng.normal(0.0, 5.0, n) + game % 7 * 0.2 - 1.5 * followed                                   # followed pitches are worth 1.5 runs per 100 pitches less
    out = compare(value, followed, game, 1.96)
    assert out["n_followed"] == int(followed.sum()) and out["n_not_followed"] == int((~followed).sum()) and out["share_followed"] == pytest.approx(followed.mean())
    assert out["diff"] == pytest.approx(value[followed].mean() - value[~followed].mean()) and abs(out["diff"] + 1.5) < 0.5 and out["lo"] < out["diff"] < out["hi"] and 0.1 < out["hi"] - out["lo"] < 1.5
    assert compare(value[:1], followed[:1], game[:1], 1.96)["diff"] is None                              # too few pitches: the means, no interval


def test_the_direct_check_run_lists_every_count_for_the_league_engine(service, result, w1, monkeypatch):
    from treepolo_mlb_data.research import policy_check_methods as pcm

    world, cols = w1
    monkeypatch.setattr(pcm, "fetch_columns", lambda ctx, config, groups, years, re_by_state=None: cols)
    monkeypatch.setattr(pcm, "get_re_table", lambda ctx, scope: FakeRe(world.env.re_cnt))
    run = service.run("policy_check", {"scope": SCOPE, "re_scope": SCOPE, "policy_run_id": result["id"], "groups": ["RvR"]})["run"]
    rows = section(service, run, "Direct check")
    assert {r["engine"] for r in rows} == {"league"} and {r["policy"] for r in rows} == {"dp_trust", "dp_unrestricted"}
    cells = [r for r in rows if r["policy"] == "dp_trust"]
    assert [r["count"] for r in cells][0] == "all" and len(cells) == 13
    total = cells[0]
    assert total["n_followed"] + total["n_not_followed"] == section(service, run, "Coverage")[0]["pitches"] and 0 < total["share_followed"] < 1
    assert sum(r["n_followed"] + r["n_not_followed"] for r in cells[1:]) == total["n_followed"] + total["n_not_followed"]       # the 12 counts add up to all
    assert "upper_bound" in {n["control"] for n in service.store.load_result(run["id"])["extras"]["notes"]}


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
