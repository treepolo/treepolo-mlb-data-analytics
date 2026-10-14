"""Player descriptors: lookup correctness, leave-game-out (no leakage of the row's own game), shrinkage, cold start."""
from __future__ import annotations

import numpy as np
import pytest

from treepolo_mlb_data.research.profiles import KeyedSums, ProfileModel, icc_strength_mean, icc_strength_rate
from treepolo_mlb_data.research.synthetic import simulate_players


@pytest.fixture(scope="module")
def world():
    return simulate_players("P7", n_pitchers=30, n_batters=60, n_games=500, seed=3)


def test_keyed_sums_lookup_matches_brute_force():
    rng = np.random.default_rng(0)
    a = rng.integers(0, 5, 400); b = np.array(list("xyz"))[rng.integers(0, 3, 400)]
    v = rng.standard_normal((400, 3))
    ks = KeyedSums().fit([a, b], v)
    qa = np.array([0, 4, 9, 2]); qb = np.array(["x", "z", "x", "w"])
    got = ks.lookup([qa, qb])
    for i in range(4):
        m = (a == qa[i]) & (b == qb[i])
        assert np.allclose(got[i], v[m].sum(axis=0) if m.any() else 0.0)


def test_prior_strengths_are_sensible():
    rng = np.random.default_rng(1)
    n = rng.integers(50, 400, 200).astype(float)
    flat = rng.binomial(n.astype(int), 0.3).astype(float)                 # everyone the same: shrink hard
    spread = rng.binomial(n.astype(int), np.clip(rng.normal(0.3, 0.1, 200), 0.02, 0.9)).astype(float)   # real differences: shrink lightly
    assert icc_strength_rate(flat, n) > 5 * icc_strength_rate(spread, n)
    cells = rng.integers(20, 200, 150).astype(float); mu = rng.normal(90, 2, 150)
    x = np.array([rng.normal(m, 3, int(c)).sum() for m, c in zip(mu, cells)]); x2 = np.array([(rng.normal(m, 3, int(c)) ** 2).sum() for m, c in zip(mu, cells)])
    assert 0.5 < icc_strength_mean(x, x2, cells) < 30     # within sd 3, between sd 2 -> k about 9/4


@pytest.mark.parametrize("side", ["pitcher", "batter", "repertoire"])
def test_leave_game_out_ignores_the_rows_own_game(world, side):
    model = ProfileModel().fit(world)
    base = model.transform(world, side, leave_game_out=True)
    game = 17
    changed = {k: v.copy() for k, v in world.items()}
    mask = changed["game_pk"] == game
    changed["outcome"][mask] = np.where(changed["outcome"][mask] == "whiff", "ball", "whiff")
    changed["release_speed"][mask] += 25.0
    # the model is refit on the modified world; rows of `game` must see the same descriptors as before
    model2 = ProfileModel().fit(changed)
    after = model2.transform(changed, side, leave_game_out=True)
    # constants (prior strengths) move a little when the data changes; compare the rows of the edited game with a tolerance that
    # would still catch a leak (a leak shifts a rate by ~ 1 / n of a thousand-pitch player, i.e. far above this)
    other = ~mask
    assert np.abs(after[mask] - base[mask]).max() < 0.02
    assert np.abs(after[other] - base[other]).max() > np.abs(after[mask] - base[mask]).max()


def test_without_leave_game_out_the_descriptor_contains_the_target():
    """The reason for leave-game-out: in a world where outcomes are pure noise, a descriptor that includes the pitch's own game
    correlates with the outcome, one that excludes it does not."""

    d = simulate_players("P0", n_pitchers=20, n_batters=40, n_games=400, pitches_per_game=40, seed=5)
    model = ProfileModel().fit(d)
    whiff = (d["outcome"] == "whiff").astype(float)
    swing = np.isin(d["outcome"], ["whiff", "foul", "in_play_out", "in_play_home_run"])
    names = model.feature_names("pitcher")
    j = names.index("p_whiff_per_swing")
    leaky = model.transform(d, "pitcher", leave_game_out=False)[:, j]; clean = model.transform(d, "pitcher", leave_game_out=True)[:, j]
    c_leaky = np.corrcoef(leaky[swing], whiff[swing])[0, 1]; c_clean = np.corrcoef(clean[swing], whiff[swing])[0, 1]
    assert c_leaky > 0.02 and abs(c_clean) < 0.015 < c_leaky


def test_unseen_player_gets_the_league_average(world):
    model = ProfileModel().fit(world)
    stranger = {k: v[:5].copy() for k, v in world.items()}
    stranger["pitcher"][:] = 999_999; stranger["batter"][:] = 888_888
    pit = model.transform(stranger, "pitcher", leave_game_out=False)
    names = model.feature_names("pitcher")
    for j, name in enumerate(names):
        if name in model.league:
            assert pit[0, j] == pytest.approx(model.league[name], rel=1e-9)
    assert pit[0, names.index("n_pitcher_pitches")] == 0
    bat = model.transform(stranger, "batter", leave_game_out=False)
    assert bat[0, 0] == pytest.approx(model.league["b_swing_rate"], rel=1e-9)


def test_thin_players_are_shrunk_toward_the_league(world):
    model = ProfileModel().fit(world)
    j = model.feature_names("batter").index("b_swing_rate")
    est = model.transform(world, "batter", leave_game_out=False)[:, j]
    batter = world["batter"]; swing = np.isin(world["outcome"], ["whiff", "foul", "in_play_out", "in_play_home_run"]).astype(float)
    league = swing.mean()
    counts = {b: int((batter == b).sum()) for b in np.unique(batter)}
    thin = min(counts, key=counts.get); thick = max(counts, key=counts.get)
    raw = lambda b: swing[batter == b].mean()
    gap = lambda b: abs(est[batter == b][0] - league) / max(abs(raw(b) - league), 1e-9)
    assert gap(thin) < gap(thick) or counts[thin] > 0.9 * counts[thick]


def test_arsenal_descriptor_tracks_the_pitchers_velocity(world):
    model = ProfileModel().fit(world)
    names = model.feature_names("pitcher"); j = names.index("a_release_speed")
    est = model.transform(world, "pitcher", leave_game_out=True)[:, j]
    fastball = world["pitch_type"] == "FF"
    truth = world["velocity_truth"][fastball]
    assert np.corrcoef(est[fastball], truth)[0, 1] > 0.95
