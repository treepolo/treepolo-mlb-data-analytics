"""The per-pitcher decision engine: the empirical-Bayes shrinkage of the pitcher x pitch-type effects, the plate-appearance weights, a pitcher's own dynamics, and the engine over a group."""
from __future__ import annotations

import numpy as np
import pytest

from treepolo_mlb_data.research import outcome_models as om, pitcher_engine as pe, policy_build as pb, policy_core as pc, policy_worlds as pw

torch = pytest.importorskip("torch")

SETTINGS = dict(max_iter=400, bagging_reps=2, seed=3, device="cpu", control_pitcher_pitch="within", control_batter="effect")


# ------------------------------------------------------------------------------------------------ the shrinkage
def test_empirical_bayes_pulls_units_with_little_information_and_recovers_the_spread_of_the_true_effects():
    rng = np.random.default_rng(0)
    U, K = 600, 4
    p = np.array([0.4, 0.3, 0.2, 0.1])
    unit = np.diag(p) - np.outer(p, p)
    n = np.concatenate([np.full(U // 2, 8.0), np.full(U // 2, 800.0)])                   # half of the units have 8 pitches, half 800
    H = n[:, None, None] * unit[None]
    true = rng.normal(0.0, 0.5, (U, K)); true -= true.mean(axis=1, keepdims=True)
    pinv = np.linalg.pinv(unit)
    noise = np.stack([rng.multivariate_normal(np.zeros(K), pinv / n[u]) for u in range(U)])
    beta = true + noise; beta -= beta.mean(axis=1, keepdims=True)
    factor, tau2, se2 = pe.shrink_factors(beta, H)
    assert factor.shape == (U, K) and np.all((factor >= 0) & (factor <= 1))
    assert 0.1 < factor[: U // 2].mean() < 0.4 and factor[U // 2:].mean() > 0.95      # 8 pitches: pulled a lot towards the mean (tau^2 / (tau^2 + se^2) is about 0.3); 800 pitches: hardly at all
    assert np.all(np.abs(np.sqrt(tau2) - 0.5 * np.sqrt(1 - 1 / K)) < 0.12)               # the spread of the true effects (centred over K classes) is found
    assert np.all(np.isfinite(se2)) and np.all(se2[: U // 2] > se2[U // 2:])
    none = pe.shrink_factors(beta[:3], np.zeros((3, K, K)))[0]
    assert np.all(none == 0.0)                                                          # a unit with no information gets no weight on its own effect


def test_plate_appearances_count_distinct_game_and_at_bat_pairs_per_pitcher():
    sub = {"pitcher": np.array([1, 1, 1, 1, 2, 2, 3]), "game_pk": np.array([10, 10, 10, 11, 10, 10, 12]), "at_bat_number": np.array([1, 1, 2, 1, 1, 1, 5])}
    ids, pa, pitches = pe.plate_appearances(sub)
    assert ids.tolist() == [1, 2, 3] and pa.tolist() == [3.0, 1.0, 1.0] and pitches.tolist() == [4.0, 2.0, 1.0]


# ------------------------------------------------------------------------------------------------ a model with pitcher x pitch-type effects
@pytest.fixture(scope="module")
def world():
    w = pw.build_world("W4")
    cols = pw.simulate_pitches(w, 3000, 2)
    return w, cols


@pytest.fixture(scope="module")
def model(world):
    _, cols = world
    return om.new_model("logistic").fit(cols, None, SETTINGS)


def test_a_model_without_pitcher_pitch_effects_cannot_make_the_engine(world):
    _, cols = world
    plain = om.new_model("logistic").fit(cols, None, {**SETTINGS, "control_pitcher_pitch": "none", "control_batter": "none", "bagging_reps": 1})
    assert pe.unavailable(plain) is not None
    gbm = om.new_model("gbm").fit(cols, None, dict(max_depth=3, min_leaf=300, learning_rate=0.2, max_iter=20, l2=1.0, backend="hgb", device="cpu", bagging_reps=1, seed=1))
    assert pe.unavailable(gbm) is not None


def test_the_shrunk_copy_keeps_the_probabilities_at_factor_one_and_removes_the_effect_at_factor_zero(world, model):
    _, cols = world
    assert pe.unavailable(model) is None
    rows = {k: v[:600] for k, v in cols.items() if isinstance(v, np.ndarray) and len(v) == len(cols["game_pk"])}
    U = len(model.indexes[pe.TARGET]); K = len(model.classes)
    same = pe.shrunk_copy(model, np.ones((U, K)))
    assert np.abs(same.predict_proba(rows, effects="specific") - model.predict_proba(rows, effects="specific")).max() < 1e-6      # the class-centring changes no probability
    none = pe.shrunk_copy(model, np.zeros((U, K)))
    assert none.members[0]["coef"].shape == model.members[0]["coef"].shape and model is not none and len(none.members) == len(model.members)
    a, size = pe._block(none)
    assert np.abs(none.members[0]["coef"][a:a + size]).max() == 0.0
    assert np.abs(model.members[0]["coef"][a:a + size]).max() > 0.0                                                              # the original model is untouched


def test_a_pitchers_dynamics_use_his_effects_and_a_pitcher_the_model_has_not_seen_is_the_league_average(world, model):
    w, cols = world
    shape = pc.Shape()
    obs, support, n = pb.usage_from_counts(cols, shape, min_count=50)
    U, K = len(model.indexes[pe.TARGET]), len(model.classes)
    full = pe.shrunk_copy(model, np.ones((U, K)))
    league = pb.model_tensor(model, shape, "RvR", effects="population").mean(axis=0)
    seen = int(model.indexes[pe.TARGET].keys[0] // 16)
    mine = pe.pitcher_dynamics(full, seen, shape, "RvR", obs, support, n)
    assert mine.P.shape == league.shape and np.allclose(mine.P.sum(axis=2), 1.0) and np.abs(mine.P - league).max() > 1e-4        # his own probabilities differ from the league's
    gone = pe.shrunk_copy(model, np.zeros((U, K)))
    assert np.abs(pe.pitcher_dynamics(gone, seen, shape, "RvR", obs, support, n).P - league).max() < 1e-6                         # no effect left (the overall pitcher effect included): the league average
    stranger = pe.pitcher_dynamics(full, -12345, shape, "RvR", obs, support, n)
    assert np.abs(stranger.P - league).max() < 1e-6                                                                                # no effects: the league-average dynamics
    assert mine.obs is obs and mine.support is support                                                                             # the observed mix and the support rule stay the league's


def test_the_engine_over_a_group_adds_up_the_savings_by_plate_appearances_and_reports_the_coverage(world, model):
    w, cols = world
    shape = pc.Shape()
    obs, support, n = pb.usage_from_counts(cols, shape, min_count=50)
    sub, _ = model._encode(cols, model.settings, fixed_classes=True)
    ids, pa, _ = pe.plate_appearances(sub)
    min_pa = int(np.sort(pa)[::-1][2])                                                                                             # only the three pitchers with the most plate appearances
    kinds = ("observed", "dp_trust")
    summary, detail, coverage, policies = pe.run("RvR", sub, model, w.env, shape, obs, support, n, kinds=kinds, trust=0.10, min_pa=min_pa, pa_per_team_season=6200.0, league_saving={"dp_trust": 0.01})
    assert policies["pitchers"].tolist() == [d["pitcher"] for d in detail] and policies["dp_trust"].shape == (len(detail), shape.size) and policies["dp_trust"].max() < shape.n_actions
    assert np.isfinite(coverage["start_observed_pitchers"]) and np.isfinite(coverage["hr_observed_pitchers"])
    assert 3 <= coverage["pitchers_used"] <= int((pa >= min_pa).sum()) and coverage["pitchers_used"] < len(ids) and coverage["pitchers_total"] == len(ids) and coverage["min_pa"] == min_pa
    assert coverage["pa_used"] == sum(r["pa"] for r in detail) and 0 < coverage["pa_share"] < 1 and coverage["pa_share"] == pytest.approx(coverage["pa_used"] / coverage["pa_total"])
    assert all(r["pa"] >= min_pa and 0 <= r["shrink_mean"] <= 1 and np.isfinite(r["saving_dp_trust"]) for r in detail)
    trust = next(r for r in summary if r["policy"] == "dp_trust")
    manual = sum(r["pa"] * r["saving_dp_trust"] for r in detail) / sum(r["pa"] for r in detail)
    assert trust["saving_pa"] == pytest.approx(manual) and trust["pitchers"] == len(detail) and trust["league_saving_pa"] == 0.01
    assert trust["saving_p10"] <= trust["saving_median"] <= trust["saving_p90"] and trust["saving_per_team_season"] == pytest.approx(6200.0 * manual)
    obs_row = next(r for r in summary if r["policy"] == "observed")
    assert obs_row["saving_pa"] == 0.0 and np.isfinite(obs_row["hr_probability"]) and np.isfinite(trust["hr_probability"])      # the policy that is played and the one proposed carry the home-run probability
    none = pe.run("RvR", sub, model, w.env, shape, obs, support, n, kinds=kinds, trust=0.10, min_pa=10 ** 9, pa_per_team_season=6200.0, league_saving={})
    assert none[0] == [] and none[1] == [] and none[2]["pitchers_used"] == 0 and none[2]["pa_share"] == 0.0                       # a threshold nobody reaches: nothing is solved and the coverage says so
