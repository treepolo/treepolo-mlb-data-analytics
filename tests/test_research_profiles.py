"""Player descriptors: lookup correctness, history mode (a row is described from earlier games only), shrinkage, cold start."""
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


def test_history_descriptor_equals_a_brute_force_computation_from_earlier_games_only(world):
    model = ProfileModel().fit(world)
    out_p = model.transform(world, "pitcher", history=True); out_b = model.transform(world, "batter", history=True)
    jp = model.feature_names("pitcher").index("p_whiff_per_swing"); jb = model.feature_names("batter").index("b_swing_rate")
    swing = np.isin(world["outcome"], ["whiff", "foul", "in_play_out", "in_play_home_run"]).astype(float); whiff = (world["outcome"] == "whiff").astype(float)
    order = np.searchsorted(np.unique(world["game_pk"]), world["game_pk"])
    rng = np.random.default_rng(0)
    for i in rng.choice(len(order), 40, replace=False):
        for entity, metric_num, metric_den, name, col, out in (("pitcher", whiff, swing, "p_whiff_per_swing", jp, out_p), ("batter", swing, np.ones_like(swing), "b_swing_rate", jb, out_b)):
            same = (world[entity] == world[entity][i]) & (order < order[i])
            grp = same & (world["group"] == world["group"][i])
            r_all = (metric_num[same].sum() + model.k_all[name] * model.league[name]) / (metric_den[same].sum() + model.k_all[name])
            expected = (metric_num[grp].sum() + model.k_group[name] * r_all) / (metric_den[grp].sum() + model.k_group[name])
            assert out[i, col] == pytest.approx(expected, rel=1e-9, abs=1e-12)


def test_whole_period_descriptor_contains_the_target_but_history_does_not():
    """Why history mode exists: in a world where outcomes are pure noise, a batter descriptor that includes the pitch's own game correlates with
    the outcome; one built from earlier games does not."""

    d = simulate_players("P0", n_pitchers=40, n_batters=60, n_games=800, pitches_per_game=40, seed=5)
    model = ProfileModel().fit(d)
    swing = np.isin(d["outcome"], ["whiff", "foul", "in_play_out", "in_play_home_run"]).astype(float)
    j = model.feature_names("batter").index("b_swing_rate")
    leaky = model.transform(d, "batter", history=False)[:, j]; clean = model.transform(d, "batter", history=True)[:, j]
    late = d["game_pk"] > 400      # early games have little history: the descriptor is mostly the league average there
    c_leaky = np.corrcoef(leaky[late], swing[late])[0, 1]; c_clean = np.corrcoef(clean[late], swing[late])[0, 1]
    assert c_leaky > 0.04 and abs(c_clean) < 0.015


def test_unseen_player_gets_the_league_average(world):
    model = ProfileModel().fit(world)
    stranger = {k: v[:5].copy() for k, v in world.items()}
    stranger["pitcher"][:] = 999_999; stranger["batter"][:] = 888_888
    pit = model.transform(stranger, "pitcher", history=False)
    names = model.feature_names("pitcher")
    for j, name in enumerate(names):
        if name in model.league:
            assert pit[0, j] == pytest.approx(model.league[name], rel=1e-9)
    assert pit[0, names.index("n_pitcher_pitches")] == 0
    bat = model.transform(stranger, "batter", history=False)
    assert bat[0, 0] == pytest.approx(model.league["b_swing_rate"], rel=1e-9)


def test_thin_players_are_shrunk_toward_the_league(world):
    model = ProfileModel().fit(world)
    j = model.feature_names("batter").index("b_swing_rate")
    est = model.transform(world, "batter", history=False)[:, j]
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
    est = model.transform(world, "pitcher", history=True)[:, j]
    fastball = world["pitch_type"] == "FF"
    truth = world["velocity_truth"][fastball]
    assert np.corrcoef(est[fastball], truth)[0, 1] > 0.95


def test_sum_table_before_matches_brute_force():
    from treepolo_mlb_data.research.profiles import SumTable

    rng = np.random.default_rng(2)
    ent = rng.integers(0, 6, 500); order = rng.integers(0, 30, 500); v = rng.standard_normal((500, 2))
    table = SumTable().fit([ent], order, v)
    qe = np.array([0, 3, 5, 9]); qo = np.array([0, 10, 29, 5])
    got = table.get([qe], qo, history=True)
    for i in range(4):
        m = (ent == qe[i]) & (order < qo[i])
        assert np.allclose(got[i], v[m].sum(axis=0) if m.any() else 0.0)
    assert np.allclose(table.get([qe], None, history=False)[1], v[ent == 3].sum(axis=0))


def test_unlisted_pitch_types_fall_back_to_the_league_mean(world):
    """Real data has pitch types outside the frozen list (and empty strings); transform must not fail on them."""
    model = ProfileModel().fit(world)
    odd = dict(world); pt = np.asarray(world["pitch_type"]).astype(object).copy(); pt[::7] = ""; pt[3::11] = "EP"; odd["pitch_type"] = pt.astype(str)
    assert np.isfinite(model.transform(odd, "pitcher", history=True)).all()
