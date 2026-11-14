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


def test_every_state_has_a_behaviour_also_without_support():
    """A state with no action that passes the support rule keeps the shares as seen (rows sum to 1); a (count, previous type) never seen falls back to the count's shares."""

    from treepolo_mlb_data.research.profiles import PITCH_TYPES

    shape = pc.Shape()
    rng = np.random.default_rng(5)
    n = 400
    cols = {"pitch_type": rng.choice(["FF", "SL"], n), "prev1_pitch_type": np.where(rng.random(n) < 0.5, "FF", "none"), "balls": np.zeros(n), "strikes": np.zeros(n),
            "prior_streak": np.zeros(n)}
    obs, support, cnt = pb.usage_from_counts(cols, shape, min_count=100000)          # nothing passes the support rule
    assert not support.any()
    assert np.allclose(obs.sum(axis=1), 1.0)
    seen = shape.index(0, 0, 0, 0, shape.none, 0)
    assert obs[seen, PITCH_TYPES.index("FF")] > 0 and obs[seen, PITCH_TYPES.index("CH")] == 0
    unseen = shape.index(3, 2, 0, 0, PITCH_TYPES.index("CU"), 1)                       # no pitch in this situation: falls back to the whole group
    assert obs[unseen, PITCH_TYPES.index("FF")] > 0


def test_support_is_judged_by_count_and_type_and_shared_by_the_whole_count():
    """A type is supported in a count when it has enough pitches there (any previous type, streak, outs, bases); every state of the count shares the answer. Coverage lists numbers."""

    shape = pc.Shape()
    rng = np.random.default_rng(11)
    types = ["FF"] * 2100 + ["SL"] * 750 + ["CH"] * 150 + ["FF"] * 200
    balls = [0] * 3000 + [1] * 200
    n = len(types)
    prev = rng.choice(["FF", "SL", "CH"], n)
    cols = {"pitch_type": np.array(types), "prev1_pitch_type": prev, "balls": np.array(balls, dtype=float), "strikes": np.zeros(n), "prior_streak": np.ones(n)}
    obs, support, cnt = pb.usage_from_counts(cols, shape, min_share=0.05, min_count=300)
    ff, sl, ch = (PITCH_TYPES.index(t) for t in ("FF", "SL", "CH"))
    b, k, o, base, p, r = shape.coords()
    zero_zero = (b == 0) & (k == 0)
    assert (support[zero_zero][:, [ff, sl]]).all() and not support[zero_zero][:, ch].any()           # 150 CH pitches: below the minimum count, in every state of the count
    assert not support[(b == 1) & (k == 0)].any()                                                  # 200 pitches in the count: nothing reaches the minimum
    assert np.allclose(obs.sum(axis=1), 1.0)
    assert (obs[zero_zero][:, ch] == 0).all()                                                       # the unsupported type has no observed share where supported ones exist
    cov = pb.support_coverage(cols, shape, support, cnt)
    assert cov["states"] == shape.size and cov["supported_states"] == int(zero_zero.sum()) and cov["no_support_states"] == shape.size - int(zero_zero.sum())
    assert cov["pitches"] == n and cov["pitch_share_unsupported"] == (150 + 200) / n
    assert cov["supported_types"] == "FF SL"
    # a looser rule supports more
    _, looser, _ = pb.usage_from_counts(cols, shape, min_share=0.0, min_count=100)
    assert looser[zero_zero][:, ch].all() and looser[(b == 1) & (k == 0)][:, ff].all()
