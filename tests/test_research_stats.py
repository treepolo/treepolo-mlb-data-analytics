from __future__ import annotations

import math

import numpy as np
import pytest

from treepolo_mlb_data.research.stats import cluster_mean_se, mean_interval, two_proportion_z, wilson_interval, z_value
from treepolo_mlb_data.research.streak import cluster_bootstrap_same_point, same_point_curve


def test_z_value():
    assert z_value(0.95) == pytest.approx(1.959964, abs=1e-5)
    assert z_value(0.90) == pytest.approx(1.644854, abs=1e-5)
    with pytest.raises(ValueError):
        z_value(1.0)


def test_wilson_interval_known_values():
    lo, hi = wilson_interval(5, 10)
    assert (lo, hi) == (pytest.approx(0.2366, abs=1e-4), pytest.approx(0.7634, abs=1e-4))
    lo, hi = wilson_interval(0, 10)
    assert lo == pytest.approx(0.0, abs=1e-12) and hi == pytest.approx(0.2775, abs=1e-4)
    assert wilson_interval(3, 0) == (None, None)


def test_cluster_mean_se_matches_a_brute_force_computation():
    rng = np.random.default_rng(7)
    sizes = rng.integers(1, 6, size=40)
    values = [rng.normal(0.1, 1.0, size=int(n)) for n in sizes]
    n = float(sum(len(v) for v in values)); s = float(sum(v.sum() for v in values))
    per = [(len(v), float(v.sum())) for v in values]
    ss = sum(sg * sg for _, sg in per); sn = sum(sg * ng for ng, sg in per); nn = sum(ng * ng for ng, _ in per)
    mean = s / n
    brute = math.sqrt(len(per) / (len(per) - 1) * sum((sg - mean * ng) ** 2 for ng, sg in per) / n ** 2)
    assert cluster_mean_se(n, s, ss, sn, nn, len(per)) == pytest.approx(brute)
    assert cluster_mean_se(n, s, ss, sn, nn, 1) is None and cluster_mean_se(0, 0, 0, 0, 0, 5) is None


def test_mean_interval_and_two_proportion_z():
    assert mean_interval(0.5, 0.1) == (pytest.approx(0.5 - 0.195996, abs=1e-5), pytest.approx(0.5 + 0.195996, abs=1e-5))
    assert mean_interval(None, 0.1) == (None, None)
    assert two_proportion_z(30, 100, 20, 100) == pytest.approx(0.1 / math.sqrt(0.25 * 0.75 * 0.02), abs=1e-9)
    assert two_proportion_z(0, 100, 0, 100) is None and two_proportion_z(1, 0, 1, 5) is None


def _cell(stratum, k, n, x):
    return {"pitch_type": "SL", "s": stratum, "k": k, "n": n, "sx": x, "sxx": x}


def test_same_point_known_answer_and_weighting():
    rows = [
        _cell("A", 1, 100, 40), _cell("A", 2, 50, 15),     # 0.40 -> 0.30, weight 100*50/150 = 33.33
        _cell("B", 1, 300, 60), _cell("B", 2, 100, 20),    # 0.20 -> 0.20, weight 75
        _cell("C", 1, 10, 5), _cell("C", 2, 200, 100),     # k=1 has only 10 pitches (< min_n 30): stratum unused
    ]
    naive, same = same_point_curve(rows, group_fields=(), strata_fields=("s",), kmax=2, min_n=30)
    est = same[0]
    assert est["k"] == 2 and est["n_strata"] == 2
    assert est["estimate"] == pytest.approx((-0.10 * 100 / 3) / (100 / 3 + 75))
    assert est["n_k_used"] == 150 and est["n_k_total"] == 350 and est["coverage"] == pytest.approx(150 / 350)
    by_k = {r["k"]: r for r in naive}
    assert by_k[1]["n"] == 410 and by_k[1]["mean"] == pytest.approx(105 / 410)
    assert by_k[2]["mean"] == pytest.approx(135 / 350)           # the naive curve pools every stratum (including C)
    assert est["lo"] < est["estimate"] < est["hi"]


def test_same_point_without_a_comparable_stratum_gives_no_number():
    rows = [_cell("A", 1, 100, 40), _cell("B", 2, 100, 30)]
    _, same = same_point_curve(rows, group_fields=(), strata_fields=("s",), kmax=2)
    assert same[0]["estimate"] is None and same[0]["coverage"] == 0.0 and same[0]["n_strata"] == 0


def test_cluster_bootstrap_agrees_with_the_analytic_se_for_independent_clusters():
    rng = np.random.default_rng(11)
    rows = []
    for stratum in ("A", "B", "C"):
        for cluster in range(150):
            for k, p in ((1, 0.40), (2, 0.34)):
                n = int(rng.integers(1, 6))
                x = int(rng.binomial(n, p))
                rows.append({"pitch_type": "SL", "s": stratum, "k": k, "cluster": cluster, "n": n, "sx": x, "sxx": x})
    flat = {}
    for r in rows:
        key = (r["s"], r["k"])
        acc = flat.setdefault(key, {"pitch_type": "SL", "s": r["s"], "k": r["k"], "n": 0, "sx": 0, "sxx": 0})
        acc["n"] += r["n"]; acc["sx"] += r["sx"]; acc["sxx"] += r["sxx"]
    _, same = same_point_curve(flat.values(), group_fields=(), strata_fields=("s",), kmax=2)
    boot = cluster_bootstrap_same_point(rows, group_fields=(), strata_fields=("s",), kmax=2, reps=300, seed=3)
    ratio = boot[0]["boot_se"] / same[0]["se"]
    assert 0.75 < ratio < 1.33, ratio
    assert boot[0]["boot_reps_used"] == 300
