from __future__ import annotations

import numpy as np

from treepolo_mlb_data.research import policy_build as pb, policy_core as pc, policy_worlds as pw
from treepolo_mlb_data.research.profiles import PITCH_TYPES


def test_prior_streak_is_the_streak_position_of_the_previous_pitch():
    # plate appearance 1: FF FF FF SL; plate appearance 2: CH CH; rows given out of order on purpose
    game = np.array([1, 1, 1, 1, 1, 1]); ab = np.array([1, 1, 1, 1, 2, 2])
    number = np.array([1, 2, 3, 4, 1, 2]); index = np.array([1, 2, 3, 4, 1, 2]); streak = np.array([1, 2, 3, 1, 1, 2])
    shuffle = np.array([3, 0, 5, 1, 4, 2])
    got = pb.derive_prior_streak(game[shuffle], ab[shuffle], number[shuffle], index[shuffle], streak[shuffle])
    assert got.tolist() == np.array([0, 1, 2, 3, 0, 1])[shuffle].tolist()
    # a pitch whose predecessor was filtered out has no known history
    assert pb.derive_prior_streak([1, 1], [1, 1], [1, 3], [1, 3], [1, 2]).tolist() == [0.0, 0.0]
    assert pb.state_streak([0, 1, 2, 3, 7]).tolist() == [0, 0, 1, 2, 2]


def test_cell_columns_give_the_pitch_its_own_streak_position():
    shape = pc.Shape()
    cols = pb.cell_columns(shape)
    b, k, o, base, p, r = shape.coords()
    S, A = shape.size, shape.n_actions
    st = np.repeat(np.arange(S), A); act = np.tile(np.arange(A), S)
    same = (p[st] == act)
    assert (cols["streak_pos"][~same] == 1).all()
    assert (cols["streak_pos"][same & (r[st] == 0)] == 2).all() and (cols["streak_pos"][same & (r[st] == 1)] == 3).all() and (cols["streak_pos"][same & (r[st] == 2)] == 3).all()
    none = p[st] == A
    assert (np.asarray(cols["prev1_pitch_type"])[none] == "none").all() and (cols["streak_pos"][none] == 1).all()
    assert np.array_equal(np.asarray(cols["pitch_type"])[:A], np.array(PITCH_TYPES))


def test_training_rows_and_grid_rows_use_the_same_streak_coding():
    """A pitch thrown from a state in the simulated data must carry the same streak feature as the grid row of that state and action."""

    w = pw.build_world("W2")
    cols = pw.simulate_pitches(w, 3000, 5)
    shape = w.shape
    grid = pb.cell_columns(shape)
    b = cols["balls"].astype(int); k = cols["strikes"].astype(int); o = cols["outs_when_up"].astype(int); base = cols["bases"]
    prev = np.array([PITCH_TYPES.index(t) if t in PITCH_TYPES else shape.none for t in cols["prev1_pitch_type"]])
    r = pb.state_streak(cols["prior_streak"])
    act = np.array([PITCH_TYPES.index(t) for t in cols["pitch_type"]])
    state = shape.index(b, k, o, base, prev, r)
    row = state * shape.n_actions + act
    assert np.array_equal(grid["streak_pos"][row], cols["streak_pos"])
    assert np.array_equal(np.asarray(grid["prev1_pitch_type"])[row], cols["prev1_pitch_type"])


def test_observed_shares_recover_the_world_behaviour():
    w = pw.build_world("W1")
    cols = pw.simulate_pitches(w, 60_000, 2)
    obs, support, n = pb.usage_from_counts(cols, w.shape, min_count=100)
    b, k, o, base, p, r = w.shape.coords()
    valid = ((p == w.shape.none) == ((b == 0) & (k == 0))) & (n.sum(axis=1) > 400)
    assert valid.sum() > 300
    assert np.abs(obs - w.truth.obs)[valid].mean() < 0.02
