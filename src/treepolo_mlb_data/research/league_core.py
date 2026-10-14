"""Library layer of the league model (P6 plan 3.4, 4.6): feature levels L0-L3, evaluation on unseen players / unseen seasons,
paired gains with game-level bootstrap, and the type-contrast check (G1b). No database access here: everything takes columnar arrays."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

import numpy as np

from . import gbm
from . import modeling as md
from .profiles import PITCH_TYPES, ProfileModel

LEVELS = ("L0", "L1", "L2", "L3")
SITUATION_NUMERIC = ("balls", "strikes", "outs_when_up", "streak_cap")
SWING_CLASSES = ("whiff", "foul_tip", "foul", "in_play_home_run", "in_play_non_hr")


def situation_features(cols: Mapping[str, np.ndarray], *, with_action: bool) -> tuple[np.ndarray, list[str]]:
    """Count, outs, runners (3 bits), previous pitch type (9 + none), same-type streak (1, 2, 3+) and, for the outcome target, the pitch type."""

    n = len(cols["game_pk"]); names: list[str] = []; parts: list[np.ndarray] = []
    for name in ("balls", "strikes", "outs_when_up"):
        parts.append(np.asarray(cols[name], dtype=np.float64)); names.append(name)
    bases = np.asarray(cols["bases"], dtype=np.int64)
    for bit, label in enumerate(("on_1b", "on_2b", "on_3b")):
        parts.append(((bases >> bit) & 1).astype(np.float64)); names.append(label)
    streak = np.minimum(np.nan_to_num(np.asarray(cols["streak_pos"], dtype=np.float64), nan=1.0), 3.0)
    parts.append(streak); names.append("streak_cap")
    prev = np.asarray(cols["prev1_pitch_type"]).astype(str)
    for t in PITCH_TYPES:
        parts.append((prev == t).astype(np.float64)); names.append(f"prev1={t}")
    parts.append((~np.isin(prev, PITCH_TYPES)).astype(np.float64)); names.append("prev1=none")
    if with_action:
        ptype = np.asarray(cols["pitch_type"]).astype(str)
        for t in PITCH_TYPES:
            parts.append((ptype == t).astype(np.float64)); names.append(f"type={t}")
    return np.column_stack(parts).astype(np.float32), names


def level_matrix(profile: ProfileModel, cols: Mapping[str, np.ndarray], level: str, target: str, *, leave_game_out: bool):
    """Feature matrix of one ladder level. L0 situation (+ action); L1 + pitcher descriptors; L2 + batter descriptors; L3 both."""

    if level not in LEVELS:
        raise ValueError(f"unknown level {level!r}")
    x, names = situation_features(cols, with_action=(target == "outcome"))
    blocks = [x]
    if level in ("L1", "L3"):
        side = "pitcher" if target == "outcome" else "repertoire"
        blocks.append(profile.transform(cols, side, leave_game_out=leave_game_out).astype(np.float32)); names += [f"P:{n}" for n in profile.feature_names(side)]
    if level in ("L2", "L3"):
        blocks.append(profile.transform(cols, "batter", leave_game_out=leave_game_out).astype(np.float32)); names += [f"B:{n}" for n in profile.feature_names("batter")]
    return np.hstack(blocks), names


def encode_outcome(outcome: np.ndarray, merge: Mapping[str, str]):
    """Outcome category -> class index; rows whose category is not in ``merge`` are dropped (mask False)."""

    outcome = np.asarray(outcome).astype(str)
    keep = np.isin(outcome, list(merge))
    classes = sorted({merge[o] for o in np.unique(outcome[keep])})
    lookup = {c: i for i, c in enumerate(classes)}
    y = np.full(len(outcome), -1, dtype=np.int64)
    y[keep] = [lookup[merge[o]] for o in outcome[keep]]
    return y, classes, keep


def encode_usage(ptype: np.ndarray):
    ptype = np.asarray(ptype).astype(str)
    keep = np.isin(ptype, PITCH_TYPES)
    lookup = {t: i for i, t in enumerate(PITCH_TYPES)}
    y = np.full(len(ptype), -1, dtype=np.int64)
    y[keep] = [lookup[t] for t in ptype[keep]]
    return y, list(PITCH_TYPES), keep


def unit_folds(ids: np.ndarray, folds: int, seed: int) -> list[tuple[np.ndarray, np.ndarray]]:
    """K folds over PLAYERS: a held-out player's rows are all in the test fold (the model never saw him)."""

    ids = np.asarray(ids)
    uniq = np.unique(ids)
    rng = np.random.default_rng(seed); order = rng.permutation(len(uniq))
    fold_of = {int(u): i % folds for i, u in zip(order, uniq[order])}
    fold = np.array([fold_of[int(u)] for u in ids])
    return [(np.nonzero(fold != k)[0], np.nonzero(fold == k)[0]) for k in range(folds) if (fold == k).any() and (fold != k).any()]


def game_split(games: np.ndarray, fraction: float, seed: int):
    """(fit rows, validation rows) with whole games in the validation part (for early stopping)."""

    uniq = np.unique(games); rng = np.random.default_rng(seed)
    valid_games = set(rng.choice(uniq, size=max(int(len(uniq) * fraction), 1), replace=False).tolist()) if len(uniq) > 1 else set()
    mask = np.array([g in valid_games for g in games.tolist()])
    return np.nonzero(~mask)[0], np.nonzero(mask)[0]


@dataclass
class LevelFit:
    members: list
    proba: np.ndarray            # mean over members for the evaluated rows
    member_loss: list[float]
    seconds: float
    n_iter: float
    names: list[str] = field(default_factory=list)


def fit_predict_level(backend: str, device: str, params: Mapping[str, Any], x_train, y_train, games_train, x_eval, n_classes: int, *,
                      reps: int, seed: int, valid_fraction: float = 0.1, y_eval=None) -> LevelFit:
    """Fit ``reps`` (>= 1) models on game-level bootstrap resamples (reps == 1 -> one plain fit) and predict ``x_eval``."""

    fit_idx, val_idx = game_split(games_train, valid_fraction, seed) if backend == "xgboost" else (np.arange(len(y_train)), np.array([], dtype=int))
    xt, yt, gt = x_train[fit_idx], y_train[fit_idx], games_train[fit_idx]
    xv = x_train[val_idx] if len(val_idx) else None; yv = y_train[val_idx] if len(val_idx) else None
    if reps <= 1:
        members = [gbm.fit_gbm(backend, device, params, xt, yt, n_classes, x_valid=xv, y_valid=yv, seed=seed)]
    else:
        members = gbm.bag_fit(backend, device, params, xt, yt, gt, n_classes, reps=reps, seed=seed, x_valid=xv, y_valid=yv)
    mean, stack = gbm.bagged_proba(members, x_eval)
    losses = [float(md.row_logloss(y_eval, stack[i]).mean()) for i in range(len(members))] if y_eval is not None else []
    return LevelFit(members, mean, losses, float(sum(m.seconds for m in members)), float(np.mean([m.n_iter for m in members])))


def prior_proba(y_train: np.ndarray, n_eval: int, n_classes: int) -> np.ndarray:
    freq = np.bincount(y_train, minlength=n_classes) / len(y_train)
    return np.tile(freq, (n_eval, 1))


def level_gain(loss_base: np.ndarray, loss_other: np.ndarray, games: np.ndarray, *, reps: int, seed: int, confidence: float = 0.95) -> dict[str, float]:
    """Mean per-row logloss improvement of ``other`` over ``base`` (positive = better) with a game-level bootstrap interval."""

    mean, lo, hi, se = md.paired_game_bootstrap(loss_base - loss_other, games, reps=reps, seed=seed, confidence=confidence)
    return {"delta_logloss": mean, "lo": lo, "hi": hi, "se": se}


# ----------------------------------------------------------------------------------------------- G1b: type contrast
def _cell_sums(unit: np.ndarray, ptype: np.ndarray, values: Sequence[np.ndarray]):
    key = np.char.add(np.char.add(unit.astype(str), "|"), np.asarray(ptype).astype(str))
    uniq, inv = np.unique(key, return_inverse=True)
    sums = [np.bincount(inv, weights=v, minlength=len(uniq)) for v in values]
    unit_of = np.array([u.split("|")[0] for u in uniq])
    return uniq, inv, sums, unit_of


def type_contrast_gain(unit: np.ndarray, ptype: np.ndarray, y: np.ndarray, classes: Sequence[str], proba_base: np.ndarray, proba_other: np.ndarray, *,
                       min_cell_swings: float = 30.0, reps: int = 200, seed: int = 0, confidence: float = 0.95) -> dict[str, float]:
    """G1b: does ``proba_other`` predict which pitch type works best for a unit better than ``proba_base``?

    Per (unit, pitch type) cell: observed whiff-per-swing (ratio of sums) vs predicted (ratio of summed probabilities); a CONTRAST is the cell
    rate minus the same unit's pooled rate over all its types. The score is the swing-weighted squared error of the predicted contrast; the
    reported gain is MSE(base) - MSE(other) (positive = better) with a bootstrap over units."""

    classes = list(classes)
    swing_idx = [classes.index(c) for c in SWING_CLASSES if c in classes]; whiff_idx = classes.index("whiff")
    swing_obs = np.isin(y, swing_idx).astype(float); whiff_obs = (y == whiff_idx).astype(float)
    cols = [swing_obs, whiff_obs, proba_base[:, swing_idx].sum(axis=1), proba_base[:, whiff_idx], proba_other[:, swing_idx].sum(axis=1), proba_other[:, whiff_idx]]
    uniq, inv, sums, unit_of = _cell_sums(unit, ptype, cols)
    s_obs, w_obs, sb, wb, so, wo = sums
    ok = s_obs >= min_cell_swings
    # pooled per unit over the cells that qualify
    unit_codes, unit_inv = np.unique(unit_of[ok], return_inverse=True)
    def pooled(num, den):
        n_ = np.bincount(unit_inv, weights=num[ok], minlength=len(unit_codes)); d_ = np.bincount(unit_inv, weights=den[ok], minlength=len(unit_codes))
        return n_ / np.maximum(d_, 1e-12)
    cell_unit = unit_inv
    count_cells = np.bincount(unit_inv, minlength=len(unit_codes))
    usable_unit = count_cells >= 2
    rate_obs = w_obs[ok] / s_obs[ok]; rate_b = wb[ok] / np.maximum(sb[ok], 1e-12); rate_o = wo[ok] / np.maximum(so[ok], 1e-12)
    d_obs = rate_obs - pooled(w_obs, s_obs)[cell_unit]
    d_b = rate_b - pooled(wb, sb)[cell_unit]; d_o = rate_o - pooled(wo, so)[cell_unit]
    weight = s_obs[ok] * usable_unit[cell_unit]
    err_b = weight * (d_obs - d_b) ** 2; err_o = weight * (d_obs - d_o) ** 2
    num_u = np.bincount(cell_unit, weights=err_b - err_o, minlength=len(unit_codes)); den_u = np.bincount(cell_unit, weights=weight, minlength=len(unit_codes))
    total = den_u.sum()
    if total <= 0:
        return {"mse_base": float("nan"), "mse_other": float("nan"), "gain": float("nan"), "lo": float("nan"), "hi": float("nan"), "units": 0}
    rng = np.random.default_rng(seed)
    w = rng.poisson(1.0, size=(reps, len(unit_codes))).astype(float)
    boot = (w @ num_u) / np.maximum(w @ den_u, 1e-12)
    lo_q, hi_q = (1 - confidence) / 2 * 100, (1 + confidence) / 2 * 100
    return {"mse_base": float(err_b.sum() / total), "mse_other": float(err_o.sum() / total), "gain": float(num_u.sum() / total),
            "lo": float(np.percentile(boot, lo_q)), "hi": float(np.percentile(boot, hi_q)), "units": int(usable_unit.sum())}
