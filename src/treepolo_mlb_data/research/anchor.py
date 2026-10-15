"""League-level fixed-effects regressions that anchor the league model (P6 plan 3.6, 3.7, A.3).

``fit_effects`` is a linear probability model per outcome class with the SAME design for every class (so the fitted values add to one), pitch type effects
that may differ by count phase (ahead / even / behind), streak and situation terms, and fixed effects for pitcher and batter (design S3) or
pitcher-game and batter (design S4). ``jbip_table`` is the value of a ball in play that is not a home run by pitch type, outs and bases.
Nothing here produces player-specific results: the fixed effects only remove who pitched and who batted.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

import numpy as np

from . import policy_core as pc
from .fixed_effects import codes, demean
from .profiles import PITCH_TYPES

PHASES = ("even", "ahead", "behind")
DESIGNS = {"S3": ("pitcher", "batter"), "S4": ("pitcher_game", "batter")}


def count_phase(balls, strikes) -> np.ndarray:
    """0 even, 1 ahead (more strikes than balls: pitcher ahead), 2 behind."""

    b = np.asarray(balls).astype(int); k = np.asarray(strikes).astype(int)
    return np.where(k > b, 1, np.where(b > k, 2, 0))


def fe_codes(cols: Mapping[str, np.ndarray], design: str) -> list[np.ndarray]:
    out = []
    for name in DESIGNS[design]:
        if name == "pitcher_game":
            out.append(codes(np.char.add(np.char.add(np.asarray(cols["pitcher"]).astype(str), "|"), np.asarray(cols["game_pk"]).astype(str))))
        else:
            out.append(codes(cols[name]))
    return out


@dataclass
class Design:
    X: np.ndarray
    names: list[str]
    action: np.ndarray            # [n] index into ``types``
    phase: np.ndarray             # [n]


def build_design(cols: Mapping[str, np.ndarray], types: Sequence[str], rows: np.ndarray | None = None) -> tuple[Design, np.ndarray]:
    """(design, row mask). Rows are pitches of the listed types; the first listed type is the reference action."""

    ptype = np.asarray(cols["pitch_type"]).astype(str)
    keep = np.isin(ptype, list(types)) if rows is None else (np.isin(ptype, list(types)) & rows)
    b = np.asarray(cols["balls"]).astype(int)[keep]; k = np.asarray(cols["strikes"]).astype(int)[keep]
    o = np.asarray(cols["outs_when_up"]).astype(int)[keep]; B = np.asarray(cols["bases"]).astype(int)[keep]
    prev = np.asarray(cols["prev1_pitch_type"]).astype(str)[keep]; streak = np.asarray(cols["streak_pos"], dtype=float)[keep]
    action = np.array([list(types).index(t) for t in ptype[keep]]); phase = count_phase(b, k)
    parts, names = [], []

    def add(col, name):
        parts.append(np.asarray(col, dtype=float)); names.append(name)

    for bb in range(4):
        for kk in range(3):
            if (bb, kk) != (0, 0):
                add((b == bb) & (k == kk), f"count={bb}-{kk}")
    for oo in (1, 2):
        add(o == oo, f"outs={oo}")
    for bs in range(1, 8):
        add(B == bs, f"bases={bs}")
    for t in PITCH_TYPES[1:]:
        add(prev == t, f"prev={t}")
    add(streak == 2, "streak=2"); add(streak >= 3, "streak=3+")
    for ai, t in enumerate(types):
        if ai == 0:
            continue
        add(action == ai, f"action={t}")
        add((action == ai) & (phase == 1), f"action={t}:ahead"); add((action == ai) & (phase == 2), f"action={t}:behind")
    return Design(np.column_stack(parts), names, action, phase), keep


def fit_effects(cols: Mapping[str, np.ndarray], *, types: Sequence[str], design: str = "S3", cluster: str = "pitcher") -> dict:
    """Per outcome class: coefficients, covariance (CR1, clustered by ``cluster``), residual-based fitted-value check."""

    if design not in DESIGNS:
        raise ValueError(f"design must be one of {list(DESIGNS)}")
    from . import modeling as md

    known = np.isin(np.asarray(cols["outcome"]).astype(str), list(md.DEFAULT_CLASS_MERGE) + ["in_play_non_hr"])      # rows outside the eight classes (bunt plays ...) are not part of the design
    d, keep = build_design(cols, types, known)
    sub = {k: np.asarray(v)[keep] for k, v in cols.items() if k in ("pitcher", "batter", "game_pk", "outcome")}
    outcome = sub["outcome"].astype(str)
    Y = np.column_stack([(outcome == name) | ((name == "in_play_non_hr") & np.isin(outcome, ["in_play_out", "in_play_single", "in_play_double", "in_play_triple", "in_play_error_other"]))
                         for name in pc.OUTCOMES]).astype(float)
    groups = fe_codes(sub, design)
    Xt, ok1 = demean(d.X, groups); Yt, ok2 = demean(Y, groups)
    informative = Xt.std(axis=0) > 1e-12
    Xu = Xt[:, informative]
    bread = np.linalg.pinv(Xu.T @ Xu)
    beta_u = bread @ (Xu.T @ Yt)
    resid = Yt - Xu @ beta_u
    cl = codes(sub[cluster] if cluster in sub else sub["pitcher"]); g = int(cl.max()) + 1
    p = Xu.shape[1]
    cov = np.zeros((len(pc.OUTCOMES), p, p))
    for c in range(len(pc.OUTCOMES)):
        scores = np.zeros((g, p))
        for j in range(p):
            scores[:, j] = np.bincount(cl, weights=Xu[:, j] * resid[:, c], minlength=g)
        cov[c] = bread @ (scores.T @ scores) @ bread * (g / max(g - 1, 1))
    full_beta = np.zeros((len(pc.OUTCOMES), len(d.names))); full_cov = np.zeros((len(pc.OUTCOMES), len(d.names), len(d.names)))
    idx = np.nonzero(informative)[0]
    full_beta[:, idx] = beta_u.T
    full_cov[np.ix_(range(len(pc.OUTCOMES)), idx, idx)] = cov
    fitted = Y - resid                                           # the model's own prediction: the outcome minus its residual (fixed effects included)
    outside = ((fitted < -1e-9) | (fitted > 1 + 1e-9)).mean(axis=0)
    return {"names": d.names, "beta": full_beta, "cov": full_cov, "types": list(types), "n": int(len(Y)), "clusters": g, "design": design, "converged": bool(ok1 and ok2),
            "outside01": outside, "usage": _usage(d, len(types)), "dropped": [d.names[i] for i in np.nonzero(~informative)[0]]}


def _usage(d: Design, n_types: int) -> np.ndarray:
    """Shares of the listed types by count phase [phase, type] (the weights that define 'the usual mix of the other types')."""

    out = np.zeros((3, n_types))
    for ph in range(3):
        m = d.phase == ph
        out[ph] = np.bincount(d.action[m], minlength=n_types) / max(int(m.sum()), 1)
    return out


def action_contrasts(fit: dict, confidence: float = 0.99) -> list[dict]:
    """Effect of each action relative to the usual mix of all listed types in the same count phase: ``beta_a - sum_j w_j beta_j``, per outcome class."""

    from scipy import stats

    names = fit["names"]; types = fit["types"]; at = {n: i for i, n in enumerate(names)}
    z = float(stats.norm.ppf(0.5 + confidence / 2))
    rows = []
    for c, cname in enumerate(pc.OUTCOMES):
        for ph, pname in enumerate(PHASES):
            def vec(ai):
                v = np.zeros(len(names))
                if ai == 0:
                    return v
                v[at[f"action={types[ai]}"]] = 1.0
                if ph:
                    v[at[f"action={types[ai]}:{pname}"]] = 1.0
                return v
            mix = sum(fit["usage"][ph][j] * vec(j) for j in range(len(types)))
            for ai, t in enumerate(types):
                v = vec(ai) - mix
                est = float(v @ fit["beta"][c]); var = float(v @ fit["cov"][c] @ v)
                se = float(np.sqrt(max(var, 0.0)))
                rows.append({"design": fit["design"], "phase": pname, "action": t, "class": cname, "effect": est, "se": se, "lo": est - z * se, "hi": est + z * se, "share": float(fit["usage"][ph][ai])})
    return rows


def streak_effects(fit: dict, confidence: float = 0.99) -> list[dict]:
    from scipy import stats

    z = float(stats.norm.ppf(0.5 + confidence / 2)); at = {n: i for i, n in enumerate(fit["names"])}
    rows = []
    for c, cname in enumerate(pc.OUTCOMES):
        for term in ("streak=2", "streak=3+"):
            i = at[term]; est = float(fit["beta"][c, i]); se = float(np.sqrt(max(fit["cov"][c, i, i], 0.0)))
            rows.append({"design": fit["design"], "term": term, "class": cname, "effect": est, "se": se, "lo": est - z * se, "hi": est + z * se})
    return rows


def jbip_table(cols: Mapping[str, np.ndarray], *, types: Sequence[str], value: np.ndarray, design: str = "S3", kappa: float = 50.0, n_actions: int = 9) -> tuple[np.ndarray, dict]:
    """J[a, outs, bases]: value (runs plus run expectancy afterwards) of a ball in play that is not a home run. Out-and-base cell mean plus the pitch-type
    effect from a regression with pitcher and batter fixed effects (so a ground-ball pitcher's sinkers are not credited with his skill), shrunk to that sum in thin cells."""

    outcome = np.asarray(cols["outcome"]).astype(str)
    inplay = np.isin(outcome, ["in_play_out", "in_play_single", "in_play_double", "in_play_triple", "in_play_error_other", "in_play_non_hr"])
    ptype = np.asarray(cols["pitch_type"]).astype(str)
    value = np.asarray(value, dtype=float)
    m = inplay & np.isin(ptype, list(types)) & np.isfinite(value) & (np.asarray(cols["outs_when_up"]).astype(int) < 3)
    jb = np.zeros((n_actions, 3, 8))
    if m.sum() < 50:
        return jb, {"n": int(m.sum()), "note": "too few balls in play"}
    sub = {k: np.asarray(v)[m] for k, v in cols.items() if k in ("pitcher", "batter", "game_pk", "outs_when_up", "bases", "pitch_type")}
    o = sub["outs_when_up"].astype(int); B = sub["bases"].astype(int); v = value[m]
    a = np.array([list(types).index(t) for t in sub["pitch_type"].astype(str)])
    X = np.zeros((len(v), len(types) - 1))
    for ai in range(1, len(types)):
        X[:, ai - 1] = a == ai
    from .fixed_effects import fe_ols

    # remove the out-and-base cell means first (they are not what the fixed-effects regression is for), then the action effect inside pitcher and batter
    cell = o * 8 + B
    cell_mean = np.bincount(cell, weights=v, minlength=24) / np.maximum(np.bincount(cell, minlength=24), 1)
    grand = float(v.mean())
    cell_mean = np.where(np.bincount(cell, minlength=24) > 0, cell_mean, grand)
    res = fe_ols(v - cell_mean[cell], X, fe_codes(sub, design), np.asarray(sub["pitcher"]))
    eff = np.concatenate([[0.0], res["beta"]])
    w = np.bincount(a, minlength=len(types)) / len(a)
    eff = eff - float((w * eff).sum())
    base = cell_mean.reshape(3, 8)[None, :, :] + np.zeros((n_actions, 1, 1))
    for ai, t in enumerate(types):
        base[PITCH_TYPES.index(t)] += eff[ai]
    sums = np.zeros((n_actions, 3, 8)); cnt = np.zeros((n_actions, 3, 8))
    ai_global = np.array([PITCH_TYPES.index(t) for t in sub["pitch_type"].astype(str)])
    np.add.at(sums, (ai_global, o, B), v); np.add.at(cnt, (ai_global, o, B), 1.0)
    jb = (sums + kappa * base) / (cnt + kappa)
    return jb, {"n": int(m.sum()), "effects": {t: float(eff[i]) for i, t in enumerate(types)}}


# ------------------------------------------------------------------------------------------------ comparison with the league model
def model_contrasts(p_outcome: np.ndarray, obs: np.ndarray, support: np.ndarray, occupancy: np.ndarray, shape: pc.Shape, types: Sequence[str]) -> list[dict]:
    """The league model's counterpart of ``action_contrasts``: per count phase and outcome class, the occupancy-weighted difference between the outcome
    probability of throwing ``a`` and of throwing the usual mix of the others, over the states where ``a`` can be used."""

    b, k, o, base, p, r = shape.coords()
    phase = count_phase(b, k)
    rows = []
    idx = [PITCH_TYPES.index(t) for t in types]
    for ph, pname in enumerate(PHASES):
        for ai, t in zip(idx, types):
            w = np.where((phase == ph) & support[:, ai], occupancy, 0.0)
            if w.sum() <= 0:
                continue
            rest = 1.0 - obs[:, ai]
            with np.errstate(divide="ignore", invalid="ignore"):
                mix = (np.einsum("sa,sac->sc", obs, p_outcome) - obs[:, ai:ai + 1] * p_outcome[:, ai, :]) / np.where(rest > 1e-9, rest, np.nan)[:, None]
            diff = p_outcome[:, ai, :] - mix
            good = np.isfinite(diff).all(axis=1)
            ww = np.where(good, w, 0.0)
            if ww.sum() <= 0:
                continue
            eff = (diff[good] * ww[good, None]).sum(axis=0) / ww.sum()
            for c, cname in enumerate(pc.OUTCOMES):
                rows.append({"phase": pname, "action": t, "class": cname, "effect": float(eff[c])})
    return rows


def anchor_consistency(model_rows: Sequence[Mapping], anchor_rows: Sequence[Mapping], *, z_detect: float = 2.576, min_effect: float = 0.0) -> dict:
    """Plan 3.6.2: on the cells where the anchor sees an effect (|effect| >= z * se), how often does the model have the same sign and how large is it relative to the anchor?
    Also reported: how many cells the model flags that the anchor does not see (a sign of confounding the model could not remove)."""

    anchor = {(r["phase"], r["action"], r["class"]): r for r in anchor_rows}
    same, ratios, n_anchor, model_only, n_model_big = 0, [], 0, 0, 0
    for m in model_rows:
        a = anchor.get((m["phase"], m["action"], m["class"]))
        if a is None or a["se"] <= 0:
            continue
        seen = abs(a["effect"]) >= z_detect * a["se"] and abs(a["effect"]) >= min_effect
        if seen:
            n_anchor += 1
            same += int(np.sign(m["effect"]) == np.sign(a["effect"]))
            ratios.append(m["effect"] / a["effect"])
        if abs(m["effect"]) >= z_detect * a["se"] and abs(m["effect"]) >= min_effect and not seen:
            model_only += 1
        n_model_big += int(abs(m["effect"]) >= z_detect * a["se"])
    return {"anchor_cells": n_anchor, "same_sign_share": same / n_anchor if n_anchor else float("nan"), "median_ratio": float(np.median(ratios)) if ratios else float("nan"),
            "model_only_cells": model_only, "model_large_cells": n_model_big,
            "passes": bool(n_anchor and same / n_anchor >= 0.9 and 0.5 <= float(np.median(ratios)) <= 1.5)}
