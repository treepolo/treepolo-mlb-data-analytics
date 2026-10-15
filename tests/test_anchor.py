"""The fixed-effects anchor (plan 3.6) on the confounded worlds W4 (pitcher quality) and W5 (form of the day), and its comparison with the league model."""
from __future__ import annotations

import numpy as np
import pytest

from treepolo_mlb_data.research import anchor as anc, gbm, policy_build as pb, policy_core as pc, policy_worlds as pw
from treepolo_mlb_data.research.profiles import PITCH_TYPES

TYPES = ["FF", "SL", "CH"]
PARAMS = dict(max_depth=4, min_leaf=1000, learning_rate=0.15, max_iter=60, l2=1.0)
BACKEND = "xgboost" if gbm.xgboost_available() else "hgb"
CACHE: dict = {}


def sim(kind, n_pa=60_000, seed=3):
    key = (kind, n_pa, seed)
    if key not in CACHE:
        w = pw.build_world(kind)
        CACHE[key] = (w, pw.simulate_pitches(w, n_pa, seed))
    return CACHE[key]


def contrast(rows, cls="whiff", action="SL", phase="even"):
    return next(r for r in rows if r["class"] == cls and r["action"] == action and r["phase"] == phase)


def test_w4_pitcher_effects_remove_the_quality_confounding():
    w, d = sim("W4")
    ph = np.asarray(d["outcome"]) == "whiff"; sl = np.asarray(d["pitch_type"]) == "SL"
    assert ph[sl].mean() - ph[~sl].mean() > 0.015                          # without fixed effects the slider looks better
    for design in ("S3", "S4"):
        row = contrast(anc.action_contrasts(anc.fit_effects(d, types=TYPES, design=design)))
        assert row["lo"] < 0 < row["hi"] and abs(row["effect"]) < 0.005    # and with them it does nothing, as in the world


def test_w5_needs_pitcher_game_effects():
    w, d = sim("W5")
    s3 = contrast(anc.action_contrasts(anc.fit_effects(d, types=TYPES, design="S3")))
    s4 = contrast(anc.action_contrasts(anc.fit_effects(d, types=TYPES, design="S4")))
    assert s3["lo"] > 0.01                                                  # form of the day is not removed by pitcher effects: a false slider effect
    assert s4["lo"] < 0 < s4["hi"] and abs(s4["effect"]) < 0.005            # pitcher-game effects remove it


def test_w1_true_slider_effect_is_recovered_and_fitted_values_add_to_one():
    w, d = sim("W1")
    fit = anc.fit_effects(d, types=TYPES, design="S3")
    rows = anc.action_contrasts(fit)
    assert contrast(rows)["lo"] > 0.02 and contrast(rows, cls="in_play_home_run")["hi"] < 0
    assert np.abs(fit["beta"].sum(axis=0)).max() < 1e-8                    # same design for every class: the coefficients of the classes add to zero
    assert fit["outside01"].max() < 0.01 and fit["converged"]
    streak = anc.streak_effects(fit)
    assert {r["term"] for r in streak} == {"streak=2", "streak=3+"}


def test_w2_repeat_effect_shows_in_the_streak_terms():
    w, d = sim("W2")
    fit = anc.fit_effects(d, types=TYPES, design="S3")
    rows = {(r["term"], r["class"]): r for r in anc.streak_effects(fit)}
    assert rows[("streak=2", "whiff")]["lo"] > 0.01 and rows[("streak=3+", "whiff")]["effect"] > rows[("streak=2", "whiff")]["effect"]


def l0_model(w, d):
    members, classes = pb.fit_outcome_members(d, backend=BACKEND, device="cpu", params=PARAMS, reps=3, seed=3)
    tensor = pb.outcome_tensor(members, classes, w.shape)
    obs, support, n = pb.usage_from_counts(d, w.shape, min_count=100)
    est = pb.analyze(pb.members_dynamics(tensor, obs, support, n), w.env, w.shape, kinds=("observed",))
    return tensor, est


def anchor_rows(fit_rows):
    return [{"phase": r["phase"], "action": r["action"], "class": r["class"], "effect": r["effect"], "se": r["se"]} for r in fit_rows]


@pytest.mark.parametrize("kind,agrees", [("W1", True), ("W4", False)])
def test_league_model_agrees_with_the_anchor_only_when_it_is_not_confounded(kind, agrees):
    w, d = sim(kind)
    tensor, est = l0_model(w, d)
    shape = w.shape
    model = anc.model_contrasts(tensor.mean(axis=0), est.obs, est.support, est.occupancy, shape, TYPES)
    s3 = anchor_rows(anc.action_contrasts(anc.fit_effects(d, types=TYPES, design="S3")))
    res = anc.anchor_consistency(model, s3)
    if agrees:
        assert res["passes"] and res["same_sign_share"] >= 0.9 and res["anchor_cells"] >= 5
    else:
        assert res["model_only_cells"] >= 2                                  # the model sees slider effects that the anchor does not: confounding
        sl_whiff = [m for m in model if m["action"] == "SL" and m["class"] == "whiff" and m["phase"] == "even"][0]["effect"]
        assert sl_whiff > 0.01                                               # and the size of that false effect is reported by the comparison


def test_jbip_table_recovers_the_value_of_balls_in_play():
    w, d = sim("W1")
    jb, info = anc.jbip_table(d, types=TYPES, value=d["pitch_value"] + np.array([w.env.re_cnt[int(b), int(k), int(o), int(B)] for b, k, o, B in zip(d["balls"], d["strikes"], d["outs_when_up"], d["bases"])]),
                              design="S3")
    seen = [PITCH_TYPES.index(t) for t in TYPES]
    assert np.abs(jb[seen] - w.env.jbip[seen]).max() < 0.03
    assert jb[PITCH_TYPES.index("SL")].mean() < jb[PITCH_TYPES.index("FF")].mean()     # W1: the slider's balls in play are worth less
