"""Known-answer tests of the decision engine on synthetic worlds W0-W3 and W6 (plan 5.2): simulate pitches from a world with a known truth,
fit the level-L0 outcome model with the real code path (bagged boosted trees), build the dynamics, solve, and compare with the truth."""
from __future__ import annotations

import numpy as np
import pytest

from treepolo_mlb_data.research import gbm, policy_build as pb, policy_core as pc, policy_worlds as pw

N_PA = 100_000
MEMBERS = 5
PARAMS = dict(max_depth=4, min_leaf=1000, learning_rate=0.15, max_iter=60, l2=1.0)     # smooth trees: the member spread sets the interval width
BACKEND = "xgboost" if gbm.xgboost_available() else "hgb"
CACHE: dict = {}


def fit_world(kind, seed=1, rows=None):
    w = pw.build_world(kind)
    cols = pw.simulate_pitches(w, N_PA, seed)
    if rows is not None:
        cols = {k: v[rows(cols)] for k, v in cols.items()}
    members, classes = pb.fit_outcome_members(cols, backend=BACKEND, device="cpu", params=PARAMS, reps=MEMBERS, seed=3)
    tensor = pb.outcome_tensor(members, classes, w.shape)
    obs, support, n = pb.usage_from_counts(cols, w.shape, min_count=100)
    return w, pb.members_dynamics(tensor, obs, support, n)


def world(kind):
    if kind not in CACHE:
        w, dyns = fit_world(kind)
        est = pb.analyze(dyns, w.env, w.shape)
        true = pb.analyze([w.truth], w.env, w.shape)
        occ = est.occupancy
        visited = occ > np.quantile(occ[occ > 0], 0.5)
        CACHE[kind] = dict(w=w, dyns=dyns, est=est, true=true, visited=visited)
    return CACHE[kind]


def cells(r):
    return r["est"].support & r["visited"][:, None] & r["true"].support


def advantage_stats(r, tau=0.005):
    est, true = r["est"], r["true"]
    mean, lo, hi = est.interval(est.advantage, 0.99)
    tadv = true.advantage[0]
    c = cells(r) & np.isfinite(mean) & np.isfinite(tadv)
    return mean, lo, hi, tadv, c, pc.detect(mean, lo, hi, est.support, tau)


@pytest.mark.parametrize("kind", ["W0", "W1", "W2", "W3"])
def test_member_intervals_cover_the_true_advantage(kind):
    r = world(kind)
    mean, lo, hi, tadv, c, _ = advantage_stats(r)
    assert c.sum() > 500
    assert ((tadv >= lo) & (tadv <= hi))[c].mean() >= 0.95


def test_w0_nothing_to_find():
    r = world("W0")
    mean, lo, hi, tadv, c, det = advantage_stats(r)
    assert np.abs(tadv[c]).max() < 1e-12                                   # the truth has no advantage anywhere
    assert det[c].mean() <= 0.01                                           # and almost nothing is flagged as detected
    # the same-data saving of the dynamic-programming policies is positive noise (winner's curse), the cross-evaluated one is not
    w = r["w"]
    _, a = fit_world("W0", seed=11, rows=lambda cols: cols["game_pk"] % 2 == 0)
    _, b = fit_world("W0", seed=11, rows=lambda cols: cols["game_pk"] % 2 == 1)
    assert abs(pb.cross_saving(a, b, w.env, w.shape)) < 0.0015


def test_w1_slider_better_everywhere():
    r = world("W1")
    est, true = r["est"], r["true"]
    mean, lo, hi, tadv, c, det = advantage_stats(r, tau=0.005)
    sl = c[:, pw.SL]
    assert (tadv[:, pw.SL][sl] < 0).all()                                  # the truth: the slider is better wherever it can be used
    assert (mean[:, pw.SL][sl] < 0).mean() >= 0.99                         # sign recovered
    big = sl & (tadv[:, pw.SL] < -0.02)
    assert big.sum() > 300 and det[:, pw.SL][big].mean() >= 0.6            # and flagged as detected in most cells where the true advantage is practically large
    obs, trust = est.obs, est.policies["dp_trust"]
    moved = (trust[:, pw.SL] - obs[:, pw.SL])[est.support[:, pw.SL] & r["visited"]]
    assert (moved > 0.05).mean() >= 0.9                                    # every trust region leans to the slider
    m, lo_s, hi_s = est.interval(est.saving_pa["dp_trust"], 0.99)
    assert lo_s <= true.saving_pa["dp_trust"][0] <= hi_s and m > 0.004      # the saving per plate appearance (same data: optimistic) contains the truth
    _, a = fit_world("W1", seed=11, rows=lambda cols: cols["game_pk"] % 2 == 0)
    _, b = fit_world("W1", seed=11, rows=lambda cols: cols["game_pk"] % 2 == 1)
    cross = pb.cross_saving(a, b, r["w"].env, r["w"].shape)
    assert 0.5 * true.saving_pa["dp_trust"][0] < cross < 1.5 * true.saving_pa["dp_trust"][0]


def true_best_agreement(r, gap=0.01):
    """Share of visited states, among those where the true best action beats the second best by at least ``gap``, in which the estimated
    unrestricted policy picks the true best action; also the number of such states."""

    eng = pc.Engine(r["w"].shape, r["w"].truth, r["w"].env)
    V, pi = eng.solve("dp_unrestricted")
    Q = np.where(eng.sup_eff, eng.q(V), np.inf)
    order = np.sort(Q, axis=1)
    sure = r["visited"] & ((order[:, 1] - order[:, 0]) >= gap)
    best = Q.argmin(axis=1)
    est_pick = r["est"].policies["dp_unrestricted"].argmax(axis=1)
    return float((est_pick[sure] == best[sure]).mean()), int(sure.sum())


def test_w2_repeating_pays():
    r = world("W2")
    est = r["est"]; shape = r["w"].shape
    p = shape.coords()[4]
    agree, n = true_best_agreement(r, 0.01)
    assert n >= 100 and agree >= 0.8                                       # where the truth has a clear best action, the estimate finds it
    agree3, n3 = true_best_agreement(r, 0.03)
    assert n3 >= 50 and agree3 >= 0.9                                      # and the clearer the better (mistakes sit where the options are nearly tied)
    # and that action is the previous type
    eng = pc.Engine(shape, r["w"].truth, r["w"].env)
    V, pi = eng.solve("dp_unrestricted")
    repeat = np.isin(p, pw.USED)
    assert (pi[repeat, :].argmax(axis=1) == p[repeat])[r["visited"][repeat]].mean() >= 0.9
    true_saving = r["true"].saving_pa["dp_unrestricted"][0]
    assert true_saving > 0.01 and est.saving_pa["dp_unrestricted"].mean() > 0.5 * true_saving


def test_w3_policy_switches_with_the_count():
    r = world("W3")
    est = r["est"]; shape = r["w"].shape
    b, k, o, base, p, rr = shape.coords()
    ahead = r["visited"] & (k > b) & est.support[:, pw.SL] & est.support[:, pw.CH]
    behind = r["visited"] & (b > k) & est.support[:, pw.SL] & est.support[:, pw.CH]
    pol = est.policies["dp_unrestricted"]
    assert ahead.sum() > 50 and behind.sum() > 50
    assert (pol[ahead, pw.SL] > 0.5).mean() >= 0.85
    assert (pol[behind, pw.CH] > 0.5).mean() >= 0.85


def test_w6_unsupported_action_is_never_recommended():
    r = world("W6")
    est = r["est"]; shape = r["w"].shape
    b = shape.coords()[0]
    three = b == 3
    assert not est.support[three, pw.CH].any()                              # nobody threw the changeup at three balls: not supported
    for kind in ("dp_unrestricted", "dp_trust", "greedy1"):
        assert (est.policies[kind][three, pw.CH] == 0).all(), kind
    assert not pc.detect(*est.interval(est.advantage, 0.99), est.support)[three, pw.CH].any()
