from __future__ import annotations

from typing import Sequence


def codes(values):
    """Integer code per distinct value (values are compared as text, so None and 'None' coincide)."""

    import numpy as np

    _, inverse = np.unique(np.asarray(values, dtype=object).astype(str), return_inverse=True)
    return inverse.reshape(-1)


def dummies(values, drop_first: bool = True):
    import numpy as np

    c = codes(values)
    k = int(c.max()) + 1 if len(c) else 0
    matrix = np.zeros((len(c), k))
    if len(c):
        matrix[np.arange(len(c)), c] = 1
    return matrix[:, 1:] if drop_first else matrix


def demean(a, groups: Sequence, iters: int = 200, tol: float = 1e-9):
    """Remove one or several fixed effects from the columns of ``a`` by alternating projections.

    ``groups`` are integer code arrays. Returns (residualized copy, converged)."""

    import numpy as np

    a = np.array(a, dtype=float, copy=True)
    if not len(groups):
        return a, True
    one_d = a.ndim == 1
    if one_d:
        a = a[:, None]
    sets = [(g, np.bincount(g).astype(float)) for g in groups]
    converged = len(sets) == 1
    for _ in range(iters):
        delta = 0.0
        for g, count in sets:
            for j in range(a.shape[1]):
                mean = np.bincount(g, weights=a[:, j]) / count
                a[:, j] -= mean[g]
                delta = max(delta, float(np.abs(mean).max()))
        if len(sets) == 1:
            break
        if delta < tol:
            converged = True
            break
    return (a[:, 0] if one_d else a), converged


def fe_ols(y, X, fe_groups: Sequence, cluster):
    """Frisch-Waugh-Lovell: residualize y and X on the fixed effects, OLS, CR1 cluster-robust standard errors.

    Returns a dict: beta, se (None where undefined), informative (rows with within-group variation), clusters, converged."""

    import numpy as np

    yt, c1 = demean(y, fe_groups)
    Xt, c2 = demean(X, fe_groups)
    informative = int((np.abs(Xt).sum(axis=1) > 1e-12).sum())
    beta, *_ = np.linalg.lstsq(Xt, yt, rcond=None)
    resid = yt - Xt @ beta
    bread = np.linalg.pinv(Xt.T @ Xt)
    cl = codes(cluster)
    g = int(cl.max()) + 1
    scores = np.zeros((g, Xt.shape[1]))
    for j in range(Xt.shape[1]):
        scores[:, j] = np.bincount(cl, weights=Xt[:, j] * resid, minlength=g)
    if g < 2:
        se = [None] * Xt.shape[1]
    else:
        variance = np.diag(bread @ (scores.T @ scores) @ bread) * g / (g - 1)
        se = [float(np.sqrt(v)) if np.isfinite(v) and v >= 0 else None for v in variance]
    return {"beta": beta, "se": se, "informative": informative, "clusters": g, "converged": bool(c1 and c2)}
