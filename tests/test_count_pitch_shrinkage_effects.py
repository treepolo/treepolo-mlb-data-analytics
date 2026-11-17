"""The case that went wrong with the first version: the shrunk count x pitch-type block together with the other effects of the real model (about 25 seconds)."""
from __future__ import annotations

import numpy as np
import pytest

from treepolo_mlb_data.research import outcome_models as om
from treepolo_mlb_data.research.outcome_models import design as dz
from test_count_pitch_shrinkage import SETTINGS, make_pitches

pytest.importorskip("torch")


def test_with_the_other_effects_of_the_real_model_the_fit_converges_and_the_interactions_do_not_grow():
    """The case that went wrong: pitcher-and-pitch effects, the day, batters and batter-and-count effects around the shrunk block. The interactions swapped levels with the count and pitch-type
    main effects, the strengths fell below the ordinary ridge, the fit never converged and the coefficients grew several times larger."""

    cols = make_pitches(2000, seed=7)
    other = dict(control_pitcher_pitch="within", control_day="within", control_batter="effect", control_survival="batter_and_count")
    models = {mode: om.new_model("logistic").fit(cols, None, {**SETTINGS, **other, "max_iter": 600, "control_count_pitch": mode}) for mode in ("none", "effect")}
    cell = dz.count_cell(cols) * dz.N_TYPES + np.maximum(dz.type_index(cols["pitch_type"]), 0)
    lo = dz.SIT_OFFSETS["count*type"]
    seen = np.bincount(cell, minlength=12 * dz.N_TYPES) > 0
    block = lambda m: np.asarray(m.members[0]["coef"][lo:lo + 12 * dz.N_TYPES], dtype=float)[seen]
    assert not models["effect"].info["hit_cap"]                                                  # it converges
    assert np.abs(block(models["effect"])).mean() < np.abs(block(models["none"])).mean()          # the interactions are smaller than without the shrinkage, not several times larger
    assert min(models["effect"].info["penalties_by_class"]["count_pitch"]) > 1.0                 # the strength is above the ordinary ridge of 1
