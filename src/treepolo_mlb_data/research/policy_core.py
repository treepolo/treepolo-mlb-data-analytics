"""Decision engine of P6: a plate appearance as a Markov decision process over (count, previous pitch type, streak), solved by dynamic programming.

A "state" is (balls, strikes, outs, bases, previous type or none, streak 1/2/3+). Outs and bases do not change inside a plate appearance, so they only
select the terminal values and the outcome probabilities. Everything is dense numpy: the dynamics are an array ``P[state, action, outcome]``.

Value = expected runs from now to the end of the half inning (``runs on this event + run expectancy after the plate appearance``), plus ``lam`` per
home run for the risk-averse objective. Policies are arrays ``pi[state, action]``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Sequence

import numpy as np

OUTCOMES = ("ball", "called_strike", "foul", "foul_tip", "hit_by_pitch", "in_play_home_run", "in_play_non_hr", "whiff")   # = sorted classes of the default class_merge
C = len(OUTCOMES)
OC = {name: i for i, name in enumerate(OUTCOMES)}
STRIKE_LIKE = ("called_strike", "foul_tip", "whiff")
STREAKS = 3
POLICIES = ("observed", "const_FF", "greedy1", "dp_unrestricted", "dp_trust")


@dataclass(frozen=True)
class Shape:
    """Sizes of the state space. The real game is ``Shape(4, 3, 9)``; tests use tiny shapes that can be enumerated."""

    n_balls: int = 4          # ball number n_balls ends the plate appearance (walk)
    n_strikes: int = 3        # strike number n_strikes ends it (strikeout)
    n_actions: int = 9

    @property
    def dims(self) -> tuple[int, ...]:
        return (self.n_balls, self.n_strikes, 3, 8, self.n_actions + 1, STREAKS)

    @property
    def size(self) -> int:
        return int(np.prod(self.dims))

    @property
    def none(self) -> int:
        return self.n_actions       # index of "no previous pitch"

    def index(self, b, k, o, base, prev, streak):
        return np.ravel_multi_index((np.asarray(b), np.asarray(k), np.asarray(o), np.asarray(base), np.asarray(prev), np.asarray(streak)), self.dims)

    def coords(self) -> tuple[np.ndarray, ...]:
        return np.unravel_index(np.arange(self.size), self.dims)

    def start_index(self, o, base):
        return self.index(0, 0, o, base, self.none, 0)


@dataclass
class Dynamics:
    """What the policy faces: outcome probabilities, the observed behaviour and which actions may be used."""

    P: np.ndarray                  # [S, A, C] outcome probabilities (rows sum to 1)
    obs: np.ndarray                # [S, A] observed share of each action (support-masked, renormalised); 0 for unsupported actions
    support: np.ndarray            # [S, A] bool
    n: np.ndarray | None = None    # [S, A] pitches behind each cell (optional, for flags)


@dataclass
class Environment:
    re_pa: np.ndarray              # [3, 8] run expectancy at 0-0 for a new plate appearance, by outs and bases (3 outs = 0 is handled in code)
    re_cnt: np.ndarray             # [n_balls, n_strikes, 3, 8] run expectancy by count (one-step values and R1)
    jbip: np.ndarray               # [A, 3, 8] value after a ball in play that is not a home run (runs + RE afterwards), by the pitch type
    start: np.ndarray              # [3, 8] how often a plate appearance starts in each (outs, bases)


# ------------------------------------------------------------------------------------------------ terminal values
def _walk_bases(base: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Bases and runs after a forced advance (walk or hit by pitch)."""

    b1, b2, b3 = (base & 1) > 0, (base & 2) > 0, (base & 4) > 0
    new = np.where(~b1, base | 1, np.where(~b2, base | 3, 7))
    runs = np.where(b1 & b2 & b3, 1, 0)
    return new, runs


def _popcount3(base: np.ndarray) -> np.ndarray:
    return (base & 1) + ((base >> 1) & 1) + ((base >> 2) & 1)


def terminal_arrays(shape: Shape, env: Environment, lam: float = 0.0):
    """``nxt[S, A, C]`` (flat index of the next state, -1 when the plate appearance ends) and ``term[S, A, C]`` (value at the end)."""

    S, A = shape.size, shape.n_actions
    b, k, o, base, p, r = shape.coords()
    nxt = np.full((S, A, C), -1, dtype=np.int64); term = np.zeros((S, A, C))
    re_pa = np.vstack([env.re_pa, np.zeros((1, 8))])          # row 3: after the third out the half inning is over
    walk_base, walk_runs = _walk_bases(base)
    for a in range(A):
        r2 = np.where(p == a, np.minimum(r + 1, STREAKS - 1), 0)
        for name, c in OC.items():
            if name == "ball":
                ended = b + 1 >= shape.n_balls
                next_index = shape.index(np.minimum(b + 1, shape.n_balls - 1), k, o, base, a, r2)
                value = walk_runs + re_pa[o, walk_base]
            elif name in STRIKE_LIKE:
                ended = k + 1 >= shape.n_strikes
                next_index = shape.index(b, np.minimum(k + 1, shape.n_strikes - 1), o, base, a, r2)
                value = re_pa[np.minimum(o + 1, 3), base]
            elif name == "foul":
                ended = np.zeros(S, dtype=bool)
                next_index = shape.index(b, np.where(k >= shape.n_strikes - 1, k, k + 1), o, base, a, r2)
                value = np.zeros(S)
            elif name == "hit_by_pitch":
                ended = np.ones(S, dtype=bool); next_index = np.zeros(S, dtype=np.int64)
                value = walk_runs + re_pa[o, walk_base]
            elif name == "in_play_home_run":
                ended = np.ones(S, dtype=bool); next_index = np.zeros(S, dtype=np.int64)
                value = 1 + _popcount3(base) + re_pa[o, 0] + lam
            else:   # in_play_non_hr
                ended = np.ones(S, dtype=bool); next_index = np.zeros(S, dtype=np.int64)
                value = env.jbip[a, o, base]
            nxt[:, a, c] = np.where(ended, -1, next_index)
            term[:, a, c] = np.where(ended, value, 0.0)
    return nxt, term


def hr_terminal(shape: Shape, nxt: np.ndarray) -> np.ndarray:
    """Terminal reward that counts home runs only (so the evaluated value is the probability the plate appearance ends in one)."""

    term = np.zeros(nxt.shape)
    term[:, :, OC["in_play_home_run"]] = 1.0
    return term


# ------------------------------------------------------------------------------------------------ engine
class Engine:
    def __init__(self, shape: Shape, dyn: Dynamics, env: Environment, lam: float = 0.0, tol: float = 1e-13, max_iter: int = 3000):
        self.shape, self.dyn, self.env, self.lam, self.tol, self.max_iter = shape, dyn, env, float(lam), tol, max_iter
        self.nxt, self.term = terminal_arrays(shape, env, lam)
        self._live = self.nxt >= 0
        self._safe = np.where(self._live, self.nxt, 0)
        sup = dyn.support.copy(); empty = ~sup.any(axis=1)
        sup[empty] = dyn.obs[empty] > 0       # a state with no supported action keeps what was observed there (flagged by ``no_support``)
        self.sup_eff = sup
        self.no_support = empty
        self.obs_eff = dyn.obs

    def rebind(self, dyn: Dynamics) -> "Engine":
        """Another engine on the same state structure and terminal values (they do not depend on the outcome probabilities) with different dynamics."""

        import copy

        other = copy.copy(self)
        other.dyn = dyn
        sup = dyn.support.copy(); empty = ~sup.any(axis=1)
        sup[empty] = dyn.obs[empty] > 0
        other.sup_eff, other.no_support, other.obs_eff = sup, empty, dyn.obs
        return other

    # --- values
    def q(self, V: np.ndarray, term: np.ndarray | None = None, rows: np.ndarray | None = None) -> np.ndarray:
        """Q[s, a] for all states, or only for ``rows`` (state indices) when given."""

        t = self.term if term is None else term
        if rows is None:
            return (self.dyn.P * np.where(self._live, V[self._safe], t)).sum(axis=2)
        return (self.dyn.P[rows] * np.where(self._live[rows], V[self._safe[rows]], t[rows])).sum(axis=2)

    def evaluate(self, pi: np.ndarray, term: np.ndarray | None = None, active: np.ndarray | None = None, tol: float | None = None) -> np.ndarray:
        """Value of a policy. ``active`` limits the work to a set of states that only lead to each other (the states of one outs-and-bases cell)."""

        tol = self.tol if tol is None else tol
        V = np.zeros(self.shape.size)
        for _ in range(self.max_iter):
            if active is None:
                new = (pi * self.q(V, term)).sum(axis=1)
                done = np.max(np.abs(new - V)) < tol
                V = new
            else:
                new = (pi[active] * self.q(V, term, active)).sum(axis=1)
                done = np.max(np.abs(new - V[active])) < tol
                V[active] = new
            if done:
                break
        return V

    def one_step(self) -> np.ndarray:
        """Q1[s, a]: value of the next pitch only, with the run expectancy of the new count after a non-final outcome."""

        sh = self.shape; b, k, o, base, _, _ = sh.coords(); S = sh.size
        re_cnt = self.env.re_cnt
        cont = np.zeros((S, sh.n_actions, C))
        for name, c in OC.items():
            if name == "ball":
                nb = np.minimum(b + 1, sh.n_balls - 1); val = re_cnt[nb, k, o, base]
            elif name in STRIKE_LIKE:
                nk = np.minimum(k + 1, sh.n_strikes - 1); val = re_cnt[b, nk, o, base]
            elif name == "foul":
                nk = np.where(k >= sh.n_strikes - 1, k, k + 1); val = re_cnt[b, nk, o, base]
            else:
                val = np.zeros(S)
            cont[:, :, c] = val[:, None]
        cont = np.where(self._live, cont, self.term)
        return (self.dyn.P * cont).sum(axis=2)

    # --- policies
    def policy_observed(self) -> np.ndarray:
        return self.obs_eff.copy()

    def policy_const(self, action: int) -> np.ndarray:
        pi = self.obs_eff.copy()
        use = self.sup_eff[:, action]
        pi[use] = 0.0; pi[use, action] = 1.0
        return pi

    def _argmin_policy(self, Q: np.ndarray) -> np.ndarray:
        masked = np.where(self.sup_eff, Q, np.inf)
        pi = np.zeros_like(Q); pi[np.arange(len(Q)), masked.argmin(axis=1)] = 1.0
        return pi

    def policy_greedy1(self) -> np.ndarray:
        return self._argmin_policy(self.one_step())

    def trust_policy(self, Q: np.ndarray, delta: float) -> np.ndarray:
        """Per state, the shares within ``delta`` of the observed shares that minimise the expected Q (see ``trust_shares``)."""

        return trust_shares(Q, self.obs_eff, self.sup_eff, delta)

    def solve(self, kind: str, trust: float = 0.10, term: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray]:
        """(V, pi) of one policy kind; the dynamic-programming kinds iterate the Bellman operator to convergence."""

        if kind == "observed":
            pi = self.policy_observed(); return self.evaluate(pi, term), pi
        if kind == "const_FF":
            pi = self.policy_const(0); return self.evaluate(pi, term), pi
        if kind == "greedy1":
            pi = self.policy_greedy1(); return self.evaluate(pi, term), pi
        if kind not in ("dp_unrestricted", "dp_trust"):
            raise ValueError(f"unknown policy {kind!r}")
        V = np.zeros(self.shape.size)
        for _ in range(self.max_iter):
            Q = self.q(V, term)
            pi = self._argmin_policy(Q) if kind == "dp_unrestricted" else self.trust_policy(Q, trust)
            new = (pi * Q).sum(axis=1)
            done = np.max(np.abs(new - V)) < self.tol
            V = new
            if done:
                break
        return V, pi

    # --- plate-appearance level numbers
    def start_value(self, V: np.ndarray) -> float:
        w = self.env.start
        idx = np.array([[self.shape.start_index(o, B) for B in range(8)] for o in range(3)])
        return float((w * V[idx]).sum() / w.sum())

    def occupancy(self, pi: np.ndarray, max_steps: int = 400) -> np.ndarray:
        """Expected number of pitches thrown from each state during one plate appearance (start distribution ``env.start``)."""

        S = self.shape.size
        mu = np.zeros(S)
        for o in range(3):
            for B in range(8):
                mu[self.shape.start_index(o, B)] += self.env.start[o, B]
        mu /= mu.sum()
        total = np.zeros(S)
        for _ in range(max_steps):
            total += mu
            flow = mu[:, None, None] * pi[:, :, None] * self.dyn.P
            nxt = np.bincount(self._safe[self._live], weights=flow[self._live], minlength=S)
            mu = nxt
            if mu.sum() < 1e-14:
                break
        return total

    def pitches_per_pa(self, pi: np.ndarray) -> float:
        return float(self.occupancy(pi).sum())

    def hr_probability(self, pi: np.ndarray) -> float:
        V = self.evaluate(pi, hr_terminal(self.shape, self.nxt))
        return self.start_value(V)


def trust_shares(Q: np.ndarray, obs: np.ndarray, sup: np.ndarray, delta: float) -> np.ndarray:
    """Exact solution of the small linear programme per state: start from the lower bounds ``max(obs - delta, 0)`` and give the remaining probability to the
    cheapest actions up to their upper bounds ``min(obs + delta, 1)``. Unsupported actions stay at zero."""

    low = np.where(sup, np.maximum(obs - delta, 0.0), 0.0)
    up = np.where(sup, np.minimum(obs + delta, 1.0), 0.0)
    room = up - low
    remaining = np.maximum(1.0 - low.sum(axis=1), 0.0)
    order = np.argsort(np.where(sup, Q, np.inf), axis=1, kind="stable")
    sorted_room = np.take_along_axis(room, order, axis=1)
    before = np.cumsum(sorted_room, axis=1) - sorted_room
    add_sorted = np.clip(remaining[:, None] - before, 0.0, sorted_room)
    add = np.zeros_like(add_sorted); np.put_along_axis(add, order, add_sorted, axis=1)
    pi = low + add
    total = pi.sum(axis=1, keepdims=True)
    return pi / np.where(total > 0, total, 1.0)


# ------------------------------------------------------------------------------------------------ numbers built on Q
def marginal_values(Q: np.ndarray, obs: np.ndarray, delta: float = 0.10) -> np.ndarray:
    """Change of the expected value per pitch when action ``a`` gains ``delta`` share and the others give it up in proportion:
    ``delta * (Q[s,a] - Qbar_{-a}[s])``. NaN where the shift is impossible (``delta`` larger than what the others hold)."""

    total = (obs * Q).sum(axis=1, keepdims=True)
    rest = 1.0 - obs
    with np.errstate(divide="ignore", invalid="ignore"):
        qbar = (total - obs * Q) / rest
        out = delta * (Q - qbar)
    return np.where((rest >= delta) & (rest > 1e-12), out, np.nan)


def advantage(Q: np.ndarray, obs: np.ndarray) -> np.ndarray:
    """``Q[s,a] - Qbar_{-a}[s]``: how much better throwing ``a`` is than the observed mix of the other types, per pitch (the quantity 4.2 judges
    against the practical size tau; the marginal value is this times the share moved)."""

    total = (obs * Q).sum(axis=1, keepdims=True)
    rest = 1.0 - obs
    with np.errstate(divide="ignore", invalid="ignore"):
        out = Q - (total - obs * Q) / rest
    return np.where(rest > 1e-12, out, np.nan)


def best_small_move(Q: np.ndarray, engine: Engine, delta: float = 0.10) -> tuple[np.ndarray, np.ndarray]:
    """(new shares, change of the one-step expected value) inside the trust region, continuation as in ``Q``."""

    pi = engine.trust_policy(Q, delta)
    return pi, (pi * Q).sum(axis=1) - (engine.obs_eff * Q).sum(axis=1)


def support_rule(counts: np.ndarray, min_share: float = 0.05, min_count: int = 300) -> tuple[np.ndarray, np.ndarray]:
    """From pitch counts ``counts[..., A]`` of a cell (the last axis is the action): (shares, supported)."""

    counts = np.asarray(counts, dtype=np.float64)
    total = counts.sum(axis=-1, keepdims=True)
    share = np.divide(counts, total, out=np.zeros_like(counts), where=total > 0)
    return share, (share >= min_share) & (counts >= min_count)


def masked_shares(share: np.ndarray, supported: np.ndarray) -> np.ndarray:
    """Observed shares with unsupported actions removed and the rest renormalised."""

    m = np.where(supported, share, 0.0); t = m.sum(axis=-1, keepdims=True)
    return np.divide(m, t, out=np.zeros_like(m), where=t > 0)


# ------------------------------------------------------------------------------------------------ bagged members
def t_critical(members: int, confidence: float) -> float:
    from scipy import stats

    return float(stats.t.ppf(0.5 + confidence / 2.0, max(members - 1, 1)))


def member_interval(values: np.ndarray, confidence: float = 0.99) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Mean and mean +- t * SD over the bagged members (axis 0), the interval convention of the whole study."""

    m = values.shape[0]
    with np.errstate(all="ignore"), __import__("warnings").catch_warnings():
        __import__("warnings").simplefilter("ignore", RuntimeWarning)
        mean = np.nanmean(values, axis=0); sd = np.nanstd(values, axis=0, ddof=1) if m > 1 else np.zeros_like(mean)
    t = t_critical(m, confidence)
    return mean, mean - t * sd, mean + t * sd


def detect(mean: np.ndarray, lo: np.ndarray, hi: np.ndarray, supported: np.ndarray, tau: float = 0.005) -> np.ndarray:
    """4.2 criteria 1, 5 and 6 on one set of members (the other criteria need the anchor and the replications): the interval excludes 0,
    the size is at least ``tau`` and the action is supported."""

    return ((lo > 0) | (hi < 0)) & (np.abs(mean) >= tau) & supported


@dataclass
class Solved:
    """Results of one dynamics (one bagged member or the full fit) for all policies."""

    values: dict[str, np.ndarray] = field(default_factory=dict)       # policy -> V[S]
    policies: dict[str, np.ndarray] = field(default_factory=dict)     # policy -> pi[S, A]
    start: dict[str, float] = field(default_factory=dict)             # policy -> expected value at the start of a plate appearance
    q_obs: np.ndarray | None = None                                   # Q[S, A] with the observed continuation


def solve_all(engine: Engine, kinds: Sequence[str] = POLICIES, trust: float = 0.10) -> Solved:
    out = Solved()
    for kind in kinds:
        V, pi = engine.solve(kind, trust)
        out.values[kind] = V; out.policies[kind] = pi; out.start[kind] = engine.start_value(V)
    if "observed" in out.values:
        out.q_obs = engine.q(out.values["observed"])
    return out
