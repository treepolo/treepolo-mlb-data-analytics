"""Per-pitcher decision engine: the same engine as the league-average one, but every pitcher gets the outcome probabilities of his own pitcher x pitch-type effects.

How a pitcher's own dynamics are made
* A pitcher's own effect of a pitch type is the sum of his overall effect (``pitcher``, which the model carries as the parent of the pitcher x pitch-type effects) and his pitcher x pitch-type
  effect (``pitcher_pitch``). Neither is shrunk by the fit under ``control_pitcher_pitch = within``. The two are merged into one effect per (pitcher, type) and that effect is shrunk after the
  fit, by empirical Bayes (the overall effect is then dropped: leaving it in unshrunk made every pitcher far worse than the league average):
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


def _pitcher_block(model) -> tuple[int, int] | None:
    """(first row, rows) of the pitcher's overall effects, or None when the model has none."""

    start = int(model.members[0]["n_dense"])
    for g in model.groups:
        if g == "pitcher":
            return start, len(model.indexes["pitcher"])
        start += len(model.indexes[g])
    return None


def _merged(model, coef: np.ndarray) -> np.ndarray:
    """[units, classes]: for every (pitcher, type) unit the pitcher x pitch-type effect plus the pitcher's overall effect, class-centred (adding the same number to every class changes no probability)."""

    a, size = _block(model)
    out = np.asarray(coef[a:a + size], dtype=np.float64).copy()
    pb_ = _pitcher_block(model)
    if pb_ is not None:
        pos = model.indexes["pitcher"].lookup(model.indexes[TARGET].keys // 16)
        ok = pos >= 0
        out[ok] += np.asarray(coef[pb_[0]:pb_[0] + pb_[1]], dtype=np.float64)[pos[ok]]
    return out - out.mean(axis=1, keepdims=True)


def effects_by_member(model) -> np.ndarray:
    """[members, units, classes] merged, class-centred effects of the (pitcher, type) units."""

    return np.stack([_merged(model, m["coef"]) for m in model.members])


def predicted(model, sub: Mapping[str, np.ndarray], chunk: int = 100_000) -> np.ndarray:
    """[rows, K] the model's own probabilities of the training rows (all its effects)."""

    n = len(np.asarray(sub["game_pk"]))
    out = []
    for a in range(0, n, chunk):
        guard.checkpoint("pitcher engine: probabilities")
        rows_sl = {k: np.asarray(v)[a:a + chunk] for k, v in sub.items() if isinstance(v, np.ndarray) and len(v) == n}
        out.append(model.predict_proba(rows_sl, effects="specific"))
    return np.concatenate(out)


def unit_information(model, sub: Mapping[str, np.ndarray], group: str = TARGET, proba: np.ndarray | None = None, chunk: int = 100_000) -> tuple[np.ndarray, np.ndarray]:
    """([units, K, K] information of every unit of ``group`` about its own effect vector, [units] number of its pitches), from the training rows ``sub`` (``proba``: their probabilities)."""

    index = model.indexes[group]
    unit = index.lookup(dz.effect_keys(sub, group))
    U, K = len(index), len(model.classes)
    H = np.zeros((U, K, K)); rows = np.zeros(U)
    n = len(unit)
    for a in range(0, n, chunk):
        guard.checkpoint("pitcher engine: information")
        sl = slice(a, min(n, a + chunk))
        if proba is None:
            rows_sl = {k: np.asarray(v)[sl] for k, v in sub.items() if isinstance(v, np.ndarray) and len(v) == n}
            p = model.predict_proba(rows_sl, effects="specific")
        else:
            p = proba[sl]
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
        per_class = np.where(np.isfinite(se2), tau2[None, :] / (tau2[None, :] + se2), 0.0)
    # One factor per unit for all classes: the effects of a unit offset each other (more balls, fewer called strikes), so pulling every class by its own factor breaks the offsets and moves the
    # outcome mix (the run value) of every pitcher the same way. The classes are weighted by their information (1 / the mean variance of the class).
    fin = np.where(np.isfinite(se2), se2, np.nan)
    with np.errstate(all="ignore"):
        wk = 1.0 / np.nanmean(fin, axis=0)
    wk = np.where(np.isfinite(wk), wk, 0.0)
    scalar = (per_class * wk[None, :]).sum(axis=1) / max(float(wk.sum()), 1e-300)
    return np.repeat(scalar[:, None], K, axis=1), tau2, se2


CONTEXT_GROUPS = ("pitcher_game", "batter", "batter_count")


def shrink_all(model, sub: Mapping[str, np.ndarray]):
    """(factor of the (pitcher, type) units, tau2, se2, {group: factor} of the day, batter and batter x count effects). The day and batter effects are fixed effects as unshrunk as the pitcher's: averaging
    them on the probability scale while the pitcher's level is shrunk makes the average too low (the centred day effects have the pitcher's level as their parent), so they are shrunk by the same
    empirical Bayes, each group on its own."""

    proba = predicted(model, sub)
    beta = effects_by_member(model).mean(axis=0)
    H, _ = unit_information(model, sub, TARGET, proba)
    factor, tau2, se2 = shrink_factors(beta, H)
    others = {}
    for name in CONTEXT_GROUPS:
        if name in model.groups:
            E = _effect_rows(model, name)
            E = E - E.mean(axis=1, keepdims=True)
            Hn, _ = unit_information(model, sub, name, proba)
            others[name] = shrink_factors(E, Hn)[0]
    return factor, tau2, se2, others


def shrunk_copy(model, factor: np.ndarray, others: Mapping[str, np.ndarray] | None = None):
    """The model with the merged (pitcher, type) effects shrunk by ``factor`` (every bagged member, the same factor) and the pitcher's overall effect removed: nothing else changes. A type a pitcher
    has no unit for gets the league average."""

    a, size = _block(model)
    pb_ = _pitcher_block(model)
    other = copy.copy(model)
    members = []
    for m in model.members:
        coef = np.array(m["coef"], copy=True)
        merged = _merged(model, m["coef"])
        coef[a:a + size] = (factor * merged).astype(coef.dtype)
        if pb_ is not None:
            coef[pb_[0]:pb_[0] + pb_[1]] = 0.0
        for name, f in (others or {}).items():
            start = int(model.members[0]["n_dense"])
            for g in model.groups:
                if g == name:
                    break
                start += len(model.indexes[g])
            n_units = len(model.indexes[name])
            blk = np.asarray(coef[start:start + n_units], dtype=np.float64)
            coef[start:start + n_units] = (f * (blk - blk.mean(axis=1, keepdims=True))).astype(coef.dtype)
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


def actual_by_pitcher(sub: Mapping[str, np.ndarray]) -> dict[int, tuple[float, float, int]]:
    """pitcher -> (mean run value of a plate appearance, share of plate appearances that were home runs, plate appearances) in the rows ``sub``: what really happened, with the run value
    of every pitch of the plate appearance (the change of the run expectancy) added up. Plate appearances with a missing run value are left out."""

    pitcher = np.asarray(sub["pitcher"]).astype(np.int64)
    if "at_bat_number" in sub:
        key = np.asarray(sub["game_pk"]).astype(np.int64) * 1000 + np.asarray(sub["at_bat_number"]).astype(np.int64)
    else:
        key = np.cumsum(np.asarray(sub["prev1_pitch_type"]).astype(str) == "none").astype(np.int64)
    value = np.asarray(sub["pitch_value"], dtype=float)
    hr = (np.asarray(sub["outcome"]).astype(str) == "in_play_home_run").astype(float)
    pair = np.stack([pitcher, key], axis=1)
    _, inv = np.unique(pair, axis=0, return_inverse=True)
    inv = inv.reshape(-1)
    n_pa = int(inv.max()) + 1 if len(inv) else 0
    total = np.bincount(inv, weights=np.nan_to_num(value), minlength=n_pa)
    bad = np.bincount(inv, weights=np.isnan(value).astype(float), minlength=n_pa) > 0
    is_hr = np.bincount(inv, weights=hr, minlength=n_pa) > 0
    owner = np.zeros(n_pa, dtype=np.int64); owner[inv] = pitcher
    out: dict[int, tuple[float, float, int]] = {}
    for p in np.unique(owner):
        m = (owner == p) & ~bad
        if m.any():
            out[int(p)] = (float(total[m].mean()), float(is_hr[m].mean()), int(m.sum()))
    return out


CONTEXTS = ("none", "day", "batter", "both")


def _effect_rows(model, name: str) -> np.ndarray | None:
    """[units, classes] mean over the members of the effects of group ``name``, or None when the model has no such group."""

    if name not in model.groups:
        return None
    start = int(model.members[0]["n_dense"])
    for g in model.groups:
        if g == name:
            break
        start += len(model.indexes[g])
    size = len(model.indexes[name])
    return np.mean([np.asarray(m["coef"][start:start + size], dtype=np.float64) for m in model.members], axis=0)


def pitcher_context(model, sub: Mapping[str, np.ndarray], pitcher: int, mode: str):
    """The games and the batters this pitcher really had: ``[(weight, day effect [K], batter effect [K], batter x count effects [12, K])]`` over his plate appearances, grouped by what is the
    same (day, batter or both, by ``mode``); the weights are plate appearances and add up to 1. The model's own effects, not shrunk."""

    mine = np.asarray(sub["pitcher"]).astype(np.int64) == pitcher
    game = np.asarray(sub["game_pk"]).astype(np.int64)[mine]; batter = np.asarray(sub["batter"]).astype(np.int64)[mine]
    ab = np.asarray(sub["at_bat_number"]).astype(np.int64)[mine] if "at_bat_number" in sub else np.cumsum(np.asarray(sub["prev1_pitch_type"]).astype(str)[mine] == "none")
    _, first = np.unique(np.stack([game, ab], axis=1), axis=0, return_index=True)
    game, batter = game[first], batter[first]                                              # one row per plate appearance
    K = len(model.classes)
    E_game, E_bat, E_bc = (_effect_rows(model, n) for n in ("pitcher_game", "batter", "batter_count"))
    use_day, use_bat = mode in ("day", "both"), mode in ("batter", "both")
    key = np.stack([game if use_day else np.zeros_like(game), batter if use_bat else np.zeros_like(batter)], axis=1)
    pairs, inv = np.unique(key, axis=0, return_inverse=True)
    inv = inv.reshape(-1)
    count = np.bincount(inv, minlength=len(pairs)).astype(float)
    out = []
    for j, (g, b) in enumerate(pairs):
        day = np.zeros(K)
        if use_day and E_game is not None:
            pos = model.indexes["pitcher_game"].lookup(np.array([int(g) * dz.KEY_BASE + pitcher]))[0]
            day = E_game[pos] if pos >= 0 else day
        bat = np.zeros(K); bc = np.zeros((12, K))
        if use_bat:
            if E_bat is not None:
                pos = model.indexes["batter"].lookup(np.array([int(b)]))[0]
                bat = E_bat[pos] if pos >= 0 else bat
            if E_bc is not None:
                pos = model.indexes["batter_count"].lookup(int(b) * 16 + np.arange(12))
                ok = pos >= 0
                bc[ok] = E_bc[pos[ok]]
        out.append((count[j] / count.sum(), day, bat, bc))
    return out


def averaged_dynamics(shrunk, model, sub, pitcher: int, shape: pc.Shape, group: str, obs, support, n, mode: str) -> pc.Dynamics:
    """The pitcher's dynamics with the day and the batter as they really were: for every (day, batter) he had, the probabilities from his own effects, averaged over his plate appearances on the
    probability scale (not the effects first and then the probability). ``mode``: ``none`` = the usual day and the league-average batter, ``day``, ``batter`` or ``both``."""

    base = pitcher_dynamics(shrunk, pitcher, shape, group, obs, support, n)
    if mode == "none":
        return base
    logp = np.log(np.clip(base.P, 1e-12, None))                                         # [S, A, C] in the engine's class order
    cls = list(model.classes)
    order = [cls.index(c) for c in pc.OUTCOMES if c in cls]                                 # the engine's classes in the model's order (the engine has the same eight)
    cells = dz.count_cell(pb.cell_columns(shape, group)).reshape(shape.size, shape.n_actions)[:, 0]
    acc = np.zeros_like(base.P)
    for weight, day, bat, bc in pitcher_context(model, sub, pitcher, mode):
        delta = (day + bat)[None, :] + bc[cells]                                            # [S, K] in the model's class order
        z = logp + delta[:, None, :][:, :, order] if len(order) == delta.shape[1] else logp + delta[:, None, :]
        z -= z.max(axis=2, keepdims=True)
        p = np.exp(z); p /= p.sum(axis=2, keepdims=True)
        acc += weight * p
    return pc.Dynamics(P=acc, obs=obs, support=support, n=n)


def averaged_dynamics_batched(shrunk, model, sub, pitcher: int, shape: pc.Shape, group: str, obs, support, n, mode: str, eng) -> pc.Dynamics:
    """``averaged_dynamics`` with the days and batters of the pitcher taken many at a time (torch, on the device of ``eng``): the same arithmetic, the contexts in pieces whose size the resource
    guard picks from the memory free now (halved and tried again when a piece does not fit)."""

    import torch

    base = pitcher_dynamics(shrunk, pitcher, shape, group, obs, support, n)
    if mode == "none":
        return base
    cls = list(model.classes)
    order = [cls.index(c) for c in pc.OUTCOMES if c in cls]
    cells = dz.count_cell(pb.cell_columns(shape, group)).reshape(shape.size, shape.n_actions)[:, 0]
    contexts = pitcher_context(model, sub, pitcher, mode)
    if not contexts:                                                                                    # a pitcher with no plate appearance in these rows: no probabilities, as ``averaged_dynamics`` has it
        return pc.Dynamics(P=np.zeros_like(base.P), obs=obs, support=support, n=n)
    weight = np.array([c[0] for c in contexts]); delta = np.stack([(c[1] + c[2])[None, :] + c[3][cells] for c in contexts])            # [J, S, K] in the model's class order
    if len(order) == delta.shape[2]:
        delta = delta[:, :, order]
    dev = eng._t["live"].device
    logp = torch.as_tensor(np.log(np.clip(base.P, 1e-12, None)), device=dev, dtype=torch.float64)                                         # [S, A, C]
    w_t = torch.as_tensor(weight, device=dev, dtype=torch.float64); d_t = torch.as_tensor(delta, device=dev, dtype=torch.float64)
    acc = torch.zeros_like(logp)
    per_item = float(logp.numel() * 8 * 4)
    j, cap = 0, None
    while j < len(weight):
        guard.checkpoint("pitcher engine: day and batter")
        size = guard.batch_items(per_item, eng.device, ceiling=256)
        size = max(1, min(size if cap is None else min(size, cap), len(weight) - j))
        try:
            z = logp[None] + d_t[j:j + size, :, None, :]
            z = z - z.max(dim=-1, keepdim=True).values
            p = torch.exp(z); p = p / p.sum(dim=-1, keepdim=True)
            acc += (w_t[j:j + size, None, None, None] * p).sum(dim=0)
        except (torch.cuda.OutOfMemoryError, MemoryError):
            guard.shrink()
            if eng.device == "cuda":
                torch.cuda.empty_cache()
            cap = max(1, size // 2)
            continue
        if eng.device == "cuda":
            guard.gpu_pause(torch)
        j += size; cap = None
    return pc.Dynamics(P=acc.cpu().numpy(), obs=obs, support=support, n=n)


def solve_pitcher(dyn: pc.Dynamics, env: pc.Environment, shape: pc.Shape, kinds: Sequence[str], trust: float) -> dict[str, dict[str, float]]:
    """Per policy kind: the value at the start of a plate appearance (and the home-run probability for ``HR_KINDS``)."""

    eng = pc.Engine(shape, dyn, env)
    out: dict[str, dict[str, float]] = {}
    for kind in dict.fromkeys(("observed", *kinds)):
        V, pi = eng.solve(kind, trust)
        out[kind] = {"start": eng.start_value(V), "hr": eng.hr_probability(pi) if kind in HR_KINDS else float("nan"), "action": np.argmax(pi, axis=1).astype(np.uint8),
                     "share": pi.astype(np.float16) if kind in ("dp_trust", "dp_unrestricted") else None}
    return out


def _kind_rows(out: Mapping[str, Mapping[str, Any]], b: int) -> dict[str, dict[str, Any]]:
    """The solution of pitcher ``b`` of a batch in the form ``solve_pitcher`` returns."""

    res: dict[str, dict[str, Any]] = {}
    for kind, v in out.items():
        pi = v["pi"][b].cpu().numpy()
        res[kind] = {"start": float(v["start"][b]), "hr": float(v["hr"][b]) if "hr" in v else float("nan"), "action": np.argmax(pi, axis=1).astype(np.uint8),
                     "share": pi.astype(np.float16) if kind in ("dp_trust", "dp_unrestricted") else None}
    return res


def solve_pitchers_batched(prepared, make_dynamics, shape: pc.Shape, env: pc.Environment, kinds: Sequence[str], trust: float, progress=None) -> list[dict[str, dict[str, Any]]]:
    """``solve_pitcher`` for all the pitchers of ``prepared`` ([(pitcher, row, choice)]), many at a time: the batch size is the resource guard's decision (``policy_batch.run_in_batches``)."""

    from .policy_batch import BatchEngine, run_in_batches

    eng = BatchEngine(shape, env)
    done = [0]

    def work(sl: slice, size: int):
        items = prepared[sl]
        P = np.stack([make_dynamics(pid, ch, eng).P for pid, _, ch in items])
        obs = eng.tensor(np.stack([ch["obs"] for _, _, ch in items])); sup = eng.tensor(np.stack([ch["support"] for _, _, ch in items]), dtype=_bool())
        out = eng.solve_kinds(eng.tensor(P), obs, sup, kinds, trust, hr_kinds=HR_KINDS)
        rows = [_kind_rows(out, b) for b in range(len(items))]
        done[0] += len(items)
        if progress:
            progress(done[0], len(prepared))
        return rows

    return [r for part in run_in_batches(eng, len(prepared), work, copies=8.0, label="pitcher engine") for r in part]


def _bool():
    import torch

    return torch.bool


# ------------------------------------------------------------------------------------------------ what one pitcher may throw and what he really throws
def type_counts(sub: Mapping[str, np.ndarray], shape: pc.Shape):
    """Per pitch of ``sub`` that the engine can read: (rows kept, action index, balls, strikes); the same filter as ``policy_build.usage_from_counts``."""

    ptype = np.asarray(sub["pitch_type"]).astype(str)
    a = np.array([pb.PITCH_TYPES.index(t) if t in pb.PITCH_TYPES else -1 for t in ptype]) if len(ptype) else np.zeros(0, dtype=int)
    b = np.asarray(sub["balls"]).astype(int); k = np.asarray(sub["strikes"]).astype(int)
    ok = (a >= 0) & (b < shape.n_balls) & (k < shape.n_strikes)
    return ok, a, b, k


def pitcher_counts(a: np.ndarray, b: np.ndarray, k: np.ndarray, shape: pc.Shape) -> np.ndarray:
    """[balls, strikes, A] the pitch counts of one pitcher by count and pitch type."""

    by_count = np.zeros((shape.n_balls, shape.n_strikes, shape.n_actions))
    np.add.at(by_count, (b, k, a), 1.0)
    return by_count


def pitcher_choice(a: np.ndarray, b: np.ndarray, k: np.ndarray, shape: pc.Shape, league_support: np.ndarray, min_type_share: float) -> dict[str, Any]:
    """``choice_from_counts`` for the pitches of one pitcher (``a`` action, ``b`` balls, ``k`` strikes of each)."""

    return choice_from_counts(pitcher_counts(a, b, k, shape), shape, league_support, min_type_share)


def choice_from_counts(by_count: np.ndarray, shape: pc.Shape, league_support: np.ndarray, min_type_share: float) -> dict[str, Any]:
    """What one pitcher may throw and what he really throws, from his pitch counts ``by_count`` [balls, strikes, A].

    * A pitch type may be chosen for him in a state when the league's support rule allows it there AND it is at least ``min_type_share`` of all his pitches in the group.
    * His observed mix in a state is his own share of each pitch type in the count (pooled over the previous pitch, outs and bases), kept to the types he may throw and renormalised.
      A count in which he threw none of them uses his overall shares of those types. A state in which he may throw nothing (the league does not support what he throws there) keeps his own
      shares of the types he uses at least ``min_type_share`` of the time (so what is recommended there is still something he throws).
    Returns ``obs`` [S, A], ``support`` [S, A], ``allowed`` [A] (pitch types he may be given), ``excluded`` [A] (types the league supports somewhere but he does not use)."""

    A = shape.n_actions
    overall = by_count.sum(axis=(0, 1))
    share = overall / max(overall.sum(), 1.0)
    allowed = (overall > 0) & (share >= min_type_share)
    bb, kk, *_ = shape.coords()
    support = league_support & allowed[None, :]
    own = by_count[bb, kk]                                                           # [S, A] his pitches in the count of each state
    allowed_f = allowed.astype(float)
    over_allowed = np.divide(overall * allowed_f, max((overall * allowed_f).sum(), 1e-12))
    obs = np.zeros_like(own)
    has = support.any(axis=1)
    m = np.where(support, own, 0.0); tot = m.sum(axis=1, keepdims=True)
    fall_sup = np.where(support, over_allowed[None, :], 0.0); fall_sup = fall_sup / np.maximum(fall_sup.sum(axis=1, keepdims=True), 1e-12)
    obs_sup = np.where(tot > 0, m / np.maximum(tot, 1e-12), fall_sup)
    m2 = own * allowed_f[None, :]; tot2 = m2.sum(axis=1, keepdims=True)
    obs_free = np.where(tot2 > 0, m2 / np.maximum(tot2, 1e-12), over_allowed[None, :])
    obs = np.where(has[:, None], obs_sup, obs_free)
    return {"obs": obs, "support": support, "allowed": allowed, "excluded": league_support.any(axis=0) & ~allowed}


# ------------------------------------------------------------------------------------------------ the whole thing for a group
def run(group: str, sub: Mapping[str, np.ndarray], model, env: pc.Environment, shape: pc.Shape, obs, support, n, *, kinds: Sequence[str], trust: float, min_pa: int,
        pa_per_team_season: float, league_saving: Mapping[str, float], league_start: float = float("nan"), league_hr: float = float("nan"), context: str = "both", shrink: bool = True, min_type_share: float = 0.01, batch: bool = True, progress=None
        ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any], dict[str, np.ndarray]]:
    """(summary rows per policy, one detail row per pitcher, the coverage row, the recommended pitch of every pitcher) of the per-pitcher engine for one group; ``sub`` are the training rows
    of the group. The recommendation is the pitch type with the largest share of the policy in each state (``{'pitchers': ids, kind: uint8 [pitchers, states]}``)."""

    if shrink:                                                                  # a model whose effects are for removing a bias: shrunk here after the fit
        factor, tau2, se2, others = shrink_all(model, sub)
        shrunk = shrunk_copy(model, factor, others)
    else:                                                                       # a prediction model: its effects are already shrunk by the fit itself
        factor, tau2, shrunk = np.ones((len(model.indexes[TARGET]), len(model.classes))), np.zeros(len(model.classes)), model
    _, unit_rows = unit_information(model, sub, TARGET, predicted(model, sub))
    ids, pa, pitches = plate_appearances(sub)
    use = np.nonzero(pa >= min_pa)[0]
    keys = model.indexes[TARGET].keys
    unit_pitcher = keys // 16
    unit_factor = factor.mean(axis=1)
    use_kinds = [k for k in kinds]
    detail: list[dict[str, Any]] = []
    allowed_by_pitcher: list[np.ndarray] = []
    counts_by_pitcher: list[np.ndarray] = []
    results: list[dict[str, dict[str, float]]] = []
    order = use[np.argsort(-pa[use])]
    ok_rows, act_all, b_all, k_all = type_counts(sub, shape)
    pit_all = np.asarray(sub["pitcher"]).astype(np.int64)
    prepared: list[tuple[int, int, dict[str, Any]]] = []
    for j in order:
        guard.checkpoint("pitcher engine")
        pid = int(ids[j])
        mine_rows = ok_rows & (pit_all == pid)
        counts_p = pitcher_counts(act_all[mine_rows], b_all[mine_rows], k_all[mine_rows], shape)
        counts_by_pitcher.append(counts_p.astype(np.float32))
        prepared.append((pid, int(j), choice_from_counts(counts_p, shape, support, min_type_share)))
    def make(pid, ch, eng=None):
        if eng is None:
            return averaged_dynamics(shrunk, shrunk, sub, pid, shape, group, ch["obs"], ch["support"], n, context)
        return averaged_dynamics_batched(shrunk, shrunk, sub, pid, shape, group, ch["obs"], ch["support"], n, context, eng)

    if batch:
        results = solve_pitchers_batched(prepared, make, shape, env, use_kinds, trust, progress)
    else:
        results = []
        for i, (pid, j, choice) in enumerate(prepared):
            guard.checkpoint("pitcher engine")
            results.append(solve_pitcher(make(pid, choice), env, shape, use_kinds, trust))
            if progress:
                progress(i + 1, len(prepared))
    for (pid, j, choice), res in zip(prepared, results):
        mine = unit_pitcher == pid
        w = unit_rows[mine]
        row = {"group": group, "pitcher": pid, "pa": int(pa[j]), "pitches": int(pitches[j]), "units": int(mine.sum()),
               "shrink_mean": float((unit_factor[mine] * w).sum() / w.sum()) if w.sum() > 0 else float("nan"), "start_observed": res["observed"]["start"],
               "hr_observed": res["observed"]["hr"], "allowed_types": int(choice["allowed"].sum()), "excluded_types": int(choice["excluded"].sum()),
               "excluded_names": " ".join(pb.PITCH_TYPES[x] for x in np.nonzero(choice["excluded"])[0])}
        row["allowed_names"] = " ".join(pb.PITCH_TYPES[x] for x in np.nonzero(choice["allowed"])[0])
        allowed_by_pitcher.append(choice["allowed"])
        for k in use_kinds:
            if k != "observed":
                row[f"saving_{k}"] = res["observed"]["start"] - res[k]["start"]
        detail.append(row)
    total_pa = float(pa.sum()); used_pa = float(pa[use].sum()) if len(use) else 0.0
    coverage = {"group": group, "min_pa": int(min_pa), "pitchers_total": int(len(ids)), "pitchers_used": int(len(use)), "pa_total": int(total_pa), "pa_used": int(used_pa),
                "pa_share": used_pa / total_pa if total_pa else float("nan"), "pitch_share": float(pitches[use].sum() / pitches.sum()) if len(use) else 0.0,
                "units": int(len(keys)), "units_pulled_over_half": float((unit_factor < 0.5).mean()) if len(keys) else float("nan"),
                "tau_by_class": {c: float(np.sqrt(t)) for c, t in zip(model.classes, tau2)}}
    if detail:                                                                  # the check: playing the observed mix, every pitcher against what he really did in these rows
        actual = actual_by_pitcher(sub)
        for d in detail:
            a = actual.get(d["pitcher"], (float("nan"), float("nan"), 0))
            d["predicted_excess_runs"] = d["start_observed"] - league_start          # the engine's start value above the league-average one, in runs per plate appearance
            d["actual_excess_runs"], d["actual_hr"], d["actual_pa"] = a
        w = np.array([d["pa"] for d in detail], dtype=float)
        ok = np.array([np.isfinite(d["actual_excess_runs"]) for d in detail])
        wavg = lambda key, m=ok: float(np.average([d[key] for d, f in zip(detail, m) if f], weights=w[m])) if m.any() else float("nan")
        coverage.update({"start_observed_pitchers": float(np.average([d["start_observed"] for d in detail], weights=w)), "start_observed_league": float(league_start),
                         "hr_observed_pitchers": float(np.average([d["hr_observed"] for d in detail], weights=w)), "hr_observed_league": float(league_hr),
                         "predicted_excess_runs": wavg("predicted_excess_runs"), "actual_excess_runs": wavg("actual_excess_runs"), "hr_predicted": wavg("hr_observed"), "hr_actual": wavg("actual_hr")})
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
    policies: dict[str, np.ndarray] = {"pitchers": np.array([d["pitcher"] for d in detail], dtype=np.int64), "pa": np.array([d["pa"] for d in detail], dtype=np.float64)}
    if allowed_by_pitcher:
        policies["allowed"] = np.stack(allowed_by_pitcher).astype(np.uint8)
        policies["counts"] = np.stack(counts_by_pitcher)
    for k in use_kinds:
        if k in ("dp_trust", "dp_unrestricted") and results:
            policies[k] = np.stack([r[k]["action"] for r in results])
            policies[k + "_share"] = np.stack([r[k]["share"] for r in results])
    return summary, detail, coverage, policies


# ------------------------------------------------------------------------------------------------ the league-average engine, one pitcher at a time
def _canonical_obs(obs: np.ndarray, choice: Mapping[str, Any]) -> np.ndarray:
    """The observed mix ``dp_unrestricted`` uses where nothing may be given: the league's mix of the types the pitcher may be given (shared by every pitcher with the same types)."""

    canon = pc.masked_shares(obs, choice["allowed"][None, :])
    return np.where(canon.sum(axis=1, keepdims=True) > 0, canon, choice["allowed"].astype(float)[None, :] / max(float(choice["allowed"].sum()), 1.0))


def league_run(group: str, sub: Mapping[str, np.ndarray], P: np.ndarray, env: pc.Environment, shape: pc.Shape, obs, support, n, *, trust: float, min_type_share: float = 0.01,
               batch: bool = True, progress=None) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any], dict[str, np.ndarray]]:
    """The league-average engine (every pitch type as strong as the league average, ``P`` [S, A, C]) solved for every pitcher that has pitches in ``sub`` (the training rows of the group),
    with the pitch types he may be given (the league's support rule and at least ``min_type_share`` of his pitches) and his own mix by count as the observed behaviour.

    ``dp_unrestricted`` depends only on the types he may be given: pitchers with the same types share one solution (a state where nothing may be given plays the league's mix of the types he may
    be given). ``dp_trust`` and the observed mix are his own. The pitchers are solved many at a time (``batch``; the resource guard sizes the batches), or one by one (``batch=False``, the
    reference the batched way is tested against). Returns (summary rows, one detail row per pitcher, the coverage row, the stored policies of every pitcher)."""

    ids, pa, pitches = plate_appearances(sub)
    ok_rows, act_all, b_all, k_all = type_counts(sub, shape)
    pit_all = np.asarray(sub["pitcher"]).astype(np.int64)
    order = np.argsort(-pa)
    pending = []                                                                                  # (pitcher id, row in ids, counts, choice, key of his set of types)
    for j in order:
        guard.checkpoint("league engine by pitcher")
        pid = int(ids[j])
        counts = pitcher_counts(act_all[ok_rows & (pit_all == pid)], b_all[ok_rows & (pit_all == pid)], k_all[ok_rows & (pit_all == pid)], shape)
        if counts.sum() <= 0:
            continue
        ch = choice_from_counts(counts, shape, support, min_type_share)
        pending.append((pid, int(j), counts, ch, ch["allowed"].tobytes()))
    sets: dict[bytes, Mapping[str, Any]] = {}
    for _, _, _, ch, key in pending:
        sets.setdefault(key, ch)
    keys = list(sets)
    if batch:
        pi_u_by_key, solved = _league_batched(shape, env, P, obs, support, n, sets, keys, pending, trust, progress)
    else:
        pi_u_by_key, solved = {}, []
        for i, (pid, j, counts, ch, key) in enumerate(pending):
            guard.checkpoint("league engine by pitcher")
            if key not in pi_u_by_key:
                eng_u = pc.Engine(shape, pc.Dynamics(P=P, obs=_canonical_obs(obs, ch), support=ch["support"], n=n), env)
                pi_u_by_key[key] = eng_u.solve("dp_unrestricted", trust)[1]
            eng = pc.Engine(shape, pc.Dynamics(P=P, obs=ch["obs"], support=ch["support"], n=n), env)
            V_o, _ = eng.solve("observed", trust)
            V_t, pi_t = eng.solve("dp_trust", trust)
            V_u = eng.evaluate(pi_u_by_key[key])
            solved.append((eng.start_value(V_o), eng.start_value(V_t), eng.start_value(V_u), pi_t))
            if progress:
                progress(i + 1, len(pending))
    detail: list[dict[str, Any]] = []
    out: dict[str, list] = {"allowed": [], "counts": [], "dp_trust": [], "dp_trust_share": [], "dp_unrestricted": [], "dp_unrestricted_share": []}
    for (pid, j, counts, ch, key), (base, v_trust, v_unres, pi_t) in zip(pending, solved):
        pi_u = pi_u_by_key[key]
        detail.append({"group": group, "pitcher": pid, "pa": int(pa[j]), "pitches": int(pitches[j]), "allowed_types": int(ch["allowed"].sum()), "excluded_types": int(ch["excluded"].sum()),
                       "excluded_names": " ".join(pb.PITCH_TYPES[x] for x in np.nonzero(ch["excluded"])[0]), "start_observed": base,
                       "saving_dp_trust": base - v_trust, "saving_dp_unrestricted": base - v_unres})
        out["allowed"].append(ch["allowed"].astype(np.uint8)); out["counts"].append(counts.astype(np.float32))
        out["dp_trust"].append(np.argmax(pi_t, axis=1).astype(np.uint8)); out["dp_trust_share"].append(pi_t.astype(np.float16))
        out["dp_unrestricted"].append(np.argmax(pi_u, axis=1).astype(np.uint8)); out["dp_unrestricted_share"].append(pi_u.astype(np.float16))
    policies: dict[str, np.ndarray] = {"pitchers": np.array([d["pitcher"] for d in detail], dtype=np.int64), "pa": np.array([d["pa"] for d in detail], dtype=np.float64)}
    for key, v in out.items():
        if v:
            policies[key] = np.stack(v)
    w = np.array([d["pa"] for d in detail], dtype=float)
    summary = [{"group": group, "policy": k, "pitchers": len(detail), "saving_pa": float(np.average([d[f"saving_{k}"] for d in detail], weights=w)) if len(detail) else float("nan")} for k in ("dp_trust", "dp_unrestricted")]
    coverage = {"group": group, "pitchers": len(detail), "pitchers_total": int(len(ids)), "different_type_sets": len(sets), "excluded_types_mean": float(np.mean([d["excluded_types"] for d in detail])) if detail else float("nan"),
                "pa": int(sum(d["pa"] for d in detail)), "min_type_share": float(min_type_share)}
    return summary, detail, coverage, policies


def _league_batched(shape, env, P, obs, support, n, sets, keys, pending, trust, progress):
    """The batched solutions of ``league_run``: (pi_unrestricted by set of types, [(start observed, start dp_trust, start dp_unrestricted, pi dp_trust)] per pitcher)."""

    import torch

    from .policy_batch import BatchEngine, run_in_batches

    eng = BatchEngine(shape, env)
    pi_u_by_key: dict[bytes, np.ndarray] = {}

    def work_sets(sl: slice, size: int):
        ks = keys[sl]
        o = eng.tensor(np.stack([_canonical_obs(obs, sets[k]) for k in ks])); sp = eng.tensor(np.stack([sets[k]["support"] for k in ks]), dtype=torch.bool)
        s_, e_ = eng._effective(o, sp)
        _, pi = eng.solve_dp("dp_unrestricted", eng.tensor(P), o, s_, e_, trust)
        return [(k, pi[i].cpu().numpy()) for i, k in enumerate(ks)]

    for part in run_in_batches(eng, len(keys), work_sets, copies=6.0, label="league engine by pitcher"):
        pi_u_by_key.update(part)
    done = [0]

    def work_pitchers(sl: slice, size: int):
        items = pending[sl]
        o = eng.tensor(np.stack([it[3]["obs"] for it in items])); sp = eng.tensor(np.stack([it[3]["support"] for it in items]), dtype=torch.bool)
        Pt = eng.tensor(P)
        res = eng.solve_kinds(Pt, o, sp, ("dp_trust",), trust)
        pi_u = eng.tensor(np.stack([pi_u_by_key[it[4]] for it in items]))
        v_u = eng.start_value(eng.evaluate(pi_u, Pt)).cpu().numpy()
        rows = [(float(res["observed"]["start"][i]), float(res["dp_trust"]["start"][i]), float(v_u[i]), res["dp_trust"]["pi"][i].cpu().numpy()) for i in range(len(items))]
        done[0] += len(items)
        if progress:
            progress(done[0], len(pending))
        return rows

    solved = [r for part in run_in_batches(eng, len(pending), work_pitchers, copies=6.0, label="league engine by pitcher") for r in part]
    return pi_u_by_key, solved


def score_batched(shape: pc.Shape, env: pc.Environment, entries: Sequence[Mapping[str, Any]], kinds: Sequence[str], shared_P: np.ndarray | None = None) -> list[dict[str, float]]:
    """The start value of the observed mix and of each policy in ``kinds`` for many pitchers at once (2025 scoring): ``entries`` hold ``obs`` [S, A], ``shares`` {kind: [S, A]} and, unless the
    probabilities are the same for everybody (``shared_P``), ``P`` = a function of the batch engine that gives the pitcher's probabilities [S, A, C]. The batch size is the resource guard's decision."""

    from .policy_batch import BatchEngine, run_in_batches

    eng = BatchEngine(shape, env)
    K = len(kinds) + 1

    def work(sl: slice, size: int):
        items = entries[sl]; b = len(items)
        pi = eng.tensor(np.stack([np.stack([it["obs"]] + [np.asarray(it["shares"][k], dtype=float) for k in kinds]) for it in items])).reshape(b * K, shape.size, shape.n_actions)
        if shared_P is not None:
            P = eng.tensor(shared_P)
        else:
            P = eng.tensor(np.stack([it["P"](eng) for it in items])).repeat_interleave(K, dim=0)
        st = eng.start_value(eng.evaluate(pi, P)).cpu().numpy().reshape(b, K)
        return [dict(zip(("observed", *kinds), (float(x) for x in st[i]))) for i in range(b)]

    return [r for part in run_in_batches(eng, len(entries), work, copies=8.0 * K, label="policy score") for r in part]
