from __future__ import annotations

from typing import Any, Mapping, Sequence

from .streak import same_point_curve


def placebo_same_point(
    rows: Sequence[dict[str, Any]], *, group_fields: Sequence[str], strata_fields: Sequence[str], pitch_types: Sequence[str],
    kmax: int, shuffles: int, seed: int = 0, min_n: int = 30, observed: Mapping[tuple, float] | None = None,
) -> list[dict[str, Any]]:
    """Same-point estimates after shuffling pitch types inside every plate appearance (the placebo).

    ``rows`` are pitch-level dicts sorted by (game_pk, at_bat_number, pitch_index_in_pa) with ``game_pk``,
    ``at_bat_number``, ``pitch_type``, the group and strata fields, ``e`` (1 if the pitch is eligible for the rate) and
    ``x`` (its value). The outcome stays with its position; only the pitch-type labels move, so any streak effect that
    survives is an artifact of the method. Returns one row per (group, pitch type, k) with the shuffled estimates'
    mean, sd, 2.5/97.5 percentiles, the number of shuffles that produced an estimate, and (when ``observed`` is given) the
    observed estimate with a permutation p-value. Note the placebo keeps each plate appearance's multiset of pitch types, so
    the null it tests is "the ORDER of pitch types inside a plate appearance carries no information"; with a real streak
    effect the shuffled estimates are not centred on zero (see the T1 test), so compare observed with the placebo band.
    """

    import numpy as np

    n = len(rows)
    if n == 0:
        return []
    rng = np.random.default_rng(seed)
    pa_key = np.array([(r["game_pk"], r["at_bat_number"]) for r in rows], dtype=np.int64)
    new_pa = np.ones(n, dtype=bool); new_pa[1:] = np.any(pa_key[1:] != pa_key[:-1], axis=1)
    pa_id = np.cumsum(new_pa) - 1
    types = np.array([r["pitch_type"] for r in rows], dtype=object)
    codes, labels = _codes(types)
    group_cols = [np.array([r[f] for r in rows], dtype=object) for f in group_fields]
    strata_cols = [np.array([r[f] for r in rows], dtype=object) for f in strata_fields]
    e = np.array([r["e"] for r in rows], dtype=np.int64); x = np.array([r["x"] for r in rows], dtype=np.float64)
    wanted = np.isin(np.arange(len(labels)), [i for i, t in enumerate(labels) if not pitch_types or t in pitch_types])
    position = np.arange(n)
    estimates: dict[tuple, list[float]] = {}
    for _ in range(shuffles):
        order = np.lexsort((rng.random(n), pa_id))             # rows sorted by (PA, random key)
        shuffled = np.empty(n, dtype=codes.dtype); shuffled[position] = codes[order]   # blocks line up because both orders keep PAs together
        change = np.ones(n, dtype=bool); change[1:] = new_pa[1:] | (shuffled[1:] != shuffled[:-1])
        start = np.maximum.accumulate(np.where(change, position, 0))
        k = np.minimum(position - start + 1, kmax)
        keep = wanted[shuffled] & (e == 1)
        cells: dict[tuple, list[float]] = {}
        for i in np.nonzero(keep)[0]:
            key = tuple(c[i] for c in group_cols) + (labels[shuffled[i]],) + tuple(c[i] for c in strata_cols) + (int(k[i]),)
            cell = cells.setdefault(key, [0, 0.0, 0.0])
            cell[0] += 1; cell[1] += x[i]; cell[2] += x[i] * x[i]
        cell_rows = []
        for key, (cn, sx, sxx) in cells.items():
            row = dict(zip(tuple(group_fields) + ("pitch_type",) + tuple(strata_fields) + ("k",), key))
            row.update(n=cn, sx=sx, sxx=sxx); cell_rows.append(row)
        _, same = same_point_curve(cell_rows, group_fields=group_fields, strata_fields=strata_fields, kmax=kmax, min_n=min_n)
        for row in same:
            if row["estimate"] is not None:
                estimates.setdefault(tuple(row[f] for f in group_fields) + (row["pitch_type"], row["k"]), []).append(row["estimate"])
    out = []
    observed = observed or {}
    for key, values in sorted(estimates.items(), key=lambda kv: tuple(str(x) for x in kv[0])):
        arr = np.array(values)
        obs = observed.get(key)
        p_two = None
        if obs is not None:  # permutation p-value with the +1 correction; two-sided = twice the smaller tail
            low = (1 + int(np.sum(arr <= obs))) / (arr.size + 1); high = (1 + int(np.sum(arr >= obs))) / (arr.size + 1)
            p_two = min(1.0, 2 * min(low, high))
        out.append({**dict(zip(tuple(group_fields) + ("pitch_type", "k"), key)), "observed": obs, "p_two_sided": p_two, "placebo_mean": float(arr.mean()),
                    "placebo_sd": float(arr.std(ddof=1)) if arr.size > 1 else None,
                    "placebo_lo": float(np.percentile(arr, 2.5)), "placebo_hi": float(np.percentile(arr, 97.5)), "placebo_n": int(arr.size)})
    return out


def _codes(values):
    import numpy as np

    labels = sorted({str(v) for v in values})
    index = {v: i for i, v in enumerate(labels)}
    return np.array([index[str(v)] for v in values], dtype=np.int64), labels
