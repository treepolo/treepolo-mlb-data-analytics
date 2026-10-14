from __future__ import annotations

import itertools

import numpy as np
import pytest

from treepolo_mlb_data.research import policy_core as pc

FOUL = pc.OC["foul"]


def random_world(shape, seed, foul=True, sparse_support=False):
    rng = np.random.default_rng(seed)
    S, A = shape.size, shape.n_actions
    P = rng.dirichlet(np.ones(pc.C) * 2.0, size=(S, A))
    if not foul:
        P[:, :, FOUL] = 0.0; P /= P.sum(axis=2, keepdims=True)
    share = rng.dirichlet(np.ones(A) * 3.0, size=S)
    support = np.ones((S, A), dtype=bool)
    if sparse_support:
        support = rng.random((S, A)) < 0.7
        support[np.arange(S), rng.integers(0, A, S)] = True
    obs = pc.masked_shares(share, support)
    re_pa = np.sort(rng.random((3, 8)) * 1.5, axis=1)[:, ::-1] * np.array([[1.0], [0.7], [0.4]]) + np.linspace(0, 0.8, 8)[None, :]
    env = pc.Environment(re_pa=re_pa, re_cnt=rng.random((shape.n_balls, shape.n_strikes, 3, 8)) + 0.2,
                         jbip=rng.random((A, 3, 8)) * 1.2, start=rng.random((3, 8)) + 0.1)
    return pc.Dynamics(P=P, obs=obs, support=support), env


def test_terminal_values_by_hand():
    shape = pc.Shape(4, 3, 2)
    env = pc.Environment(re_pa=np.arange(24, dtype=float).reshape(3, 8) / 10, re_cnt=np.zeros((4, 3, 3, 8)), jbip=np.full((2, 3, 8), 0.5), start=np.ones((3, 8)))
    nxt, term = pc.terminal_arrays(shape, env, lam=0.25)
    def s(b, k, o, base):
        return int(shape.index(b, k, o, base, 0, 0))
    ball, hbp, hr, k_, bip = pc.OC["ball"], pc.OC["hit_by_pitch"], pc.OC["in_play_home_run"], pc.OC["whiff"], pc.OC["in_play_non_hr"]
    # walk with the bases loaded: one run, bases stay loaded, same outs
    assert nxt[s(3, 0, 1, 7), 0, ball] == -1 and term[s(3, 0, 1, 7), 0, ball] == pytest.approx(1 + env.re_pa[1, 7])
    # walk with a man on first only: runners on first and second
    assert term[s(3, 0, 0, 1), 0, ball] == pytest.approx(env.re_pa[0, 3])
    # walk with a man on second: first and second, no run (the man on second is not forced)
    assert term[s(3, 0, 0, 2), 0, ball] == pytest.approx(env.re_pa[0, 3])
    # ball four is the end, ball three is not
    assert nxt[s(2, 0, 0, 0), 0, ball] >= 0
    # third strike: one more out; the third out ends the inning (value 0)
    assert term[s(0, 2, 0, 0), 0, k_] == pytest.approx(env.re_pa[1, 0]) and term[s(0, 2, 2, 5), 0, k_] == pytest.approx(0.0)
    # home run with two on: three runs, bases clear, plus the risk term
    assert term[s(0, 0, 0, 3), 0, hr] == pytest.approx(3 + env.re_pa[0, 0] + 0.25)
    assert term[s(0, 0, 0, 3), 1, bip] == pytest.approx(0.5) and term[s(0, 0, 0, 0), 0, hbp] == pytest.approx(env.re_pa[0, 1])
    # a foul with two strikes does not change the count but changes the previous type and the streak
    foul = pc.OC["foul"]
    st = s(0, 2, 0, 0)
    n1 = nxt[st, 1, foul]
    assert n1 == shape.index(0, 2, 0, 0, 1, 0)
    st_same = int(shape.index(0, 2, 0, 0, 1, 0))
    assert nxt[st_same, 1, foul] == shape.index(0, 2, 0, 0, 1, 1)
    assert nxt[int(shape.index(0, 2, 0, 0, 1, 2)), 1, foul] == shape.index(0, 2, 0, 0, 1, 2)      # the streak is capped at 3


def reachable(engine, start):
    seen = {start}; stack = [start]
    while stack:
        s = stack.pop()
        for a in range(engine.shape.n_actions):
            for c in range(pc.C):
                if engine.dyn.P[s, a, c] > 0 and engine.nxt[s, a, c] >= 0 and engine.nxt[s, a, c] not in seen:
                    seen.add(int(engine.nxt[s, a, c])); stack.append(int(engine.nxt[s, a, c]))
    return sorted(seen)


def exact_value(engine, states, choice, start):
    """Independent policy value: solve the linear system on the reachable states (no iteration)."""

    idx = {s: i for i, s in enumerate(states)}
    n = len(states); M = np.eye(n); rhs = np.zeros(n)
    for s in states:
        a = choice[s]
        for c in range(pc.C):
            p = engine.dyn.P[s, a, c]
            if engine.nxt[s, a, c] >= 0:
                M[idx[s], idx[int(engine.nxt[s, a, c])]] -= p
            else:
                rhs[idx[s]] += p * engine.term[s, a, c]
    return np.linalg.solve(M, rhs)


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_dp_equals_brute_force_over_all_deterministic_policies(seed):
    shape = pc.Shape(2, 1, 2)
    dyn, env = random_world(shape, seed, sparse_support=(seed == 3))
    eng = pc.Engine(shape, dyn, env)
    start = int(shape.start_index(1, 5))
    states = reachable(eng, start)
    assert 6 <= len(states) <= 16
    best = np.inf
    options = [[a for a in range(shape.n_actions) if eng.sup_eff[s, a]] for s in states]
    for combo in itertools.product(*options):
        choice = dict(zip(states, combo))
        best = min(best, exact_value(eng, states, choice, start)[states.index(start)])
    V, pi = eng.solve("dp_unrestricted")
    assert V[start] == pytest.approx(best, abs=1e-9)
    # and the evaluation of the returned policy agrees with the exact solve
    choice = {s: int(pi[s].argmax()) for s in states}
    assert exact_value(eng, states, choice, start)[states.index(start)] == pytest.approx(best, abs=1e-9)


def test_observed_value_equals_exact_linear_solve_with_random_mixed_policy():
    shape = pc.Shape(3, 2, 3)
    dyn, env = random_world(shape, 5)
    eng = pc.Engine(shape, dyn, env)
    V = eng.evaluate(eng.policy_observed())
    S = shape.size
    M = np.eye(S); rhs = np.zeros(S)
    pi = eng.policy_observed()
    for a in range(shape.n_actions):
        for c in range(pc.C):
            w = pi[:, a] * dyn.P[:, a, c]
            live = eng.nxt[:, a, c] >= 0
            np.subtract.at(M, (np.arange(S)[live], eng.nxt[live, a, c]), w[live])
            rhs[~live] += w[~live] * eng.term[~live, a, c]
    assert np.max(np.abs(np.linalg.solve(M, rhs) - V)) < 1e-9


def test_single_step_problem_reduces_to_argmin():
    shape = pc.Shape(1, 1, 4)           # the first ball or strike ends the plate appearance; no fouls, so there is no next pitch
    dyn, env = random_world(shape, 11, foul=False)
    eng = pc.Engine(shape, dyn, env)
    V, pi = eng.solve("dp_unrestricted")
    q1 = (dyn.P * eng.term).sum(axis=2)
    assert np.allclose(V, np.where(eng.sup_eff, q1, np.inf).min(axis=1))
    g = eng.policy_greedy1()
    assert np.array_equal(g.argmax(axis=1), pi.argmax(axis=1))      # nothing to look ahead to: greedy equals dp


def test_probabilities_and_ordering_of_policies():
    shape = pc.Shape(4, 3, 5)
    dyn, env = random_world(shape, 7, sparse_support=True)
    assert np.allclose(dyn.P.sum(axis=2), 1) and np.allclose(dyn.obs.sum(axis=1), 1)
    eng = pc.Engine(shape, dyn, env)
    solved = pc.solve_all(eng, trust=0.10)
    for kind, pi in solved.policies.items():
        assert np.allclose(pi.sum(axis=1), 1, atol=1e-9) and (pi[~eng.sup_eff] == 0).all() and (pi >= -1e-12).all(), kind
    s = solved.start
    assert s["dp_unrestricted"] <= s["dp_trust"] + 1e-9 <= s["observed"] + 2e-9
    assert s["dp_unrestricted"] <= s["greedy1"] + 1e-9 and s["dp_unrestricted"] <= s["const_FF"] + 1e-9
    # no state is worse off than under the observed policy
    assert (solved.values["dp_trust"] <= solved.values["observed"] + 1e-9).all()


def test_trust_policy_matches_linprog_and_the_two_extremes():
    from scipy.optimize import linprog

    shape = pc.Shape(2, 2, 6)
    dyn, env = random_world(shape, 9, sparse_support=True)
    eng = pc.Engine(shape, dyn, env)
    rng = np.random.default_rng(0)
    Q = rng.random((shape.size, shape.n_actions))
    pi = eng.trust_policy(Q, 0.10)
    for s in rng.integers(0, shape.size, 25):
        sup = eng.sup_eff[s]; obs = eng.obs_eff[s]
        bounds = [(max(obs[a] - 0.10, 0.0), min(obs[a] + 0.10, 1.0)) if sup[a] else (0.0, 0.0) for a in range(shape.n_actions)]
        res = linprog(Q[s], A_eq=np.ones((1, shape.n_actions)), b_eq=[1.0], bounds=bounds, method="highs")
        assert res.success
        assert float(pi[s] @ Q[s]) == pytest.approx(res.fun, abs=1e-9)
        assert np.abs(pi[s] - obs).max() <= 0.10 + 1e-9
    assert np.allclose(eng.trust_policy(Q, 0.0), eng.obs_eff)
    wide = eng.trust_policy(Q, 1.0)
    assert np.allclose(wide.argmax(axis=1), eng._argmin_policy(Q).argmax(axis=1))


def test_value_with_risk_term_is_linear_in_lambda_and_zero_lambda_is_plain():
    shape = pc.Shape(4, 3, 3)
    dyn, env = random_world(shape, 13)
    e0, e2 = pc.Engine(shape, dyn, env, lam=0.0), pc.Engine(shape, dyn, env, lam=2.0)
    pi = e0.policy_observed()
    v0, v2 = e0.evaluate(pi), e2.evaluate(pi)
    hr = e0.evaluate(pi, pc.hr_terminal(shape, e0.nxt))
    assert np.allclose(v2, v0 + 2.0 * hr, atol=1e-9)
    assert e0.start_value(v0) == pytest.approx(pc.Engine(shape, dyn, env).start_value(v0))
    assert 0 < e0.hr_probability(pi) < 1


def test_marginal_value_closed_form_matches_direct_recomputation():
    shape = pc.Shape(4, 3, 4)
    dyn, env = random_world(shape, 17)
    eng = pc.Engine(shape, dyn, env)
    Vobs = eng.evaluate(eng.policy_observed()); Q = eng.q(Vobs)
    m = pc.marginal_values(Q, eng.obs_eff, 0.10)
    rng = np.random.default_rng(1)
    for s in rng.integers(0, shape.size, 30):
        for a in range(shape.n_actions):
            pi = eng.obs_eff[s].copy()
            if 1 - pi[a] < 0.10:
                assert np.isnan(m[s, a]); continue
            new = pi * (1 - 0.10 / (1 - pi[a])); new[a] = pi[a] + 0.10
            assert m[s, a] == pytest.approx(new @ Q[s] - pi @ Q[s], abs=1e-12)


def test_occupancy_counts_pitches_per_plate_appearance():
    shape = pc.Shape(2, 1, 2)       # ball two or strike one ends it; fouls with one strike repeat
    P = np.zeros((shape.size, 2, pc.C)); P[:, :, pc.OC["ball"]] = 0.3; P[:, :, pc.OC["foul"]] = 0.2; P[:, :, pc.OC["whiff"]] = 0.5
    obs = np.full((shape.size, 2), 0.5)
    env = pc.Environment(re_pa=np.zeros((3, 8)), re_cnt=np.zeros((2, 1, 3, 8)), jbip=np.zeros((2, 3, 8)), start=np.eye(3, 8))
    eng = pc.Engine(shape, pc.Dynamics(P, obs, np.ones((shape.size, 2), dtype=bool)), env)
    # from 0-0: ends with prob .5 (whiff) at once; ball (.3) moves to 1-0 where ball/whiff end (.8) and foul (.2) repeats; foul at 0-0 repeats
    # N0 = 1 + .3*N1 + .2*N0 ; N1 = 1 + .2*N1  =>  N1 = 1.25, N0 = (1 + .375)/.8
    assert eng.pitches_per_pa(eng.policy_observed()) == pytest.approx((1 + 0.3 * 1.25) / 0.8, abs=1e-9)


def test_support_rule_and_member_interval():
    share, sup = pc.support_rule(np.array([[1000, 20, 400, 0], [100, 50, 20, 10]]), min_share=0.05, min_count=300)
    assert sup.tolist() == [[True, False, True, False], [False, False, False, False]]
    assert pc.masked_shares(share, sup)[0].sum() == pytest.approx(1.0) and pc.masked_shares(share, sup)[1].sum() == 0.0
    vals = np.array([[1.0], [1.2], [0.8], [1.0], [1.0]])
    mean, lo, hi = pc.member_interval(vals, 0.99)
    assert mean[0] == pytest.approx(1.0) and lo[0] < 1.0 < hi[0]
    assert pc.detect(np.array([0.01, 0.01, 0.001]), np.array([0.002, -0.002, 0.0005]), np.array([0.02, 0.02, 0.002]), np.array([True, True, True])).tolist() == [True, False, False]
