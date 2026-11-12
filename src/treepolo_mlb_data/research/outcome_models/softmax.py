"""Multinomial logistic regression on a sparse design with a separate penalty per column, per-row weights and per-row offsets.

``scikit-learn`` has no offsets and one penalty for every column; the bias controls need both (an effect is penalised on its own scale; the trees hand their margin to the
effects as an offset when the effects are re-estimated). The objective is ``sum_i w_i * NLL_i + 0.5 * sum_j lambda_j * ||W_j||^2`` divided by ``sum_i w_i``; the intercepts
are not penalised. L-BFGS from scipy does the work.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class SoftmaxFit:
    coef: np.ndarray                 # [p, K]
    intercept: np.ndarray            # [K]
    n_iter: int
    converged: bool
    hit_cap: bool
    seconds: float
    trace: list[tuple[int, float]] = field(default_factory=list)        # (iteration, objective per unit weight) every ``trace_every`` iterations


def softmax_rows(z: np.ndarray) -> np.ndarray:
    z = z - z.max(axis=1, keepdims=True)
    e = np.exp(z)
    return e / e.sum(axis=1, keepdims=True)


def log_softmax_rows(z: np.ndarray) -> np.ndarray:
    m = z.max(axis=1, keepdims=True)
    return z - (m + np.log(np.exp(z - m).sum(axis=1, keepdims=True)))


def fit_softmax(x, y: np.ndarray, n_classes: int, penalty: np.ndarray, *, weights: np.ndarray | None = None, offset: np.ndarray | None = None, max_iter: int = 200,
                init: SoftmaxFit | None = None, trace_every: int = 10, tol: float = 1e-9) -> SoftmaxFit:
    """Fit ``coef`` and ``intercept``. ``x`` is a scipy sparse matrix (or an array), ``y`` the class index of every row, ``penalty`` one value per column."""

    import time

    from scipy import sparse
    from scipy.optimize import minimize

    started = time.monotonic()
    x = x.tocsr() if sparse.issparse(x) else sparse.csr_matrix(x)
    n, p = x.shape
    xt = x.T.tocsr()
    y = np.asarray(y, dtype=np.int64)
    w = np.ones(n) if weights is None else np.asarray(weights, dtype=np.float64)
    wsum = float(w.sum())
    off = None if offset is None else np.asarray(offset, dtype=np.float64)
    lam = np.asarray(penalty, dtype=np.float64)
    onehot_rows = np.arange(n)
    trace: list[tuple[int, float]] = []
    state = {"iter": 0, "last": None}

    def unpack(v):
        return v[:p * n_classes].reshape(p, n_classes), v[p * n_classes:]

    def objective(v):
        coef, b = unpack(v)
        z = x @ coef + b
        if off is not None:
            z = z + off
        lsm = log_softmax_rows(z)
        nll = -lsm[onehot_rows, y]
        loss = float((w * nll).sum() + 0.5 * (lam[:, None] * coef * coef).sum()) / wsum
        r = np.exp(lsm)
        r[onehot_rows, y] -= 1.0
        r *= w[:, None]
        g_coef = (xt @ r + lam[:, None] * coef) / wsum
        g_b = r.sum(axis=0) / wsum
        state["last"] = loss
        return loss, np.concatenate([g_coef.ravel(), g_b])

    def callback(_v):
        state["iter"] += 1
        if trace_every and state["iter"] % trace_every == 0 and state["last"] is not None:
            trace.append((state["iter"], float(state["last"])))

    if init is not None and init.coef.shape == (p, n_classes):
        v0 = np.concatenate([init.coef.ravel(), init.intercept])
    else:
        prior = np.bincount(y, weights=w, minlength=n_classes) / max(wsum, 1e-12)
        v0 = np.concatenate([np.zeros(p * n_classes), np.log(np.clip(prior, 1e-9, 1.0))])
    res = minimize(objective, v0, jac=True, method="L-BFGS-B", callback=callback, options={"maxiter": int(max_iter), "maxcor": 20, "ftol": tol, "gtol": 1e-7})
    coef, b = unpack(res.x)
    hit_cap = (not res.success) and int(res.nit) >= int(max_iter)
    return SoftmaxFit(coef.copy(), b.copy(), int(res.nit), bool(res.success), bool(hit_cap), time.monotonic() - started, trace)
