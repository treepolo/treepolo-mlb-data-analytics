"""Per-pitcher decision engine: the same engine as the league-average one, but every pitcher gets the outcome probabilities of his own pitcher x pitch-type effects.

How a pitcher's own dynamics are made
* The model's pitcher x pitch-type effects (``pitcher_pitch``; with ``control_pitcher_pitch = within`` they are not shrunk by the fit) are first shrunk after the fit, by empirical Bayes:
  the effect of a (pitcher, type) unit has a standard error from the information in its own pitches (sum of ``diag(p) - p p'`` over them), the spread of true effects among units
  (``tau^2`` per outcome class) is estimated from the effects themselves, and every effect is pulled towards 0 (the league average of the pitch type) by ``tau^2 / (tau^2 + se^2)``.
  A unit with few pitches is pulled a lot, a unit with many hardly at all.
* The day is the usual one (the pitcher x game effect is 0) and the batter is the league-average batter (no batter effect): both are units the model has never seen.
* The observed mix of pitches and the support rule stay the league's (a single pitcher has too few pitches for either); only the outcome probabilities are his.
* Every pitcher with enough plate appearances (``min_pa``) gets the engine solved once (the mean of the bagged members is his dynamics), and the savings are added up weighted by the plate
  appearances he faced. The share of all plate appearances that these pitchers cover is reported.

The engine assumes the batter does not adjust to what the pitcher changes, so the savings are an upper bound. Numbers only; nothing is judged."""
from __future__ import annotations

import copy
from typing import Any, Mapping, Sequence

import numpy as np

from . import policy_build as pb
from . import policy_core as pc
from .outcome_models import design as dz
from .resource_guard import guard

TARGET = "pitcher_pitch"
HR_KINDS = ("observed", "dp_trust")           # the home-run probability costs one more evaluation per policy: only for the policy that is played and the one that is proposed
ASSUMPTION_ZH = "引擎假設打者不會因配球改變而調整，所以算出的少失分是上限。"
ASSUMPTION_EN = "The engine assumes that batters do not adjust to a change in pitch selection, so the savings it computes are an upper bound."


def unavailable(model) -> str | None:
    """Why this model cannot give a per-pitcher engine, or None."""

    if getattr(model, "name", "") != "logistic":
        return "逐投手引擎只支援邏輯迴歸模型 / the per-pitcher engine needs the logistic model"
    if TARGET not in (getattr(model, "groups", None) or {}):
        return "模型沒有投手×球種效應（control_pitcher_pitch 沒開） / the model has no pitcher x pitch-type effects (control_pitcher_pitch is off)"
    return None


# ------------------------------------------------------------------------------------------------ effects of the model
def _block(model) -> tuple[int, int]:
    """(first row, rows) of the pitcher x pitch-type effects in a member's coefficient matrix."""

    start = int(model.members[0]["n_dense"])
    for g in model.groups:
        if g == TARGET:
            return start, len(model.indexes[TARGET])
        start += len(model.indexes[g])
    raise KeyError(TARGET)


def effects_by_member(model) -> np.ndarray:
    """[members, units, classes] effects of the (pitcher, type) units, each class-centred (adding the same number to every class changes no probability)."""

    a, size = _block(model)
    out = np.stack([np.asarray(m["coef"][a:a + size], dtype=np.float64) for m in model.members])
    return out - out.mean(axis=2, keepdims=True)


def unit_information(model, sub: Mapping[str, np.ndarray], chunk: int = 100_000) -> tuple[np.ndarray, np.ndarray]:
    """([units, K, K] information of every (pitcher, type) unit about its own effect vector, [units] number of its pitches), from the training rows ``sub``."""

    index = model.indexes[TARGET]
    unit = index.lookup(dz.effect_keys(sub, TARGET))
    U, K = len(index), len(model.classes)
    H = np.zeros((U, K, K)); rows = np.zeros(U)
    n = len(unit)
    for a in range(0, n, chunk):
        guard.checkpoint("pitcher engine: information")
        sl = slice(a, min(n, a + chunk))
        rows_sl = {k: np.asarray(v)[sl] for k, v in sub.items() if isinstance(v, np.ndarray) and len(v) == n}
        p = model.predict_proba(rows_sl, effects="specific")
        u = unit[sl]; ok = u >= 0
        p, u = p[ok], u[ok]
        rows += np.bincount(u, minlength=U)
        for i in range(K):
            for j in range(K):
                w = (p[:, i] - p[:, i] * p[:, i]) if i == j else -p[:, i] * p[:, j]
                H[:, i, j] += np.bincount(u, weights=w, minlength=U)
    return H, rows


def shrink_factors(beta: np.ndarray, H: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(factor [units, K], tau2 [K], se2 [units, K]): the empirical-Bayes weights that pull the class-centred effects ``beta`` [units, K] towards 0.

    ``se2``: variance of each (centred) effect from the information ``H`` (the information has the all-ones vector in its null space, so ``inv(H + J/K) - J/K`` is its pseudo-inverse).
    ``tau2``: spread of the true effects among units, per class, by the iterated moment estimator ``sum w^2 (b^2 - se2) / sum w^2`` with ``w = 1 / (tau2 + se2)``."""

    U, K, _ = H.shape
    sym = (H + H.transpose(0, 2, 1)) / 2.0
    ones = np.full((K, K), 1.0 / K)
    with np.errstate(all="ignore"):
        inv = np.linalg.inv(sym + ones[None] + 1e-9 * np.eye(K)[None])                         # the tiny ridge only matters for a unit with no information at all (then se2 is infinite below)
        se2 = np.einsum("ukk->uk", inv) - 1.0 / K
    trace = np.einsum("ukk->u", sym)
    se2 = np.where(np.isfinite(se2) & (se2 > 0) & (trace[:, None] > 1e-9), se2, np.inf)
    tau2 = np.zeros(K)
    for k in range(K):
        ok = np.isfinite(se2[:, k])
        if not ok.any():
            tau2[k] = 0.0
            continue
        b2 = beta[ok, k] ** 2; v = se2[ok, k]
        t = max(float(np.mean(b2 - v)), 1e-8)
        for _ in range(100):
            w = 1.0 / (t + v) ** 2
            new = max(float(np.sum(w * (b2 - v)) / np.sum(w)), 1e-12)
            done = abs(new - t) <= 1e-9 * max(t, 1e-6)
            t = new
            if done:
                break
        tau2[k] = t
    with np.errstate(all="ignore"):
        factor = np.where(np.isfinite(se2), tau2[None, :] / (tau2[None, :] + se2), 0.0)
    return factor, tau2, se2


def shrunk_copy(model, factor: np.ndarray):
    """The model with its pitcher x pitch-type effects replaced by ``factor`` times the class-centred effects (every bagged member, the same factor): nothing else changes."""

    a, size = _block(model)
    other = copy.copy(model)
    members = []
    for m in model.members:
        coef = np.array(m["coef"], copy=True)
        block = np.asarray(coef[a:a + size], dtype=np.float64)
        block = block - block.mean(axis=1, keepdims=True)
        coef[a:a + size] = (factor * block).astype(coef.dtype)
        members.append({**m, "coef": coef})
    other.members = members
    return other


# ------------------------------------------------------------------------------------------------ plate appearances
def plate_appearances(sub: Mapping[str, np.ndarray]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(pitcher ids, plate appearances, pitches) of every pitcher in ``sub``; a plate appearance is a (game, at bat) pair."""

    pitcher = np.asarray(sub["pitcher"]).astype(np.int64)
    if "at_bat_number" in sub:
        pa_key = np.asarray(sub["game_pk"]).astype(np.int64) * 1000 + np.asarray(sub["at_bat_number"]).astype(np.int64)
    else:                                                                       # rows in the order they were thrown, without an at-bat number: a plate appearance starts at a pitch with no previous one
        pa_key = np.cumsum(np.asarray(sub["prev1_pitch_type"]).astype(str) == "none").astype(np.int64)
    order = np.lexsort((pa_key, pitcher))
    p, k = pitcher[order], pa_key[order]
    new = np.ones(len(p), dtype=bool)
    new[1:] = (p[1:] != p[:-1]) | (k[1:] != k[:-1])
    ids, inverse = np.unique(p, return_inverse=True)
    return ids, np.bincount(inverse, weights=new.astype(float), minlength=len(ids)), np.bincount(inverse, minlength=len(ids)).astype(float)


# ------------------------------------------------------------------------------------------------ one pitcher
def pitcher_dynamics(shrunk, pitcher: int, shape: pc.Shape, group: str, obs, support, n) -> pc.Dynamics:
    """The engine's dynamics for one pitcher: his (shrunk) pitcher x pitch-type effects, the usual day, the league-average batter; the league's observed mix and support."""

    size = shape.size * shape.n_actions
    # game_pk is 0 in the grid: a game the model has not seen, so no pitcher x game effect (the usual day); the batter is one the model has not seen: no batter effect
    extra = {"pitcher": np.full(size, int(pitcher), dtype=np.int64), "batter": np.full(size, -1, dtype=np.int64)}
    tensor = pb.model_tensor(shrunk, shape, group, effects="specific", extra=extra)
    return pc.Dynamics(P=tensor.mean(axis=0), obs=obs, support=support, n=n)


def solve_pitcher(dyn: pc.Dynamics, env: pc.Environment, shape: pc.Shape, kinds: Sequence[str], trust: float) -> dict[str, dict[str, float]]:
    """Per policy kind: the value at the start of a plate appearance (and the home-run probability for ``HR_KINDS``)."""

    eng = pc.Engine(shape, dyn, env)
    out: dict[str, dict[str, float]] = {}
    for kind in dict.fromkeys(("observed", *kinds)):
        V, pi = eng.solve(kind, trust)
        out[kind] = {"start": eng.start_value(V), "hr": eng.hr_probability(pi) if kind in HR_KINDS else float("nan")}
    return out


# ------------------------------------------------------------------------------------------------ the whole thing for a group
def run(group: str, sub: Mapping[str, np.ndarray], model, env: pc.Environment, shape: pc.Shape, obs, support, n, *, kinds: Sequence[str], trust: float, min_pa: int,
        pa_per_team_season: float, league_saving: Mapping[str, float], progress=None) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """(summary rows per policy, one detail row per pitcher, the coverage row) of the per-pitcher engine for one group; ``sub`` are the training rows of the group."""

    beta = effects_by_member(model).mean(axis=0)
    H, unit_rows = unit_information(model, sub)
    factor, tau2, se2 = shrink_factors(beta, H)
    shrunk = shrunk_copy(model, factor)
    ids, pa, pitches = plate_appearances(sub)
    use = np.nonzero(pa >= min_pa)[0]
    keys = model.indexes[TARGET].keys
    unit_pitcher = keys // 16
    unit_factor = factor.mean(axis=1)
    use_kinds = [k for k in kinds]
    detail: list[dict[str, Any]] = []
    results: list[dict[str, dict[str, float]]] = []
    order = use[np.argsort(-pa[use])]
    for i, j in enumerate(order):
        guard.checkpoint("pitcher engine")
        pid = int(ids[j])
        dyn = pitcher_dynamics(shrunk, pid, shape, group, obs, support, n)
        res = solve_pitcher(dyn, env, shape, use_kinds, trust)
        results.append(res)
        mine = unit_pitcher == pid
        w = unit_rows[mine]
        row = {"group": group, "pitcher": pid, "pa": int(pa[j]), "pitches": int(pitches[j]), "units": int(mine.sum()),
               "shrink_mean": float((unit_factor[mine] * w).sum() / w.sum()) if w.sum() > 0 else float("nan"), "start_observed": res["observed"]["start"]}
        for k in use_kinds:
            if k != "observed":
                row[f"saving_{k}"] = res["observed"]["start"] - res[k]["start"]
        detail.append(row)
        if progress:
            progress(i + 1, len(order))
    total_pa = float(pa.sum()); used_pa = float(pa[use].sum()) if len(use) else 0.0
    coverage = {"group": group, "min_pa": int(min_pa), "pitchers_total": int(len(ids)), "pitchers_used": int(len(use)), "pa_total": int(total_pa), "pa_used": int(used_pa),
                "pa_share": used_pa / total_pa if total_pa else float("nan"), "pitch_share": float(pitches[use].sum() / pitches.sum()) if len(use) else 0.0,
                "units": int(len(keys)), "units_pulled_over_half": float((unit_factor < 0.5).mean()) if len(keys) else float("nan"),
                "tau_by_class": {c: float(np.sqrt(t)) for c, t in zip(model.classes, tau2)}}
    summary: list[dict[str, Any]] = []
    if results:
        w = np.array([d["pa"] for d in detail], dtype=float)
        for k in use_kinds:
            sav = np.array([r["observed"]["start"] - r[k]["start"] for r in results])
            start = np.array([r[k]["start"] for r in results]); hr = np.array([r[k]["hr"] for r in results])
            mean = float((w * sav).sum() / w.sum())
            summary.append({"group": group, "policy": k, "pitchers": int(len(results)), "pa_share": coverage["pa_share"], "saving_pa": mean, "saving_p10": float(np.percentile(sav, 10)),
                            "saving_median": float(np.median(sav)), "saving_p90": float(np.percentile(sav, 90)), "saving_per_team_season": mean * pa_per_team_season,
                            "start_value": float((w * start).sum() / w.sum()), "hr_probability": float((w * hr).sum() / w.sum()) if np.isfinite(hr).all() else float("nan"),
                            "league_saving_pa": float(league_saving.get(k, float("nan")))})
    return summary, detail, coverage
