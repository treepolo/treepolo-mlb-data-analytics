"""Letting the data choose how strongly each group of player effects is shrunk (empirical Bayes), and what one pitch is worth as information.

The effects of a group have a normal prior with a variance of its own for EVERY outcome class (a game's form moves whiffs, balls and home runs, not the others, so one variance for all
classes would shrink the real ones too much); the penalty used by the solver is its precision ``1 / var`` in logit units. The variance is estimated by the EM update for random
effects: ``var_k = mean over units(effect_k^2 + posterior variance of the effect_k)``, where the posterior variance is ``1 / (information + 1 / var_k)`` and the information of a unit
for class k is its number of pitches times ``p_k * (1 - p_k)``. The solve and the update alternate (``em_rounds`` times, each solve warm-started from the last), starting from a weak
penalty so that the effects are free to be large if the data say so. A group that does not vary ends up with a large penalty; a group that varies keeps a small one.
"""
from __future__ import annotations

from typing import Any, Mapping

import numpy as np

from . import controls as ctl
from .softmax import SoftmaxFit, fit_softmax

START_PENALTY = 1.0           # the prior variance the estimate starts from: 1 logit squared
VAR_MIN, VAR_MAX = 1e-4, 100.0


def class_info(class_shares) -> np.ndarray:
    """What one pitch tells about the logit of each outcome class: p * (1 - p). Small for every class (at most 0.25)."""

    p = np.asarray(class_shares, dtype=float)
    return p * (1.0 - p)


def info_per_pitch(class_shares) -> float:
    """The mean over the outcome classes of ``class_info``: about 0.08 for the eight pitch results."""

    p = np.asarray(class_shares, dtype=float)
    return float(np.mean(class_info(p))) if len(p) else 0.1


def fit_with_estimated_penalties(x, y: np.ndarray, n_classes: int, penalty: np.ndarray, *, n_dense: int, spans: Mapping[str, tuple[int, int]], within: Mapping[str, bool],
                                 col_counts: np.ndarray, per_pitch, weights=None, offset=None, max_iter: int = 1000, em_rounds: int = 8, init: SoftmaxFit | None = None,
                                 progress=None) -> dict[str, Any]:
    """Solve, estimate every (not 'within') group's variance per outcome class, repeat, then solve once more with the final penalties.

    ``penalty``: one value per column of ``x`` (ordinary columns first, then the effect groups; ``spans`` give each group's (start, size) inside the effect block). ``col_counts``: the
    number of pitches of every effect column. ``per_pitch``: the information of one pitch per class (an array of ``n_classes``) or a single number. Returns the final fit, the
    precision chosen for every group and class (``penalties``: group -> array of ``n_classes``), the seconds, the iterations and whether the last solve hit the cap."""

    v = np.broadcast_to(np.asarray(per_pitch, dtype=float), (n_classes,)).copy()
    pen = np.tile(np.asarray(penalty, dtype=np.float64)[:, None], (1, n_classes))
    chosen: dict[str, np.ndarray] = {}
    for g, (start, size) in spans.items():
        if not within[g]:
            pen[n_dense + start: n_dense + start + size] = START_PENALTY
            chosen[g] = np.full(n_classes, START_PENALTY)
    fit, seconds, n_iter = init, 0.0, 0
    for rnd in range(max(int(em_rounds), 1)):
        fit = fit_softmax(x, y, n_classes, pen, weights=weights, offset=offset, max_iter=max_iter, init=fit)
        seconds += fit.seconds; n_iter += fit.n_iter
        worst = 0.0
        for g, (start, size) in spans.items():
            if within[g] or size == 0:
                continue
            sl = slice(n_dense + start, n_dense + start + size)
            block = fit.coef[sl]                                                       # [units, classes]
            cur = pen[n_dense + start]                                                 # [classes]
            posterior = 1.0 / (col_counts[start:start + size, None] * v[None, :] + cur[None, :])
            var = np.clip(np.mean(block * block + posterior, axis=0), VAR_MIN, VAR_MAX)          # one variance per outcome class
            new = 1.0 / var
            worst = max(worst, float(np.max(np.abs(np.log(new / cur)))))
            pen[sl] = new[None, :]
            chosen[g] = new
        if progress:
            progress("outcome_model", (rnd + 1) / max(int(em_rounds), 1), f"shrinkage round {rnd + 1}")
        if worst < 0.05:                                                              # the penalties stopped moving
            break
    fit = fit_softmax(x, y, n_classes, pen, weights=weights, offset=offset, max_iter=max_iter, init=fit)         # coefficients that belong to the final penalties
    seconds += fit.seconds; n_iter += fit.n_iter
    return {"fit": fit, "penalties": chosen, "seconds": seconds, "n_iter": n_iter, "hit_cap": bool(fit.hit_cap)}


def estimated_penalties_for(model_groups: Mapping[str, bool], chosen: Mapping[str, Any]) -> dict[str, Any]:
    """The precision of every effect group of a model: the estimate (an array over the outcome classes), or the tiny 'within' value."""

    return {g: (ctl.WITHIN_PENALTY if w else np.asarray(chosen.get(g, START_PENALTY), dtype=float)) for g, w in model_groups.items()}
