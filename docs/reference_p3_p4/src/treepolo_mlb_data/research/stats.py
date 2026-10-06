from __future__ import annotations

import math
from statistics import NormalDist


def z_value(confidence: float) -> float:
    """Two-sided normal quantile, e.g. 0.95 -> 1.95996."""

    if not 0.0 < confidence < 1.0:
        raise ValueError("confidence must be between 0 and 1")
    return NormalDist().inv_cdf(0.5 + confidence / 2.0)


def wilson_interval(successes: int, n: int, confidence: float = 0.95) -> tuple[float | None, float | None]:
    """Wilson score interval for a proportion; (None, None) when n is 0."""

    if n <= 0:
        return None, None
    z = z_value(confidence)
    p = successes / n
    z2 = z * z
    centre = (p + z2 / (2 * n)) / (1 + z2 / n)
    half = z * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n)) / (1 + z2 / n)
    return max(0.0, centre - half), min(1.0, centre + half)


def cluster_mean_se(n: float, s: float, ss: float, sn: float, nn: float, g: float) -> float | None:
    """Cluster-robust standard error of a mean (CR1).

    Totals over all clusters: n = observations, s = sum of values; and per-cluster sums combined as
    ss = sum(s_g^2), sn = sum(s_g * n_g), nn = sum(n_g^2); g = number of clusters.
    Returns None when there are fewer than 2 clusters or no observations.
    """

    if n <= 0 or g < 2:
        return None
    mean = s / n
    meat = ss - 2.0 * mean * sn + mean * mean * nn
    return math.sqrt(max(meat, 0.0) * g / (g - 1) / (n * n))


def mean_interval(mean: float | None, se: float | None, confidence: float = 0.95) -> tuple[float | None, float | None]:
    if mean is None or se is None:
        return None, None
    z = z_value(confidence)
    return mean - z * se, mean + z * se


def two_proportion_z(k1: int, n1: int, k2: int, n2: int) -> float | None:
    """z statistic for p1 - p2 with a pooled variance; None when it is undefined."""

    if n1 <= 0 or n2 <= 0:
        return None
    pooled = (k1 + k2) / (n1 + n2)
    var = pooled * (1 - pooled) * (1 / n1 + 1 / n2)
    if var <= 0:
        return None
    return (k1 / n1 - k2 / n2) / math.sqrt(var)
