"""The within-situation version of the direct check: when the recommended pitches are concentrated in situations where fewer runs are lost, but inside a situation following the recommendation makes no
difference, the plain comparison shows a big difference and the within-situation one shows about none."""
from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("pyfixest")

from treepolo_mlb_data.research.policy_check_methods import compare, within_state


def fake_pitches(n: int = 4000, seed: int = 3):
    rng = np.random.default_rng(seed)
    state = rng.integers(0, 10, n)
    level = np.where(state < 5, 0.0, 3.0)                                                     # five situations that lose few runs, five that lose many
    followed = rng.random(n) < np.where(state < 5, 0.8, 0.2)                                   # the recommendation is followed mostly in the situations that lose few runs
    value = level + rng.normal(0.0, 1.0, n)                                                    # inside a situation following the recommendation changes nothing
    game = rng.integers(0, 200, n)
    return value, followed, state, game


def test_the_plain_comparison_is_far_from_zero_and_the_within_situation_one_is_near_zero():
    value, followed, state, game = fake_pitches()
    plain = compare(value, followed, game, 1.96)
    within = within_state(value, followed, state, game)
    assert plain["diff"] < -1.0 and plain["hi"] < -0.5                                         # the plain version sees a big benefit that is only the mix of situations
    assert abs(within["coef"]) < 0.15 and within["lo"] < 0.0 < within["hi"]                    # the within-situation one sees none
    assert within["share_used"] == pytest.approx(1.0) and within["states_used"] == 10


def test_only_situations_with_followed_and_not_followed_pitches_are_used():
    value, followed, state, game = fake_pitches()
    followed = followed.copy(); followed[state == 9] = False                                   # one situation where nothing was followed
    within = within_state(value, followed, state, game)
    assert within["states_used"] == 9 and within["states_total"] == 10 and 0.8 < within["share_used"] < 1.0
    none = within_state(value[:50], np.zeros(50, dtype=bool), state[:50], game[:50])
    assert none["coef"] is None and none["n_used"] == 0                                        # nothing followed: nothing to compare
