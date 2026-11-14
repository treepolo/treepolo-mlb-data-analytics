"""Outcome models (logistic, boosted trees) with the bias controls: the solver, the designs, the interface (population / specific, storage, refusals) and the research method.

Tests marked ``report`` run a known-answer world (the confounded worlds W4, W5, W7) and only write numbers to docs/research/known_answer_report.json."""
from __future__ import annotations

import numpy as np
import pytest

from report_helper import record
from treepolo_mlb_data.config import AppConfig
from treepolo_mlb_data.research import gbm, policy_worlds as pw
from treepolo_mlb_data.research import outcome_models as om
from treepolo_mlb_data.research.config_schema import ConfigError
from treepolo_mlb_data.research.outcome_models import controls as ctl, design as dz
from treepolo_mlb_data.research.outcome_models.softmax import fit_softmax, softmax_rows
from treepolo_mlb_data.research.policy_core import OUTCOMES
from treepolo_mlb_data.research.service import ResearchService
from treepolo_mlb_data.research.synthetic import simulate_players, write_player_world_db

HAS_XGB = gbm.xgboost_available()
SMALL = dict(max_iter=60, l2=1.0, bagging_reps=1, seed=3)


# ------------------------------------------------------------------------------------------------ the solver
def test_softmax_matches_scikit_learn_when_every_column_has_the_same_penalty():
    from scipy import sparse
    from sklearn.linear_model import LogisticRegression

    rng = np.random.default_rng(0)
    x = rng.standard_normal((1500, 5)); logits = x @ rng.standard_normal((5, 3)); p = softmax_rows(logits)
    y = np.array([rng.choice(3, p=row) for row in p])
    ours = fit_softmax(sparse.csr_matrix(x), y, 3, np.full(5, 2.0), max_iter=500)
    theirs = LogisticRegression(C=0.5, max_iter=1000).fit(x, y)                       # sum of NLL + 0.5 * (1 / C) * ||W||^2: the same objective as penalty 2
    a = softmax_rows(x @ ours.coef + ours.intercept); b = theirs.predict_proba(x)
    assert np.abs(a - b).max() < 0.02


def test_softmax_penalty_per_column_offset_and_weights():
    from scipy import sparse

    rng = np.random.default_rng(1)
    x = rng.standard_normal((800, 3)); y = rng.integers(0, 3, 800)
    loose = fit_softmax(sparse.csr_matrix(x), y, 3, np.array([0.01, 0.01, 0.01]), max_iter=300)
    tight = fit_softmax(sparse.csr_matrix(x), y, 3, np.array([1e9, 1e9, 1e9]), max_iter=300)
    assert np.abs(tight.coef).max() < 1e-4 < np.abs(loose.coef).max()                    # a huge penalty pins the effect at 0
    mixed = fit_softmax(sparse.csr_matrix(x), y, 3, np.array([1e9, 0.01, 0.01]), max_iter=300)
    assert np.abs(mixed.coef[0]).max() < 1e-4 and np.abs(mixed.coef[1:]).max() > 1e-4      # each column has its own penalty
    # an offset that already is the truth leaves nothing for the coefficients
    truth = rng.standard_normal((800, 3)); yy = np.array([rng.choice(3, p=r) for r in softmax_rows(truth)])
    off = fit_softmax(sparse.csr_matrix(x), yy, 3, np.full(3, 50.0), offset=truth, max_iter=300)
    assert np.abs(off.coef).max() < 0.2
    w = np.where(y == 0, 5.0, 1.0)
    weighted = fit_softmax(sparse.csr_matrix(np.zeros((800, 1))), y, 3, np.array([1.0]), weights=w, max_iter=100)
    freq = np.bincount(y, weights=w) / w.sum()
    assert np.allclose(softmax_rows(weighted.intercept[None, :])[0], freq, atol=0.01)


# ------------------------------------------------------------------------------------------------ designs
def _world(kind, n_pa=6000, seed=2):
    w = pw.build_world(kind)
    return w, pw.simulate_pitches(w, n_pa, seed)


def test_effect_index_gives_unseen_units_no_column():
    idx = dz.EffectIndex(np.array([5, 9, 9, 12]))
    assert len(idx) == 3 and idx.lookup(np.array([9, 4, 12, 99])).tolist() == [1, -1, 2, -1]
    cols = {"game_pk": np.array([1, 1, 2]), "pitcher": np.array([5, 9, 9]), "batter": np.array([1, 1, 2]), "pitch_type": np.array(["FF", "SL", "FF"]), "balls": np.zeros(3), "strikes": np.zeros(3)}
    keys = {g: dz.effect_keys(cols, g) for g in ctl.EFFECT_GROUPS}
    assert keys["pitcher"].tolist() == [5, 9, 9] and len(set(keys["pitcher_pitch"].tolist())) == 3 and len(set(keys["pitcher_game"].tolist())) == 3
    indexes = dz.fit_effect_indexes(cols, ["pitcher", "pitcher_pitch"])
    mat, spans = dz.effect_matrix(cols, indexes, ["pitcher", "pitcher_pitch"])
    assert mat.shape == (3, len(indexes["pitcher"]) + len(indexes["pitcher_pitch"])) and (mat.sum(axis=1) == 2).all() and spans["pitcher_pitch"][0] == len(indexes["pitcher"])
    unseen = dict(cols, pitcher=np.array([5, 77, 9]))
    mat2, _ = dz.effect_matrix(unseen, indexes, ["pitcher", "pitcher_pitch"])
    assert mat2[1].sum() == 0                                                              # a pitcher the model has not seen has no effect column


def test_situation_design_has_one_active_column_per_block():
    w, cols = _world("W0", 500)
    mat, names = dz.situation_design(cols)
    assert mat.shape == (len(cols["game_pk"]), len(names)) and (np.asarray(mat.sum(axis=1)).ravel() == 12).all()


def test_effect_groups_follow_the_controls_and_include_the_pitchers_overall_effect():
    assert ctl.effect_groups(ctl.default_controls()) == {}
    g = ctl.effect_groups({**ctl.default_controls(), "control_pitcher_pitch": "effect"})
    assert set(g) == {"pitcher", "pitcher_pitch"} and not any(g.values())                    # shrunk towards the pitcher's overall effect first
    within = ctl.effect_groups({**ctl.default_controls(), "control_pitcher": "within"})
    assert within == {"pitcher": True}
    day = ctl.effect_groups({**ctl.default_controls(), "control_day": "within"})
    assert day == {"pitcher": False, "pitcher_game": True}                                   # the day effects are not shrunk; the pitcher's overall effect still is
    assert ctl.effect_groups({**ctl.default_controls(), "control_day": "pitcher_game_effect"}) == {"pitcher": False, "pitcher_game": False}
    day_pitch = ctl.effect_groups({**ctl.default_controls(), "control_day_pitch": "within"})
    assert day_pitch == {"pitcher": False, "pitcher_game": True, "pitcher_game_pitch": True}
    assert "within" in {f.name: f for f in ctl.CONTROL_FIELDS}["control_day"].choices and "within" in {f.name: f for f in ctl.CONTROL_FIELDS}["control_day_pitch"].choices
    for name in ("control_pitcher", "control_pitcher_pitch", "control_day", "control_day_pitch"):                       # every control that can compare only inside the unit says what auto does not do
        field = {f.name: f for f in ctl.CONTROL_FIELDS}[name]
        assert "同組內比較" in field.help_zh and "random effects" in field.help_en and "within" in field.help_en
    full = ctl.effect_groups({**ctl.default_controls(), "control_day_pitch": "effect", "control_survival": "batter_and_count", "control_batter": "effect"})
    assert {"pitcher", "pitcher_game", "pitcher_game_pitch", "batter", "batter_count"} <= set(full)


def test_impossible_control_combinations_are_refused_with_a_reason():
    with pytest.raises(ConfigError, match="control_batter"):
        ctl.validate_controls({**ctl.default_controls(), "control_survival": "batter_and_count"}, model="logistic")
    with pytest.raises(ConfigError, match="hgb"):
        ctl.validate_controls({**ctl.default_controls(), "control_pitcher": "effect"}, model="gbm", backend="hgb")
    ctl.validate_controls({**ctl.default_controls(), "control_pitcher": "descriptors"}, model="gbm", backend="hgb")           # descriptors need no offset
    with pytest.raises(ConfigError, match="unknown model"):
        om.get_model_class("no_such_model")
    assert {"logistic", "gbm"} <= set(om.model_kinds())


# ------------------------------------------------------------------------------------------------ the models
@pytest.fixture(scope="module")
def w4():
    return _world("W4", 30_000, 5)


def _fit(kind, cols, **settings):
    model = om.new_model(kind)
    return model.fit(cols, None, {**SMALL, **({"backend": "xgboost" if HAS_XGB else "hgb", "device": "cpu", "max_depth": 3, "min_leaf": 100} if kind == "gbm" else {}), **settings})


def test_logistic_population_and_specific_agree_for_a_player_the_model_has_not_seen(w4):
    w, cols = w4
    model = _fit("logistic", cols, control_pitcher="effect", control_batter="effect")
    rows = {k: v[:400] for k, v in cols.items()}
    new = dict(rows, pitcher=np.full(400, 987654), batter=np.full(400, 876543))
    assert np.allclose(model.predict_proba(new, effects="specific"), model.predict_proba(new, effects="population"), atol=1e-12)
    seen = model.predict_proba(rows, effects="specific"); pop = model.predict_proba(rows, effects="population")
    assert np.abs(seen - pop).max() > 1e-3                                                      # a pitcher the model knows does differ from the league average
    assert np.allclose(seen.sum(axis=1), 1) and model.classes == list(OUTCOMES)


@pytest.mark.skipif(not HAS_XGB, reason="xgboost not installed")
def test_gbm_effects_through_the_offset_and_storage_round_trip(w4):
    w, cols = w4
    model = _fit("gbm", cols, control_pitcher="effect", bagging_reps=2)
    rows = {k: v[:300] for k, v in cols.items()}
    new = dict(rows, pitcher=np.full(300, 987654))
    assert np.allclose(model.predict_proba(new, effects="specific"), model.predict_proba(new, effects="population"), atol=1e-6)
    assert model.predict_members(rows, effects="specific").shape == (2, 300, len(OUTCOMES))
    again = om.OutcomeModel.from_bytes(model.to_bytes())
    assert np.allclose(again.predict_proba(rows, effects="specific"), model.predict_proba(rows, effects="specific"), atol=1e-7)
    assert model.info["effects"]["pitcher"] == len(np.unique(cols["pitcher"])) and not hasattr(model, "_x_tree")           # training matrices are not stored


@pytest.mark.skipif(not HAS_XGB, reason="xgboost not installed")
def test_a_zero_base_margin_equals_plain_xgboost(w4):
    from treepolo_mlb_data.research import league_core as lc

    w, cols = w4
    x, _ = lc.situation_features(cols, with_action=True)
    y, classes, keep = lc.encode_outcome(cols["outcome"], __import__("treepolo_mlb_data.research.modeling", fromlist=["x"]).DEFAULT_CLASS_MERGE)
    params = dict(max_depth=3, min_leaf=100, learning_rate=0.2, max_iter=30)
    plain = gbm.fit_gbm("xgboost", "cpu", params, x[keep], y[keep], len(classes), seed=4)
    zero = gbm.fit_gbm("xgboost", "cpu", params, x[keep], y[keep], len(classes), seed=4, base_margin=np.zeros((int(keep.sum()), len(classes))))
    z = np.zeros((200, len(classes)))
    # a zero margin starts the trees from equal class probabilities, plain xgboost from the class shares, so the two are close, not identical
    assert np.abs(plain.predict_proba(x[:200]) - zero.predict_proba(x[:200], base_margin=z)).mean() < 0.02
    assert plain.history["train"] and len(plain.history["train"]) == plain.n_iter


def test_the_refused_settings_stop_before_any_training(w4):
    w, cols = w4
    with pytest.raises(ConfigError):
        _fit("logistic", cols, control_survival="batter_and_count")
    if HAS_XGB:
        with pytest.raises(ConfigError):
            _fit("gbm", cols, backend="hgb", control_pitcher="effect")


def test_location_is_averaged_over_the_historical_locations():
    rng = np.random.default_rng(0)
    w, cols = _world("W0", 3000, 4)
    n = len(cols["game_pk"])
    cols = dict(cols, plate_x=rng.normal(0, 0.8, n), plate_z=rng.normal(2.5, 0.8, n))
    model = _fit("logistic", cols, control_location="include_and_average", location_samples=8)
    rows = {k: v[:50] for k, v in cols.items()}
    a = model.predict_proba(rows, effects="population", seed=1); b = model.predict_proba(rows, effects="population", seed=1); c = model.predict_proba(rows, effects="population", seed=2)
    assert np.allclose(a, b) and np.allclose(a.sum(axis=1), 1) and np.abs(a - c).max() > 0                  # same seed same answer; another draw of locations differs a little
    assert model.pool is not None and model.info["location"]


# ------------------------------------------------------------------------------------------------ known answers (numbers only)
def _repeat_effect(model, cols, action="SL"):
    """Mean change in the whiff probability when the previous pitch was the same type (a run of 2) versus a different type, league-average player, same situations."""

    n = min(len(cols["game_pk"]), 8000)
    base = {k: v[:n] for k, v in cols.items() if isinstance(v, np.ndarray) and len(v) >= n}
    first = dict(base, pitch_type=np.full(n, action), prev1_pitch_type=np.full(n, "FF" if action != "FF" else "SL"), streak_pos=np.ones(n))
    rep = dict(base, pitch_type=np.full(n, action), prev1_pitch_type=np.full(n, action), streak_pos=np.full(n, 2.0))
    i = model.classes.index("whiff")
    return float((model.predict_proba(rep, effects="population")[:, i] - model.predict_proba(first, effects="population")[:, i]).mean())


@pytest.mark.report
def test_report_w7_a_strong_pitch_is_not_a_repeat_effect():
    """Known answer: in W7 the true repeat effect is 0. Without pitcher-and-pitch effects the model should credit the repeat; with them it should come back towards 0.
    The shrinkage is chosen by the data (automatic mode); the iterations are 600. Numbers only, with the penalties the data chose and whether the fit hit the iteration cap."""

    w, cols = _world("W7", 60_000, 6)
    out = {}
    for name, extra in (("none", {}), ("pitcher", dict(control_pitcher="effect")), ("pitcher_pitch_effect", dict(control_pitcher_pitch="effect")), ("pitcher_pitch_within", dict(control_pitcher_pitch="within"))):
        model = _fit("logistic", cols, max_iter=600, **extra)
        out[name] = _repeat_effect(model, cols)
        out[f"{name}_hit_cap"] = int(model.info["hit_cap"])
        if model.info["penalties_estimated"]:
            out[f"{name}_penalties"] = model.info["penalties"]
    record("W7_repeat_effect_whiff", **out)
    assert all(np.isfinite(v) for k, v in out.items() if isinstance(v, float))


@pytest.mark.report
def test_report_w4_and_w5_slider_whiff_effect_under_each_control():
    """Known answer: in W4 (pitcher quality) and W5 (form of the day) the slider does nothing, so the slider-minus-fastball whiff difference should be near 0. Numbers only, under each
    control, with the shrinkage chosen by the data and, for comparison, a manual strong penalty (100) and a manual tiny one (0.0001); 600 iterations."""

    out = {}
    for kind, controls in (("W4", ("none", "effect", "within")), ("W5", ("none", "pitcher_game_effect"))):
        w, cols = _world(kind, 60_000, 7)
        n = min(8000, len(cols["game_pk"]))
        base = {k: v[:n] for k, v in cols.items() if isinstance(v, np.ndarray) and len(v) >= n}
        for c in controls:
            variants = [("auto", {})] if c in ("none", "within") else [("auto", {}), ("manual_100", dict(effect_penalty_mode="manual", effect_penalty_pitcher=100.0, effect_penalty_pitcher_game=100.0)),
                                                                        ("manual_0.0001", dict(effect_penalty_mode="manual", effect_penalty_pitcher=1e-4, effect_penalty_pitcher_game=1e-4))]
            for label, extra_mode in variants:
                extra = {"W4": dict(control_pitcher=c), "W5": dict(control_day=c)}[kind]
                model = _fit("logistic", cols, max_iter=600, **extra, **extra_mode)
                i = model.classes.index("whiff")
                sl = model.predict_proba(dict(base, pitch_type=np.full(n, "SL"), streak_pos=np.ones(n), prev1_pitch_type=np.full(n, "none")), effects="population")[:, i]
                ff = model.predict_proba(dict(base, pitch_type=np.full(n, "FF"), streak_pos=np.ones(n), prev1_pitch_type=np.full(n, "none")), effects="population")[:, i]
                key = f"{kind}_{c}_{label}"
                out[key] = float((sl - ff).mean()); out[f"{key}_hit_cap"] = int(model.info["hit_cap"])
                if model.info["penalties_estimated"]:
                    out[f"{key}_penalties"] = model.info["penalties"]
    record("W4_W5_slider_vs_fastball_whiff", **out)
    assert all(np.isfinite(v) for k, v in out.items() if isinstance(v, float))


@pytest.mark.report
def test_report_w5_day_effects_compared_only_inside_the_day():
    """Known answer: W5's slider does nothing. control_day = within (the day effects are not shrunk) should remove the confounding. The iterations are raised until the optimiser stops by
    itself (hit_cap 0); the report says how many it used. Numbers only."""

    w, cols = _world("W5", 60_000, 7)
    n = min(8000, len(cols["game_pk"]))
    base = {k: v[:n] for k, v in cols.items() if isinstance(v, np.ndarray) and len(v) >= n}
    out = {}
    for cap in (1000, 3000, 8000):
        model = _fit("logistic", cols, max_iter=cap, control_day="within")
        out["tried_max_iter"] = out.get("tried_max_iter", []) + [cap]
        if not model.info["hit_cap"]:
            break
    i = model.classes.index("whiff")
    sl = model.predict_proba(dict(base, pitch_type=np.full(n, "SL"), streak_pos=np.ones(n), prev1_pitch_type=np.full(n, "none")), effects="population")[:, i]
    ff = model.predict_proba(dict(base, pitch_type=np.full(n, "FF"), streak_pos=np.ones(n), prev1_pitch_type=np.full(n, "none")), effects="population")[:, i]
    out.update(slider_minus_fastball_whiff=float((sl - ff).mean()), max_iter_used=cap, iterations_used=float(model.info["n_iter"]), hit_cap=int(model.info["hit_cap"]), penalties=model.info["penalties"])
    record("W5_control_day_within", **out)
    assert np.isfinite(out["slider_minus_fastball_whiff"])


def test_shrinkage_is_a_precision_chosen_by_the_data_and_the_information_of_a_pitch_is_small():
    from treepolo_mlb_data.research.outcome_models import shrinkage as sh

    assert sh.info_per_pitch([0.5, 0.5]) == pytest.approx(0.25) and sh.info_per_pitch([0.9, 0.1]) == pytest.approx(0.09) and 0.05 < sh.info_per_pitch([0.36, 0.17, 0.17, 0.01, 0.004, 0.012, 0.16, 0.10]) < 0.12
    w, cols = _world("W4", 20_000, 5)
    auto = _fit("logistic", cols, max_iter=300, control_pitcher="effect", control_batter="effect")
    assert auto.info["penalties_estimated"] and set(auto.info["penalties"]) == {"pitcher", "batter"} and all(0 < v <= 1e4 for v in auto.info["penalties"].values())
    assert {g: len(v) for g, v in auto.info["penalties_by_class"].items()} == {"pitcher": 8, "batter": 8} and len(set(auto.info["penalties_by_class"]["pitcher"])) > 1          # one precision per outcome class
    manual = _fit("logistic", cols, max_iter=300, control_pitcher="effect", control_batter="effect", effect_penalty_mode="manual", effect_penalty_pitcher=7.0, effect_penalty_batter=9.0)
    assert not manual.info["penalties_estimated"] and manual.info["penalties"] == {"pitcher": 7.0, "batter": 9.0}                  # a manual value is kept as written
    # the share of his own data a pitcher's effect uses is information / (information + penalty), not pitches / (pitches + penalty)
    pid = int(manual.players("pitcher")[0][0]); n = manual.player_info("pitcher", pid)["pitches"]
    got = manual.player_info("pitcher", pid)["shrinkage"]
    info = n * manual.class_info                                                        # the information of n pitches about each outcome class
    assert got == pytest.approx(float(np.mean(info / (info + 7.0)))) and got != pytest.approx(n / (n + 7.0))
    pen = np.asarray(auto.info["penalties_by_class"]["pitcher"]); info_a = n * auto.class_info
    assert auto.player_info("pitcher", pid)["shrinkage"] == pytest.approx(float(np.mean(info_a / (info_a + pen))))


def test_hitting_the_iteration_cap_is_reported():
    w, cols = _world("W4", 12_000, 5)
    stopped = _fit("logistic", cols, max_iter=3, control_pitcher="effect")
    assert stopped.info["hit_cap"] is True
    done = _fit("logistic", cols, max_iter=1000)                                                                                  # no effects: a small problem that converges
    assert done.info["hit_cap"] is False and om.fill_defaults({})["max_iter"] == 1000 and om.fill_defaults({})["effect_penalty_mode"] == "auto"


# ------------------------------------------------------------------------------------------------ the research method
@pytest.fixture(scope="module")
def players(tmp_path_factory):
    data = tmp_path_factory.mktemp("om_world") / "data"; data.mkdir()
    d = simulate_players("P7", n_pitchers=50, n_batters=70, n_games=700, pitches_per_game=40, seed=7)
    write_player_world_db(data / "statcast.sqlite3", d)
    return ResearchService(AppConfig(data_dir=str(data)), None)


SCOPE = {"game_years": [2023, 2024], "game_types": ["R"]}
RUN = {"scope": SCOPE, "groups": ["RvR"], "model": "logistic", "control_pitcher": "effect", "control_batter": "effect", "train_years": [2023], "test_years": [2024], "cold_by": ["pitcher"],
       "cold_folds": 3, "bootstrap_reps": 30, "max_iter": 60, "min_class_count": 20, "effect_sample": 3000, "replication_members": 0}


def _section(service, run, prefix):
    return next(s["rows"] for s in service.store.load_result(run["id"])["sections"] if s["title"].startswith(prefix))


def test_the_method_reports_numbers_for_every_section_and_no_verdict(players):
    run = players.run("outcome_model_train", RUN)["run"]
    assert "hit_cap" in players.store.load_result(run["id"])["extras"] and players.store.load_result(run["id"])["extras"]["effect_penalty_mode"] == "auto"
    fit = _section(players, run, "Fit summary")
    roles = {(r["role"], r["mode"]) for r in fit}
    assert ("model", "season_specific") in roles and ("model", "season_population") in roles and ("baseline", "season_specific") in roles and ("model", "cold_pitcher") in roles
    assert all(r["logloss"] > 0 and r["n_eval"] > 0 for r in fit) and all(("loss_whiff" in r) for r in fit)
    gain = _section(players, run, "Effect gain")
    assert gain and all({"delta_logloss", "lo", "hi", "n"} <= set(r) for r in gain) and not any("passed" in r for r in gain)
    effects = _section(players, run, "Pitch effects")
    assert effects and {r["phase"] for r in effects} <= {"even", "ahead", "behind"} and all(r["lo"] is None or r["lo"] <= r["effect"] + 1e-9 for r in effects)
    common = _section(players, run, "Common sense")
    assert common and all(0 <= r["whiff_obs"] <= 1 for r in common)
    controls = _section(players, run, "Controls")
    assert {r["control"] for r in controls} == set(ctl.CONTROL_NAMES) and next(r for r in controls if r["control"] == "control_pitcher")["setting"] == "effect"
    extras = players.store.load_result(run["id"])["extras"]
    assert extras["controls"]["control_pitcher"] == "effect" and extras["comparisons"]["pitch_effects"]["RvR"]["n"] >= 0
    assert players.store.load_artifact(run["id"], "outcome_model_RvR") is not None


def test_any_year_combination_is_allowed_and_the_stored_model_can_be_loaded(players):
    from treepolo_mlb_data.research.outcome_model_methods import load_model

    run = players.run("outcome_model_train", {**RUN, "train_years": [2024], "test_years": [2023], "cold_by": [], "compare_baseline": False, "min_class_count": 1})["run"]      # train on the later year, test on the earlier one
    assert run["status"] == "success"
    model = load_model(players.store.load_artifact(run["id"], "outcome_model_RvR")["rows"])
    assert model.name == "logistic" and model.classes and model.info["effects"]["pitcher"] > 0


def test_the_method_validates_its_settings(players):
    with pytest.raises(ConfigError, match="disjoint"):
        players.run("outcome_model_train", {**RUN, "test_years": [2023]})
    with pytest.raises(ConfigError, match="control_batter"):
        players.run("outcome_model_train", {**RUN, "control_batter": "none", "control_survival": "batter_and_count"})
    with pytest.raises(ConfigError, match="model"):
        players.run("outcome_model_train", {**RUN, "model": "no_such_model"})
    with pytest.raises(ConfigError):
        players.run("outcome_model_train", {**RUN, "unknown_setting": 1})


def test_param_search_scores_any_model_on_unseen_players_and_a_year(players):
    out = players.run("param_search", {"scope": SCOPE, "groups": ["RvR"], "model": "logistic", "control_pitcher": "effect", "trials": 3, "folds": 3, "scoring": ["pitcher", "year"],
                                       "grid": {"l2": [0.5, 5.0], "effect_penalty_pitcher": [5.0, 50.0]}, "max_iter": 40, "tune_rows": 20000, "min_class_count": 20})
    extras = players.store.load_result(out["run"]["id"])["extras"]
    assert set(extras["best_params"]) == {"l2", "effect_penalty_pitcher"} and extras["scoring"] == ["pitcher", "year"] and extras["model"] == "logistic"
    trials = _section(players, out["run"], "Trials")
    assert len(trials) == 3 and all(r["logloss_pitcher_folds"] is not None and r["logloss_year_folds"] is not None and r["logloss_batter_folds"] is None for r in trials)
    with pytest.raises(ConfigError, match="grid"):
        players.run("param_search", {"scope": SCOPE, "groups": ["RvR"], "model": "logistic", "grid": {"no_such_setting": [1]}})
    with pytest.raises(ConfigError, match="year"):
        players.run("param_search", {"scope": {"game_years": [2023], "game_types": ["R"]}, "groups": ["RvR"], "scoring": ["year"]})


def test_a_fit_that_stops_at_the_cap_is_listed_in_the_result(players):
    run = players.run("outcome_model_train", {**RUN, "max_iter": 2, "compare_baseline": False, "cold_by": []})["run"]
    extras = players.store.load_result(run["id"])["extras"]
    assert extras["hit_cap"] and extras["hit_cap"][0]["role"] == "model"
    fit = _section(players, run, "Fit summary")
    assert any(r["hit_cap"] == 1 for r in fit)
