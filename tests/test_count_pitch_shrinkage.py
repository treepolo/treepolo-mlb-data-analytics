"""The count x pitch-type interactions of the logistic model are shrunk by the amount of data (empirical Bayes, one strength per outcome class): thin cells go towards 0, thick cells hardly move."""
from __future__ import annotations

import numpy as np
import pytest

from treepolo_mlb_data.research import outcome_models as om
from treepolo_mlb_data.research.outcome_models import design as dz, fastglm
from treepolo_mlb_data.research.profiles import PITCH_TYPES

pytest.importorskip("torch")

SETTINGS = dict(control_pitcher="none", control_pitcher_pitch="none", control_batter="none", control_day="none", bagging_reps=1, seed=1, device="cpu", max_iter=300, solver="grouped")
OUTCOMES = ["ball", "called_strike", "foul", "foul_tip", "hit_by_pitch", "in_play_home_run", "in_play_out", "whiff"]


def make_pitches(n: int = 3000, seed: int = 5) -> dict[str, np.ndarray]:
    """A small fixed data set: one common pitch type, a few ordinary ones and many rare ones, most pitches thrown in the first counts, so a few count x type cells are thick and most are thin.
    The common type really behaves differently in three counts (the thick cells carry a real interaction); everywhere else the interactions are noise."""

    rng = np.random.default_rng(seed)
    share = np.array([0.50, 0.20, 0.12, 0.07, 0.04, 0.03, 0.02, 0.015, 0.005]); share = share / share.sum()
    typ = rng.choice(len(PITCH_TYPES), size=n, p=share)
    count_p = np.array([0.28, 0.16, 0.08, 0.14, 0.12, 0.08, 0.05, 0.05, 0.06, 0.01, 0.02, 0.03]); count_p = count_p / count_p.sum()       # 0-0, 0-1, 0-2, 1-0, ...
    cell = rng.choice(12, size=n, p=count_p)
    balls, strikes = cell // 3, cell % 3
    P = np.tile(np.array([0.30, 0.15, 0.12, 0.03, 0.01, 0.03, 0.20, 0.16]), (n, 1))
    P[strikes == 2, 7] += 0.10; P[strikes == 2, 0] -= 0.10
    P[(cell == 0) & (typ == 0), 0] += 0.25; P[(cell == 0) & (typ == 0), 6] -= 0.15                 # the common type in 0-0: more balls
    P[(cell == 1) & (typ == 0), 1] += 0.25; P[(cell == 1) & (typ == 0), 0] -= 0.15                 # in 0-1: more called strikes
    P[(cell == 3) & (typ == 0), 7] += 0.20; P[(cell == 3) & (typ == 0), 6] -= 0.10                 # in 1-0: more whiffs
    P = np.clip(P, 0.005, None); P = P / P.sum(axis=1, keepdims=True)
    outcome = np.array(OUTCOMES)[(rng.random(n)[:, None] > np.cumsum(P, axis=1)).sum(axis=1).clip(0, 7)]
    names = np.array(PITCH_TYPES)
    return {"game_pk": rng.integers(1, 80, n), "pitcher": rng.integers(1, 10, n), "batter": rng.integers(100, 140, n), "group": np.full(n, "RvR"), "pitch_type": names[typ],
            "outcome": outcome, "balls": balls.astype(float), "strikes": strikes.astype(float), "outs_when_up": rng.integers(0, 3, n).astype(float), "bases": rng.integers(0, 8, n),
            "prev1_pitch_type": np.where(rng.random(n) < 0.3, "none", names[rng.choice(len(PITCH_TYPES), size=n, p=share)]), "streak_pos": rng.integers(1, 4, n).astype(float),
            "prior_streak": np.zeros(n), "game_year": np.full(n, 2023), "pa_id": np.arange(n), "pitch_value": np.zeros(n), "state_code": np.zeros(n)}


@pytest.fixture(scope="module")
def fits():
    cols = make_pitches()
    models = {mode: om.new_model("logistic").fit(cols, None, {**SETTINGS, "control_count_pitch": mode}) for mode in ("none", "effect")}
    cell = dz.count_cell(cols) * dz.N_TYPES + np.maximum(dz.type_index(cols["pitch_type"]), 0)
    n = np.bincount(cell, minlength=12 * dz.N_TYPES)
    lo = dz.SIT_OFFSETS["count*type"]
    block = lambda m: np.asarray(m.members[0]["coef"][lo:lo + 12 * dz.N_TYPES], dtype=float)
    cent = fastglm.GroupedSoftmax._centering_matrix(12 * dz.N_TYPES, (12, dz.N_TYPES), n > 0)
    return models, n, cent @ block(models["none"]), block(models["effect"])          # before: only the interaction part (centred inside every count and pitch type), the part the shrunk model keeps in its block


def test_thin_count_by_pitch_cells_move_towards_zero_and_thick_cells_hardly_move(fits):
    models, n, before, after = fits
    thin = (n >= 1) & (n < 30); thick = n >= 150
    assert thin.sum() >= 10 and thick.sum() >= 2
    ratio = lambda m: np.abs(after[m]).sum() / np.abs(before[m]).sum()
    mid = (n >= 30) & (n < 150)
    assert ratio(thin) < 0.65                                                                    # thin cells: pulled clearly towards 0
    assert (np.abs(after[thin]) <= np.abs(before[thin]) + 1e-6).mean() > 0.9                     # nearly every one of them
    assert ratio(thin) < ratio(mid) < ratio(thick) and ratio(thick) > 0.75                       # the more data a cell has, the less it is pulled; thick cells hardly change


def test_the_strength_is_estimated_per_outcome_class_and_unused_cells_stay_at_zero(fits):
    models, n, before, after = fits
    by_class = models["effect"].info["penalties_by_class"]["count_pitch"]
    assert len(by_class) == len(models["effect"].classes) and len(set(np.round(by_class, 6))) > 1 and all(v > 0 for v in by_class)   # one strength per class, from the data
    assert "count_pitch" not in (models["none"].info.get("penalties_by_class") or {})
    assert np.abs(after[n == 0]).max() < 1e-3                                                    # a combination never seen has nothing to estimate

