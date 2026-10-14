"""Synthetic worlds W0-W3 and W6 for the decision engine (plan 5.2): the true outcome probabilities and the true behaviour are known, so the
best policy and its margin are known too (solve the true dynamics with ``policy_core``)."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from . import policy_core as pc
from .profiles import PITCH_TYPES

FF, SL, CH = PITCH_TYPES.index("FF"), PITCH_TYPES.index("SL"), PITCH_TYPES.index("CH")
USED = (FF, SL, CH)
WORLDS = {
    "W0": "Every pitch type has the same outcome probabilities: no margin, nothing to detect.",
    "W1": "The slider is better than the others in every situation (more whiffs, fewer home runs): every trust region should lean to the slider.",
    "W2": "Repeating the previous pitch type adds whiffs: the best policy repeats.",
    "W3": "The slider is better when the pitcher is ahead in the count, the changeup when he is behind: the policy switches with the count.",
    "W6": "The changeup is excellent at three balls but nobody throws it there: the action is not supported and must not be recommended.",
}
RE_PA = np.array([[0.50, 0.90, 1.12, 1.50, 1.35, 1.75, 1.95, 2.35],
                  [0.27, 0.54, 0.69, 0.95, 0.96, 1.21, 1.36, 1.60],
                  [0.10, 0.23, 0.33, 0.45, 0.35, 0.48, 0.60, 0.80]])
START = np.array([0.34, 0.33, 0.33])[:, None] * np.array([[0.56, 0.18, 0.07, 0.06, 0.01, 0.06, 0.03, 0.03]])
BASE_PROBS = dict(ball=0.36, called_strike=0.17, foul=0.17, foul_tip=0.01, hit_by_pitch=0.004, in_play_home_run=0.012, in_play_non_hr=0.16, whiff=0.10)


@dataclass
class PolicyWorld:
    kind: str
    shape: pc.Shape
    truth: pc.Dynamics
    env: pc.Environment
    notes: str = ""
    expect: dict = field(default_factory=dict)


def _logits(shape: pc.Shape) -> tuple[np.ndarray, tuple[np.ndarray, ...]]:
    b, k, o, base, p, r = shape.coords()
    lg = np.zeros((shape.size, shape.n_actions, pc.C))
    for name, c in pc.OC.items():
        lg[:, :, c] = np.log(BASE_PROBS[name])[None, None]
    # the count moves the mix a little (more fouls with two strikes, more balls when behind, fewer called strikes when ahead)
    lg[:, :, pc.OC["foul"]] += (0.5 * (k == 2))[:, None]
    lg[:, :, pc.OC["ball"]] += (0.10 * b - 0.05 * k)[:, None]
    lg[:, :, pc.OC["called_strike"]] += (-0.05 * b)[:, None]
    return lg, (b, k, o, base, p, r)


def _usage(shape: pc.Shape, coords, blocked_at_three_balls: bool = False) -> np.ndarray:
    b, k, o, base, p, r = coords
    S, A = shape.size, shape.n_actions
    score = np.full((S, A), -np.inf)
    for a, w in ((FF, 0.55), (SL, 0.30), (CH, 0.15)):
        score[:, a] = np.log(w)
    score[:, SL] += 0.3 * (k == 2)
    for a in USED:
        score[:, a] += 0.25 * (p == a) * (1 + 0.5 * r)
    if blocked_at_three_balls:
        score[b == 3, CH] = -np.inf
    e = np.exp(score - score.max(axis=1, keepdims=True))
    return e / e.sum(axis=1, keepdims=True)


def _jbip(re_pa: np.ndarray, base_offset: dict[int, float] | None = None) -> np.ndarray:
    jb = np.zeros((9, 3, 8))
    re = np.vstack([re_pa, np.zeros((1, 8))])
    for o in range(3):
        for B in range(8):
            out = re[o + 1, B]
            new = ((B << 1) | 1) & 7
            runs = ((B >> 2) & 1) + 0.5 * ((B >> 1) & 1)
            jb[:, o, B] = 0.72 * out + 0.28 * (runs + re[o, new])
    for a, v in (base_offset or {}).items():
        jb[a] += v
    return jb


def build_world(kind: str, shape: pc.Shape | None = None) -> PolicyWorld:
    if kind not in WORLDS:
        raise KeyError(f"unknown policy world {kind!r}; choose from {list(WORLDS)}")
    shape = shape or pc.Shape()
    lg, coords = _logits(shape)
    b, k, o, base, p, r = coords
    ahead, behind = k > b, b > k
    jb_offset = None
    if kind == "W1":
        lg[:, SL, pc.OC["whiff"]] += 0.7; lg[:, SL, pc.OC["in_play_home_run"]] -= 0.5; lg[:, SL, pc.OC["in_play_non_hr"]] -= 0.15
        jb_offset = {SL: -0.03}
    elif kind == "W2":
        for a in USED:
            lg[:, a, pc.OC["whiff"]] += 0.8 * (p == a) * np.minimum(r + 1, 2) / 2
    elif kind == "W3":
        lg[:, SL, pc.OC["whiff"]] += 0.8 * ahead
        lg[:, SL, pc.OC["ball"]] += 0.35 * behind
        lg[:, CH, pc.OC["ball"]] -= 0.8 * behind
        lg[:, CH, pc.OC["called_strike"]] += 0.4 * behind
    elif kind == "W6":
        lg[:, CH, pc.OC["ball"]] -= 2.5 * (b == 3)
        lg[:, CH, pc.OC["called_strike"]] += 1.2 * (b == 3)
    P = np.exp(lg - lg.max(axis=2, keepdims=True)); P /= P.sum(axis=2, keepdims=True)
    obs = _usage(shape, coords, blocked_at_three_balls=(kind == "W6"))
    support = np.zeros((shape.size, shape.n_actions), dtype=bool)
    support[:, list(USED)] = obs[:, list(USED)] > 0.05
    obs = pc.masked_shares(obs, support)
    truth = pc.Dynamics(P=P, obs=obs, support=support)
    # Make the run-expectancy table a fixed point of the world: the value of the observed behaviour at the start of a plate appearance must equal the table
    # (as it does in real data, where the table is measured from the same pitches).
    re_pa = RE_PA.copy()
    for _ in range(40):
        env = pc.Environment(re_pa=re_pa, re_cnt=np.zeros((shape.n_balls, shape.n_strikes, 3, 8)), jbip=_jbip(re_pa, jb_offset), start=START.copy())
        eng = pc.Engine(shape, truth, env)
        V = eng.evaluate(eng.policy_observed())
        new = np.array([[V[shape.start_index(o, B)] for B in range(8)] for o in range(3)])
        done = np.abs(new - re_pa).max() < 1e-10
        re_pa = new
        if done:
            break
    env = pc.Environment(re_pa=re_pa, re_cnt=np.zeros((shape.n_balls, shape.n_strikes, 3, 8)), jbip=_jbip(re_pa, jb_offset), start=START.copy())
    eng = pc.Engine(shape, truth, env)
    V = eng.evaluate(eng.policy_observed()); occ = eng.occupancy(eng.policy_observed()) + 1e-12
    cnt = np.zeros((shape.n_balls, shape.n_strikes, 3, 8))
    for bb in range(shape.n_balls):
        for kk in range(shape.n_strikes):
            for oo in range(3):
                for B in range(8):
                    idx = shape.index(bb, kk, oo, B, np.arange(shape.n_actions + 1)[:, None], np.arange(pc.STREAKS)[None, :]).ravel()
                    cnt[bb, kk, oo, B] = float((V[idx] * occ[idx]).sum() / occ[idx].sum())
    env.re_cnt = cnt
    return PolicyWorld(kind, shape, truth, env, WORLDS[kind])


def simulate_pitches(world: PolicyWorld, n_pa: int, seed: int, *, pa_per_game: int = 60, group: str = "RvR", years=(2023, 2024)) -> dict[str, np.ndarray]:
    """Columnar pitch table (the layout ``league_core`` reads): plate appearances played by the true behaviour against the true outcome probabilities."""

    rng = np.random.default_rng(seed)
    sh, dyn = world.shape, world.truth
    n_pa = int(n_pa)
    ob = rng.choice(24, size=n_pa, p=(world.env.start / world.env.start.sum()).ravel())
    outs, bases = ob // 8, ob % 8
    state = np.array(sh.index(0, 0, outs, bases, sh.none, 0), dtype=np.int64)
    pa_id = np.arange(n_pa)
    active = np.ones(n_pa, dtype=bool)
    rows = {k: [] for k in ("pa", "state", "action", "outcome")}
    cum_obs = np.cumsum(dyn.obs, axis=1); cum_P = np.cumsum(dyn.P, axis=2)
    nxt, _ = pc.terminal_arrays(sh, world.env)
    for _ in range(400):
        idx = np.nonzero(active)[0]
        if len(idx) == 0:
            break
        s = state[idx]
        a = np.minimum((rng.random(len(idx))[:, None] > cum_obs[s]).sum(axis=1), sh.n_actions - 1)
        c = np.minimum((rng.random(len(idx))[:, None] > cum_P[s, a]).sum(axis=1), pc.C - 1)
        rows["pa"].append(pa_id[idx]); rows["state"].append(s); rows["action"].append(a); rows["outcome"].append(c)
        n2 = nxt[s, a, c]
        active[idx[n2 < 0]] = False
        state[idx[n2 >= 0]] = n2[n2 >= 0]
    pa = np.concatenate(rows["pa"]); st = np.concatenate(rows["state"]); ac = np.concatenate(rows["action"]); oc = np.concatenate(rows["outcome"])
    order = np.argsort(pa, kind="stable"); pa, st, ac, oc = pa[order], st[order], ac[order], oc[order]
    b, k, o, base, p, r = np.unravel_index(st, sh.dims)
    n = len(pa)
    game = pa // pa_per_game + 1
    names = np.array([("in_play_out" if x == "in_play_non_hr" else x) for x in pc.OUTCOMES])      # the raw name the default class map folds into in_play_non_hr
    prev = np.where(p == sh.none, "none", np.array(PITCH_TYPES + ("none",))[np.minimum(p, sh.n_actions)])
    in_play = names == "in_play_out"
    value = np.where(in_play[oc], world.env.jbip[ac, o, base] - world.env.re_cnt[np.minimum(b, sh.n_balls - 1), np.minimum(k, sh.n_strikes - 1), o, base], 0.0)
    return dict(game_pk=game, pitcher=10 + (pa % 40), batter=1000 + (pa % 90), group=np.full(n, group), pitch_type=np.array(PITCH_TYPES)[ac], outcome=names[oc],
                balls=b.astype(float), strikes=k.astype(float), outs_when_up=o.astype(float), bases=base, prev1_pitch_type=prev, streak_pos=(r + 1).astype(float),
                game_year=np.where(game <= int(game.max() * 0.6), years[0], years[1]), pa_id=pa,
                pitch_value=value, state_code=(b * 1000 + k * 100 + o * 10 + base).astype(float))
