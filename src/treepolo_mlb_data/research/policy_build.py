"""From fitted models and pitch counts to the arrays the decision engine needs, and the analysis of a set of bagged members (plan 2.8, 4.2, 4.3)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

import numpy as np

from . import gbm
from . import league_core as lc
from . import modeling as md
from . import policy_core as pc
from .profiles import PITCH_TYPES


# ------------------------------------------------------------------------------------------------ the grid of situations
def cell_columns(shape: pc.Shape, group: str = "RvR") -> dict[str, np.ndarray]:
    """One row per (state, action): the columns ``league_core.level_matrix`` reads, so the fitted level-L0 model can be asked about every situation."""

    S, A = shape.size, shape.n_actions
    b, k, o, base, p, r = shape.coords()
    rows = np.repeat(np.arange(S), A); action = np.tile(np.arange(A), S)
    prev = np.array(list(PITCH_TYPES) + ["none"])[np.minimum(p, A)]
    return dict(game_pk=np.zeros(S * A, dtype=np.int64), balls=b[rows].astype(float), strikes=k[rows].astype(float), outs_when_up=o[rows].astype(float), bases=base[rows],
                streak_pos=(r[rows] + 1).astype(float), prev1_pitch_type=prev[rows], pitch_type=np.array(PITCH_TYPES)[action], group=np.full(S * A, group))


def outcome_tensor(members: Sequence[gbm.FittedGbm], classes: Sequence[str], shape: pc.Shape, group: str = "RvR") -> np.ndarray:
    """[M, S, A, C] outcome probabilities of each bagged member for every situation, in the order of ``policy_core.OUTCOMES``."""

    cols = cell_columns(shape, group)
    x, _ = lc.level_matrix(None, cols, "L0", "outcome", history=False)      # level L0 uses situation and pitch type only
    col_of = {name: i for i, name in enumerate(classes)}
    out = np.zeros((len(members), shape.size, shape.n_actions, pc.C))
    for m, member in enumerate(members):
        proba = member.predict_proba(x)
        for name, c in pc.OC.items():
            if name in col_of:
                out[m, :, :, c] = proba[:, col_of[name]].reshape(shape.size, shape.n_actions)
        out[m] /= out[m].sum(axis=2, keepdims=True)
    return out


def fit_outcome_members(cols: Mapping[str, np.ndarray], *, backend: str = "hgb", device: str = "cpu", params: Mapping[str, Any] | None = None, reps: int = 5,
                        seed: int = 0, merge: Mapping[str, str] | None = None) -> tuple[list[gbm.FittedGbm], list[str]]:
    """Level-L0 outcome model, ``reps`` game-level bootstrap members (the members behind every interval of the policy tables)."""

    merge = merge or md.DEFAULT_CLASS_MERGE
    y, classes, keep = lc.encode_outcome(cols["outcome"], merge)
    ok = keep & np.isin(np.asarray(cols["pitch_type"]).astype(str), PITCH_TYPES)
    sub = lc.take(cols, np.nonzero(ok)[0])
    x, _ = lc.level_matrix(None, sub, "L0", "outcome", history=False)
    fit = lc.fit_predict_level(backend, device, params or {}, x, y[ok], np.asarray(sub["game_pk"]), x[:2], len(classes), reps=max(reps, 2), seed=seed)
    return fit.members, list(classes)


def usage_from_counts(cols: Mapping[str, np.ndarray], shape: pc.Shape, *, kappa: float = 30.0, min_share: float = 0.05, min_count: int = 300
                      ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Observed behaviour per state: shares by (count, previous type, streak) shrunk towards the (count, previous type) shares, the support rule on the
    (count, previous type) cell (plan 2.3), and the pitch counts. Outs and bases do not enter (the behaviour is pooled over them)."""

    A = shape.n_actions
    ptype = np.asarray(cols["pitch_type"]).astype(str)
    a = np.array([PITCH_TYPES.index(t) if t in PITCH_TYPES else -1 for t in ptype]) if len(ptype) else np.zeros(0, dtype=int)
    prev_name = np.asarray(cols["prev1_pitch_type"]).astype(str)
    prev = np.array([PITCH_TYPES.index(t) if t in PITCH_TYPES else A for t in prev_name])
    b = np.asarray(cols["balls"]).astype(int); k = np.asarray(cols["strikes"]).astype(int)
    r = np.clip(np.nan_to_num(np.asarray(cols["streak_pos"], dtype=float), nan=1.0).astype(int) - 1, 0, pc.STREAKS - 1)
    ok = (a >= 0) & (b < shape.n_balls) & (k < shape.n_strikes)
    cnt = np.zeros((shape.n_balls, shape.n_strikes, A + 1, pc.STREAKS, A))
    np.add.at(cnt, (b[ok], k[ok], prev[ok], r[ok], a[ok]), 1.0)
    cell = cnt.sum(axis=3)                                       # (b, k, prev, action)
    share_cell, support_cell = pc.support_rule(cell, min_share, min_count)
    raw = cnt + kappa * share_cell[:, :, :, None, :]
    share_r = raw / np.maximum(raw.sum(axis=4, keepdims=True), 1e-12)
    S = shape.size; bb, kk, oo, bs, pp, rr = shape.coords()
    obs_full = share_r[bb, kk, pp, rr]; support = support_cell[bb, kk, pp]
    n = cnt[bb, kk, pp, rr]
    return pc.masked_shares(obs_full, support), support, n


def members_dynamics(tensor: np.ndarray, obs: np.ndarray, support: np.ndarray, n: np.ndarray | None = None) -> list[pc.Dynamics]:
    return [pc.Dynamics(P=tensor[m], obs=obs, support=support, n=n) for m in range(tensor.shape[0])]


# ------------------------------------------------------------------------------------------------ analysis of bagged members
@dataclass
class Analysis:
    shape: pc.Shape
    kinds: tuple[str, ...]
    start: dict[str, np.ndarray]                 # policy -> start value per member [M]
    saving_pa: dict[str, np.ndarray]             # policy -> (observed - policy) at the start of a plate appearance, per member [M]
    pitches_per_pa: float
    hr_pa: dict[str, np.ndarray]                 # policy -> probability that the plate appearance ends in a home run, per member [M]
    q_obs: np.ndarray                            # [M, S, A] value of throwing a at s and then playing as observed
    marginal: np.ndarray                         # [M, S, A] change per pitch when a gains `delta` share
    advantage: np.ndarray                        # [M, S, A] Q(s,a) minus the observed mix of the other types: what 4.2 judges against tau
    policies: dict[str, np.ndarray]              # policy -> mean shares [S, A] over the members
    occupancy: np.ndarray                        # [S] expected pitches per plate appearance from each state under the observed behaviour
    obs: np.ndarray
    support: np.ndarray
    delta: float
    extras: dict[str, Any] = field(default_factory=dict)

    def interval(self, values: np.ndarray, confidence: float = 0.99):
        return pc.member_interval(values, confidence)


def analyze(members: Sequence[pc.Dynamics], env: pc.Environment, shape: pc.Shape, *, kinds: Sequence[str] = pc.POLICIES, trust: float = 0.10, delta: float = 0.10,
            lam: float = 0.0) -> Analysis:
    M = len(members)
    start = {k: np.zeros(M) for k in kinds}; hr = {k: np.zeros(M) for k in kinds}
    q_obs = np.zeros((M, shape.size, shape.n_actions)); marg = np.zeros_like(q_obs); adv = np.zeros_like(q_obs)
    pol_sum = {k: np.zeros((shape.size, shape.n_actions)) for k in kinds}
    ppp = np.zeros(M); occ = np.zeros(shape.size); v_obs = np.zeros(shape.size)
    for m, dyn in enumerate(members):
        eng = pc.Engine(shape, dyn, env, lam=lam)
        solved = pc.solve_all(eng, tuple(dict.fromkeys(("observed", *kinds))), trust)
        for k in kinds:
            start[k][m] = solved.start[k]; hr[k][m] = eng.hr_probability(solved.policies[k]); pol_sum[k] += solved.policies[k] / M
        q_obs[m] = solved.q_obs; marg[m] = pc.marginal_values(solved.q_obs, eng.obs_eff, delta); adv[m] = pc.advantage(solved.q_obs, eng.obs_eff)
        o = eng.occupancy(eng.policy_observed()); ppp[m] = o.sum(); occ += o / M; v_obs += solved.values["observed"] / M
    base = start["observed"]
    saving = {k: base - start[k] for k in kinds}
    return Analysis(shape, tuple(kinds), start, saving, float(ppp.mean()), hr, q_obs, marg, adv, pol_sum, occ, members[0].obs, members[0].support, delta,
                    {"members": M, "trust": trust, "lam": lam, "v_observed": v_obs})


def pooled(members: Sequence[pc.Dynamics]) -> pc.Dynamics:
    """One dynamics from several bagged members (the mean of their outcome probabilities)."""

    first = members[0]
    return pc.Dynamics(P=np.mean([m.P for m in members], axis=0), obs=first.obs, support=first.support, n=first.n)


def cross_saving(selected: Sequence[pc.Dynamics], evaluated: Sequence[pc.Dynamics], env: pc.Environment, shape: pc.Shape, kind: str = "dp_trust", trust: float = 0.10) -> float:
    """Winner's-curse-free value of a policy (plan 4.3): choose it on one set of dynamics, evaluate it on another; average both directions.
    Returns the saving per plate appearance versus the observed behaviour (positive = fewer runs)."""

    a_dyn, b_dyn = pooled(selected), pooled(evaluated)
    out = []
    for chosen, judged in ((a_dyn, b_dyn), (b_dyn, a_dyn)):
        ea, eb = pc.Engine(shape, chosen, env), pc.Engine(shape, judged, env)
        _, pi = ea.solve(kind, trust)
        out.append(eb.start_value(eb.evaluate(eb.policy_observed())) - eb.start_value(eb.evaluate(pi)))
    return float(np.mean(out))
