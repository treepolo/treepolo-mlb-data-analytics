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


def level_matrix(profile: ProfileModel, cols: Mapping[str, np.ndarray], level: str, target: str, *, history: bool):
    """Feature matrix of one ladder level. L0 situation (+ action); L1 + pitcher descriptors; L2 + batter descriptors; L3 both."""

    if level not in LEVELS:
        raise ValueError(f"unknown level {level!r}")
    x, names = situation_features(cols, with_action=(target == "outcome"))
    blocks = [x]
    if level in ("L1", "L3"):
        side = "pitcher" if target == "outcome" else "repertoire"
        blocks.append(profile.transform(cols, side, history=history).astype(np.float32)); names += [f"P:{n}" for n in profile.feature_names(side)]
    if level in ("L2", "L3"):
        blocks.append(profile.transform(cols, "batter", history=history).astype(np.float32)); names += [f"B:{n}" for n in profile.feature_names("batter")]
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


# ------------------------------------------------------------------------------------------------ one group, end to end
def take(cols: Mapping[str, np.ndarray], idx: np.ndarray) -> dict[str, np.ndarray]:
    return {k: v[idx] for k, v in cols.items()}


def _targets(cols: Mapping[str, np.ndarray], target: str, merge: Mapping[str, str] | None):
    if target == "usage":
        return encode_usage(cols["pitch_type"])
    return encode_outcome(cols["outcome"], merge)


def _pitch_filter(cols: Mapping[str, np.ndarray]) -> np.ndarray:
    return np.isin(np.asarray(cols["pitch_type"]).astype(str), PITCH_TYPES)


def evaluate_group(cols_train: Mapping[str, np.ndarray], cols_test: Mapping[str, np.ndarray] | None, *, profile: ProfileModel, target: str = "outcome",
                   levels: Sequence[str] = LEVELS, backend: str = "hgb", device: str = "cpu", params: Mapping[str, Any] | None = None,
                   reps: int = 1, reps_by_level: Mapping[str, int] | None = None, seed: int = 0, merge: Mapping[str, str] | None = None, bootstrap_reps: int = 200, cold_by: Sequence[str] = (),
                   cold_folds: int = 5, calibrate: bool = False, min_cell_swings: float = 30.0, confidence: float = 0.95, fit_final: bool = False) -> dict[str, Any]:
    """Fit every ladder level on ``cols_train`` and score it on the held-out season (``cols_test``) and/or on unseen players (``cold_by``).

    Descriptors of training rows come from EARLIER games only (history mode); those of ``cols_test`` from ``profile`` as fitted (the whole training period).
    Returns row lists: ``fit``, ``gain`` (level vs L0, paired game bootstrap), ``contrast`` (G1b), ``calibration``."""

    params = dict(params or {})
    from .modeling import calibration_rows

    keep_tr = _pitch_filter(cols_train) if target == "usage" else np.ones(len(cols_train["game_pk"]), dtype=bool)
    ytr_all, classes, ok_tr = _targets(cols_train, target, merge)
    keep_tr = keep_tr & ok_tr
    ctr = take(cols_train, np.nonzero(keep_tr)[0]); ytr = ytr_all[keep_tr]
    n_classes = len(classes)
    out: dict[str, Any] = {"fit": [], "gain": [], "contrast": [], "calibration": [], "classes": classes, "models": {}}
    games_tr = np.asarray(ctr["game_pk"])
    xtr = {lv: level_matrix(profile, ctr, lv, target, history=True) for lv in levels}
    oof: dict[tuple[str, str], np.ndarray] = {}; oof_rows: dict[str, np.ndarray] = {}
    # ---- cold start: held-out PLAYERS inside the training period
    for by in cold_by:
        ids = np.asarray(ctr[by]); folds = unit_folds(ids, cold_folds, seed)
        eval_idx = np.concatenate([te for _, te in folds]); oof_rows[by] = eval_idx
        losses = {}
        for lv in levels:
            proba = np.zeros((len(eval_idx), n_classes)); pos = {int(i): k for k, i in enumerate(eval_idx)}
            secs = 0.0
            for tr, te in folds:
                fit = fit_predict_level(backend, device, params, xtr[lv][0][tr], ytr[tr], games_tr[tr], xtr[lv][0][te], n_classes, reps=1, seed=seed, y_eval=ytr[te])
                proba[[pos[int(i)] for i in te]] = fit.proba; secs += fit.seconds
            oof[(by, lv)] = proba
            losses[lv] = md.row_logloss(ytr[eval_idx], proba)
            out["fit"].append({"mode": f"cold_{by}", "level": lv, "n_train": int(np.mean([len(t) for t, _ in folds])), "n_eval": len(eval_idx), "logloss": float(losses[lv].mean()),
                               "brier": float(md.multiclass_brier_rows(ytr[eval_idx], proba).mean()), "fit_seconds": secs, "n_iter": float("nan"), "backend": backend})
        prior = np.zeros(len(eval_idx)); position = {int(i): k for k, i in enumerate(eval_idx)}
        for tr, te in folds:
            pr = np.bincount(ytr[tr], minlength=n_classes) / len(tr)
            prior[[position[int(i)] for i in te]] = -np.log(np.clip(pr[ytr[te]], md.EPS, 1.0))
        for lv in levels:
            if lv == "L0":
                continue
            row = {"mode": f"cold_{by}", "comparison": f"L0→{lv}", "n_eval": len(eval_idx),
                   **level_gain(losses["L0"], losses[lv], ids[eval_idx], reps=bootstrap_reps, seed=seed, confidence=confidence)}   # resample PLAYERS: that is the unit of independence
            out["gain"].append(row)
            if target == "outcome" and "whiff" in classes and (lv in ("L1", "L3") and by == "pitcher" or lv in ("L2", "L3") and by == "batter"):
                c = type_contrast_gain(ids[eval_idx], np.asarray(ctr["pitch_type"])[eval_idx], ytr[eval_idx], classes, oof[(by, "L0")], oof[(by, lv)],
                                       min_cell_swings=min_cell_swings, reps=bootstrap_reps, seed=seed, confidence=confidence)
                out["contrast"].append({"mode": f"cold_{by}", "side": by, "comparison": f"L0→{lv}", **c})
    # ---- held-out season
    if cols_test is not None:
        keep_te = _pitch_filter(cols_test) if target == "usage" else np.ones(len(cols_test["game_pk"]), dtype=bool)
        yte_all, _, ok_te = _targets(cols_test, target, merge)
        keep_te = keep_te & ok_te
        cte = take(cols_test, np.nonzero(keep_te)[0]); yte = yte_all[keep_te]; games_te = np.asarray(cte["game_pk"])
        xte = {lv: level_matrix(profile, cte, lv, target, history=False)[0] for lv in levels}
        prior_te = -np.log(np.clip(prior_proba(ytr, len(yte), n_classes)[np.arange(len(yte)), yte], md.EPS, 1.0))
        losses_te = {}; probas_te = {}
        for lv in levels:
            fit = fit_predict_level(backend, device, params, xtr[lv][0], ytr, games_tr, xte[lv], n_classes, reps=(reps_by_level or {}).get(lv, reps), seed=seed, y_eval=yte)
            proba = fit.proba
            if calibrate and ("pitcher" in oof_rows or "batter" in oof_rows):
                by = "pitcher" if "pitcher" in oof_rows else "batter"
                from .gbm import apply_isotonic, fit_isotonic

                cal = fit_isotonic(oof[(by, lv)], ytr[oof_rows[by]])
                raw_loss = float(md.row_logloss(yte, proba).mean()); proba = apply_isotonic(cal, proba)
                out["fit"].append({"mode": "season_calibrated", "level": lv, "n_train": len(ytr), "n_eval": len(yte), "logloss": float(md.row_logloss(yte, proba).mean()),
                                   "raw_logloss": raw_loss, "brier": float(md.multiclass_brier_rows(yte, proba).mean()), "fit_seconds": 0.0, "n_iter": fit.n_iter, "backend": backend})
            probas_te[lv] = proba; losses_te[lv] = md.row_logloss(yte, proba)
            out["fit"].append({"mode": "season", "level": lv, "n_train": len(ytr), "n_eval": len(yte), "logloss": float(losses_te[lv].mean()),
                               "brier": float(md.multiclass_brier_rows(yte, proba).mean()), "prior_logloss": float(prior_te.mean()), "fit_seconds": fit.seconds,
                               "n_iter": fit.n_iter, "member_logloss_sd": float(np.std(fit.member_loss)) if len(fit.member_loss) > 1 else 0.0, "backend": backend})
            for r in calibration_rows(yte, proba, classes, 10):
                out["calibration"].append({"mode": "season", "level": lv, **r})
            out["models"][lv] = fit.members
        for lv in levels:
            if lv == "L0":
                continue
            out["gain"].append({"mode": "season", "comparison": f"L0→{lv}", "n_eval": len(yte), **level_gain(losses_te["L0"], losses_te[lv], games_te, reps=bootstrap_reps, seed=seed, confidence=confidence)})
            if target == "outcome" and "whiff" in classes:
                for by in ("pitcher", "batter"):
                    if lv in (("L1", "L3") if by == "pitcher" else ("L2", "L3")):
                        c = type_contrast_gain(np.asarray(cte[by]), np.asarray(cte["pitch_type"]), yte, classes, probas_te["L0"], probas_te[lv],
                                               min_cell_swings=min_cell_swings, reps=bootstrap_reps, seed=seed, confidence=confidence)
                        out["contrast"].append({"mode": "season", "side": by, "comparison": f"L0→{lv}", **c})
                        # A pitcher or batter seen in training can be recognised by his descriptors, so the model may simply remember his own
                        # type effects; only the players NEW in the held-out season show whether the descriptors generalise.
                        fresh = ~np.isin(np.asarray(cte[by]), np.asarray(ctr[by]))
                        if fresh.sum() > 0:
                            c2 = type_contrast_gain(np.asarray(cte[by])[fresh], np.asarray(cte["pitch_type"])[fresh], yte[fresh], classes, probas_te["L0"][fresh],
                                                    probas_te[lv][fresh], min_cell_swings=min_cell_swings, reps=bootstrap_reps, seed=seed, confidence=confidence)
                            out["contrast"].append({"mode": "season_unseen", "side": by, "comparison": f"L0→{lv}", **c2})
    # ---- without a held-out season there is no season fit: the model that answers questions is fitted on all the training rows
    if cols_test is None and fit_final:
        for lv in levels:
            fit = fit_predict_level(backend, device, params, xtr[lv][0], ytr, games_tr, xtr[lv][0][:2], n_classes, reps=(reps_by_level or {}).get(lv, reps), seed=seed)
            out["models"][lv] = fit.members
    return out
