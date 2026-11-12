"""The fixed-effects anchor (plan 3.6) on the confounded worlds W4 (pitcher quality) and W5 (form of the day), and its comparison with the league model."""
from __future__ import annotations

import numpy as np
import pytest

from treepolo_mlb_data.research import anchor as anc, gbm, policy_build as pb, policy_core as pc, policy_worlds as pw
from treepolo_mlb_data.research.profiles import PITCH_TYPES

from report_helper import record

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


def compared(kind):
    w, d = sim(kind)
    tensor, est = l0_model(w, d)
    model = anc.model_contrasts(tensor.mean(axis=0), est.obs, est.support, est.occupancy, w.shape, TYPES)
    s3 = anchor_rows(anc.action_contrasts(anc.fit_effects(d, types=TYPES, design="S3")))
    return model, s3, anc.anchor_consistency(model, s3)


def test_anchor_consistency_lists_every_cell_and_gives_no_verdict():
    model, s3, res = compared("W1")
    assert set(res) == {"cells", "summary"} and "passes" not in res["summary"]
    keyed = {(r["phase"], r["action"], r["class"]): r for r in s3}
    assert res["summary"]["cells"] == len(res["cells"]) >= 5
    for cell in res["cells"]:
        a = keyed[(cell["phase"], cell["action"], cell["class"])]
        m = next(x for x in model if (x["phase"], x["action"], x["class"]) == (cell["phase"], cell["action"], cell["class"]))
        assert cell["model_effect"] == pytest.approx(m["effect"]) and cell["anchor_effect"] == pytest.approx(a["effect"])
        assert cell["anchor_lo"] == pytest.approx(a["effect"] - anc.ANCHOR_Z * a["se"]) and cell["anchor_hi"] == pytest.approx(a["effect"] + anc.ANCHOR_Z * a["se"])
        assert cell["difference"] == pytest.approx(m["effect"] - a["effect"]) and cell["same_sign"] == int(np.sign(m["effect"]) == np.sign(a["effect"]))
    s = res["summary"]
    assert s["same_sign_share"] == pytest.approx(sum(c["same_sign"] for c in res["cells"]) / len(res["cells"]))
    assert s["outside_anchor_interval"] == sum(1 for c in res["cells"] if not c["anchor_lo"] <= c["model_effect"] <= c["anchor_hi"])


def test_anchor_consistency_on_made_up_cells():
    model = [{"phase": "even", "action": "SL", "class": "whiff", "effect": 0.02}, {"phase": "even", "action": "SL", "class": "foul", "effect": -0.01},
             {"phase": "even", "action": "CH", "class": "whiff", "effect": 0.05}, {"phase": "even", "action": "FF", "class": "whiff", "effect": 0.1}]
    anchor = [{"phase": "even", "action": "SL", "class": "whiff", "effect": 0.04, "se": 0.01}, {"phase": "even", "action": "SL", "class": "foul", "effect": 0.01, "se": 0.01},
              {"phase": "even", "action": "CH", "class": "whiff", "effect": 0.0, "se": 0.01}, {"phase": "even", "action": "FF", "class": "whiff", "effect": 0.1, "se": 0.0}]
    res = anc.anchor_consistency(model, anchor)
    assert len(res["cells"]) == 3                                            # the cell with se = 0 has no interval and is not listed
    assert [c["same_sign"] for c in res["cells"]] == [1, 0, 0]               # 0.02 vs 0.04 same; -0.01 vs 0.01 opposite; 0.05 vs 0.0 is not the same sign
    assert res["summary"]["same_sign_share"] == pytest.approx(1 / 3)
    assert res["summary"]["median_ratio"] == pytest.approx(-0.25)            # ratios 0.5 and -1.0; the cell whose anchor effect is 0 has no ratio
    assert res["summary"]["outside_anchor_interval"] == 3                    # 0.02 is outside [0.0204, 0.0596]; -0.01 is outside [-0.0096, 0.0296]; 0.05 is outside [-0.0196, 0.0196]


@pytest.mark.report
@pytest.mark.parametrize("kind", ["W1", "W4"])
def test_report_league_model_against_the_anchor(kind):
    """Known answer: W1 is not confounded, so the league model should agree with the anchor; W4 is confounded by pitcher quality, so it should show a slider effect the anchor does not.
    Numbers only: same-sign share, median ratio, the model's slider whiff effect."""

    model, s3, res = compared(kind)
    sl_whiff = [m for m in model if m["action"] == "SL" and m["class"] == "whiff" and m["phase"] == "even"][0]["effect"]
    record(f"anchor_consistency_{kind}", **res["summary"], model_slider_whiff_effect=sl_whiff)
    assert res["summary"]["cells"] > 0


def test_jbip_table_recovers_the_value_of_balls_in_play():
    w, d = sim("W1")
    jb, info = anc.jbip_table(d, types=TYPES, value=d["pitch_value"] + np.array([w.env.re_cnt[int(b), int(k), int(o), int(B)] for b, k, o, B in zip(d["balls"], d["strikes"], d["outs_when_up"], d["bases"])]),
                              design="S3")
    seen = [PITCH_TYPES.index(t) for t in TYPES]
    assert np.abs(jb[seen] - w.env.jbip[seen]).max() < 0.03
    assert jb[PITCH_TYPES.index("SL")].mean() < jb[PITCH_TYPES.index("FF")].mean()     # W1: the slider's balls in play are worth less
