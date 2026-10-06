from __future__ import annotations

import math
from collections import defaultdict
from typing import Any, Iterable, Sequence

from .stats import z_value


def _cells(rows: Iterable[dict[str, Any]], group_fields: Sequence[str], strata_fields: Sequence[str]):
    cells: dict[tuple, dict[tuple, tuple[int, float, float]]] = defaultdict(dict)
    for row in rows:
        key = tuple(row[f] for f in group_fields) + (row["pitch_type"],)
        stratum = tuple(row[f] for f in strata_fields)
        cells[key][(stratum, int(row["k"]))] = (int(row["n"] or 0), float(row["sx"] or 0.0), float(row["sxx"] or 0.0))
    return cells


def _sort_key(item):
    return tuple(str(x) for x in item[0])


def same_point_curve(
    rows: Iterable[dict[str, Any]], *, group_fields: Sequence[str], strata_fields: Sequence[str], kmax: int,
    min_n: int = 30, confidence: float = 0.95,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Naive curve and same-point comparison from streak cell rows.

    ``rows`` have group fields, ``pitch_type``, strata fields, ``k`` (1..kmax, kmax pools "kmax or more"), and the sums
    ``n`` (eligible pitches), ``sx`` (sum of x), ``sxx`` (sum of x squared).

    naive: pooled mean of x at each k (what a plain "by streak length" table shows; confounded by survival).
    same-point: at every stratum that has both k and k=1 with n >= min_n, take mean_k - mean_1; combine the strata
    with weights n_k * n_1 / (n_k + n_1) (inverse-variance up to a constant for similar variances).
    ``coverage`` = share of the k pitches that sit in a usable stratum. No usable stratum -> estimate None.
    """

    z = z_value(confidence)
    naive: list[dict[str, Any]] = []
    same: list[dict[str, Any]] = []
    for key, table in sorted(_cells(rows, group_fields, strata_fields).items(), key=_sort_key):
        label = dict(zip(tuple(group_fields) + ("pitch_type",), key))
        totals = {k: [0, 0.0, 0.0] for k in range(1, kmax + 1)}
        for (_, k), (n, sx, sxx) in table.items():
            if k in totals:
                totals[k][0] += n; totals[k][1] += sx; totals[k][2] += sxx
        for k in range(1, kmax + 1):
            n, sx, sxx = totals[k]
            if n:
                mean = sx / n
                se = math.sqrt(max(sxx / n - mean * mean, 0.0) / n)
                naive.append({**label, "k": k, "n": n, "mean": mean, "se": se, "lo": mean - z * se, "hi": mean + z * se})
        strata = sorted({s for (s, _) in table}, key=lambda s: tuple(str(x) for x in s))
        for k in range(2, kmax + 1):
            num = den = varnum = 0.0
            used = used_n = 0
            for stratum in strata:
                a = table.get((stratum, k)); b = table.get((stratum, 1))
                if not a or not b or a[0] < min_n or b[0] < min_n:
                    continue
                mk, m1 = a[1] / a[0], b[1] / b[0]
                vk = max(a[2] / a[0] - mk * mk, 0.0); v1 = max(b[2] / b[0] - m1 * m1, 0.0)
                w = a[0] * b[0] / (a[0] + b[0])
                num += w * (mk - m1); den += w; varnum += w * w * (vk / a[0] + v1 / b[0])
                used += 1; used_n += a[0]
            total_k = totals[k][0]
            if den > 0:
                est = num / den; se = math.sqrt(varnum) / den
                same.append({**label, "k": k, "estimate": est, "se": se, "lo": est - z * se, "hi": est + z * se,
                             "n_strata": used, "n_k_used": used_n, "n_k_total": total_k, "coverage": used_n / total_k if total_k else None})
            else:
                same.append({**label, "k": k, "estimate": None, "se": None, "lo": None, "hi": None,
                             "n_strata": 0, "n_k_used": 0, "n_k_total": total_k, "coverage": 0.0 if total_k else None})
    return naive, same


def cluster_bootstrap_same_point(
    rows: Iterable[dict[str, Any]], *, group_fields: Sequence[str], strata_fields: Sequence[str], kmax: int,
    min_n: int = 30, reps: int = 200, seed: int = 0, confidence: float = 0.95,
) -> list[dict[str, Any]]:
    """Cluster (Poisson) bootstrap of the same-point estimate.

    ``rows`` are like those of ``same_point_curve`` plus a ``cluster`` field (e.g. pitcher id); each row is the sums for
    one (group, pitch_type, stratum, k, cluster). Every replicate gives each cluster a Poisson(1) weight.
    Returns rows (group fields, pitch_type, k, boot_se, boot_lo, boot_hi, boot_reps_used).
    """

    import numpy as np

    rng = np.random.default_rng(seed)
    z_lo, z_hi = (1 - confidence) / 2 * 100, (1 + confidence) / 2 * 100
    grouped: dict[tuple, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[tuple(row[f] for f in group_fields) + (row["pitch_type"],)].append(row)
    out: list[dict[str, Any]] = []
    for key, items in sorted(grouped.items(), key=_sort_key):
        label = dict(zip(tuple(group_fields) + ("pitch_type",), key))
        strata = {s: i for i, s in enumerate(sorted({tuple(r[f] for f in strata_fields) for r in items}, key=lambda s: tuple(str(x) for x in s)))}
        clusters = {c: i for i, c in enumerate(sorted({r["cluster"] for r in items}, key=str))}
        cell = np.array([strata[tuple(r[f] for f in strata_fields)] * kmax + (int(r["k"]) - 1) for r in items])
        cl = np.array([clusters[r["cluster"]] for r in items])
        n = np.array([float(r["n"] or 0) for r in items]); sx = np.array([float(r["sx"] or 0.0) for r in items])
        size = len(strata) * kmax
        draws = {k: [] for k in range(2, kmax + 1)}
        for _ in range(reps):
            w = rng.poisson(1.0, size=len(clusters))[cl]
            big_n = np.bincount(cell, weights=w * n, minlength=size).reshape(-1, kmax)
            big_x = np.bincount(cell, weights=w * sx, minlength=size).reshape(-1, kmax)
            with np.errstate(divide="ignore", invalid="ignore"):
                mean = np.where(big_n > 0, big_x / big_n, np.nan)
            for k in range(2, kmax + 1):
                nk, n1 = big_n[:, k - 1], big_n[:, 0]
                ok = (nk >= min_n) & (n1 >= min_n)
                if not ok.any():
                    continue
                wt = nk[ok] * n1[ok] / (nk[ok] + n1[ok])
                draws[k].append(float(np.sum(wt * (mean[ok, k - 1] - mean[ok, 0])) / np.sum(wt)))
        for k in range(2, kmax + 1):
            d = np.array(draws[k])
            if d.size >= 2:
                out.append({**label, "k": k, "boot_se": float(d.std(ddof=1)), "boot_lo": float(np.percentile(d, z_lo)),
                            "boot_hi": float(np.percentile(d, z_hi)), "boot_reps_used": int(d.size)})
            else:
                out.append({**label, "k": k, "boot_se": None, "boot_lo": None, "boot_hi": None, "boot_reps_used": int(d.size)})
    return out
