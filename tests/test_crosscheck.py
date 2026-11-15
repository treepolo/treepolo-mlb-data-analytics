"""The pyfixest cross-check of the repeat and previous-pitch effects (a report section) and the note on what 'within' pitcher-and-pitch effects cannot identify."""
from __future__ import annotations

import numpy as np
import pytest

from treepolo_mlb_data.research import crosscheck, outcome_models as om, policy_worlds as pw
from treepolo_mlb_data.research.outcome_models import controls as ctl

pytest.importorskip("pyfixest")
pytest.importorskip("torch")


def test_the_note_says_that_pitch_types_cannot_be_compared_as_a_whole_with_pitcher_pitch_within():
    notes = crosscheck.identification_notes({**ctl.default_controls(), "control_pitcher_pitch": "within"})
    assert len(notes) == 1 and "無法識別" in notes[0]["zh"] and "not identified" in notes[0]["en"] and notes[0]["control"] == "control_pitcher_pitch"
    assert crosscheck.identification_notes(ctl.default_controls()) == []
    how = {r["control"]: r["how"] for r in ctl.describe_controls({**ctl.default_controls(), "control_pitcher_pitch": "within"}, model="logistic", backend="xgboost")}
    assert "同一投手、同一球種" in how["control_pitcher_pitch"]


def test_overall_effects_that_a_finer_group_covers_are_left_out_of_the_fixed_effects():
    assert crosscheck.fixed_effect_sets({"pitcher": True, "pitcher_pitch": True, "pitcher_game": True, "batter": False, "batter_count": False}) == ["pitcher_pitch", "pitcher_game", "batter_count"]
    assert crosscheck.fixed_effect_sets({"pitcher": False}) == ["pitcher"]


def test_the_cross_check_gives_effects_with_game_clustered_errors_and_finds_no_repeat_effect_in_w7():
    """W7: a pitcher's strong pitch is thrown more and repeated more, but repeating itself does nothing. Inside a pitcher-and-pitch pair the repeat effect should be about 0."""

    cols = pw.simulate_pitches(pw.build_world("W7"), 20_000, 5)
    model = om.new_model("logistic").fit(cols, None, dict(max_iter=300, bagging_reps=1, seed=3, device="cpu", control_pitcher_pitch="within"))
    sub, y = model._encode(cols, model.settings, fixed_classes=True)
    rows, info = crosscheck.run("RvR", sub, y, model.classes, model.groups)
    assert info["available"] and info["clustered_by"] == "game" and "pitcher_pitch" in info["fixed_effects"] and "pitcher" not in info["fixed_effects"]
    assert {"run_2", "run_3plus"} <= {r["term"] for r in rows} and {r["class"] for r in rows} <= set(model.classes)
    assert all(r["se"] > 0 and r["lo"] < r["effect"] < r["hi"] and set(r) == set(crosscheck.COLUMNS) for r in rows)
    whiff = next(r for r in rows if r["class"] == "whiff" and r["term"] == "run_2")
    assert abs(whiff["effect"]) < max(0.02, 4 * whiff["se"])
    without = om.new_model("logistic").fit(cols, None, dict(max_iter=300, bagging_reps=1, seed=3, device="cpu"))
    sub0, y0 = without._encode(cols, without.settings, fixed_classes=True)
    assert crosscheck.run("RvR", sub0, y0, without.classes, {})[1]["available"]                 # no controls: only the situation fixed effects
