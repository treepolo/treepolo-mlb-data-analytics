from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .stats import z_value

MISSING = "__missing__"
EPS = 1e-15
# Default class scheme: 8 classes. Categories that are not listed (bunt, unclassified) are dropped and counted.
DEFAULT_CLASS_MERGE: dict[str, str] = {
    "ball": "ball", "called_strike": "called_strike", "whiff": "whiff", "foul_tip": "foul_tip", "foul": "foul",
    "hit_by_pitch": "hit_by_pitch", "in_play_home_run": "in_play_home_run", "in_play_triple": "in_play_non_hr",
    "in_play_double": "in_play_non_hr", "in_play_single": "in_play_non_hr", "in_play_out": "in_play_non_hr",
    "in_play_error_other": "in_play_non_hr",
}
NUMERIC_FEATURES = (
    "plate_x", "plate_z", "release_speed", "pfx_x", "pfx_z", "release_spin_rate", "release_extension", "outs_when_up", "balls", "strikes",
    "pitch_index_in_pa", "speed_diff_prev1", "dx_prev1", "dz_prev1",
)
CATEGORICAL_FEATURES = (
    "pitch_type", "zone", "count_state", "bases", "prev1_pitch_type", "prev2_pitch_type", "prev3_pitch_type", "prev1_description",
    "streak_cap", "p_throws", "stand",
)
# Features that are derived in Python from raw columns of the pitch table.
DERIVED_SOURCES = {"count_state": ("balls", "strikes"), "streak_cap": ("streak_pos",)}
MODEL_NAMES = ("logistic", "hgb")


def source_columns(features: Sequence[str]) -> list[str]:
    """Pitch-table columns the AST query must return for a feature list."""

    out: list[str] = []
    for name in features:
        for column in DERIVED_SOURCES.get(name, (name,)):
            if column not in out:
                out.append(column)
    return out


def add_derived(rows: Sequence[dict[str, Any]], streak_cap: int) -> None:
    """In place: count_state ('1-2') and streak_cap (streak position capped at ``streak_cap``)."""

    for row in rows:
        row["count_state"] = f'{row["balls"]}-{row["strikes"]}' if "balls" in row else None
        sp = row.get("streak_pos")
        row["streak_cap"] = None if sp is None else min(int(sp), streak_cap)


@dataclass
class Design:
    """Column layout learned on the TRAINING rows only (standardization, category levels, missing indicators)."""

    numeric: list[str]
    categorical: list[str]

    def fit(self, rows: Sequence[dict[str, Any]]) -> "Design":
        import numpy as np

        self.stats: dict[str, tuple[float, float, bool]] = {}
        for name in self.numeric:
            x = self._num(rows, name)
            ok = ~np.isnan(x)
            mean = float(np.nanmean(x)) if ok.any() else 0.0
            std = float(np.nanstd(x)) if ok.any() else 1.0
            self.stats[name] = (mean, std or 1.0, bool((~ok).any()))
        self.levels = {name: sorted({MISSING if r[name] is None else str(r[name]) for r in rows}) for name in self.categorical}
        self.columns = (
            [f"num:{n}" for n in self.numeric] + [f"missing:{n}" for n in self.numeric if self.stats[n][2]]
            + [f"cat:{n}={v}" for n in self.categorical for v in self.levels[n]]
        )
        return self

    @staticmethod
    def _num(rows, name):
        import numpy as np

        return np.array([np.nan if r[name] is None else float(r[name]) for r in rows], dtype=np.float64)

    def transform(self, rows: Sequence[dict[str, Any]]):
        import numpy as np

        columns = []
        for name in self.numeric:
            mean, std, _ = self.stats[name]
            x = self._num(rows, name)
            columns.append(np.where(np.isnan(x), 0.0, (x - mean) / std))
        for name in self.numeric:
            if self.stats[name][2]:
                columns.append(np.isnan(self._num(rows, name)).astype(np.float64))
        for name in self.categorical:
            raw = np.array([MISSING if r[name] is None else str(r[name]) for r in rows], dtype=object)
            for level in self.levels[name]:
                columns.append((raw == level).astype(np.float64))
        return np.column_stack(columns).astype(np.float32) if columns else np.zeros((len(rows), 0), dtype=np.float32)


def make_model(name: str, params: Mapping[str, Any]):
    if name == "logistic":
        from sklearn.linear_model import LogisticRegression

        return LogisticRegression(C=params["C"], max_iter=params["max_iter"])
    if name == "hgb":
        from sklearn.ensemble import HistGradientBoostingClassifier

        return HistGradientBoostingClassifier(
            max_iter=params["hgb_max_iter"], learning_rate=params["hgb_learning_rate"], max_depth=params["hgb_max_depth"],
            min_samples_leaf=params["hgb_min_samples_leaf"], random_state=params["seed"])
    raise ValueError(f"unknown model {name!r}")


def fit_predict(name: str, params: Mapping[str, Any], x_train, y_train, x_test, n_classes: int):
    """Fit one model; return (probabilities [n_test, n_classes] in class-index order, fit seconds, model).

    Classes absent from the training fold get probability 0 (then floored by the loss clip)."""

    import numpy as np

    model = make_model(name, params)
    started = time.monotonic()
    model.fit(x_train, y_train)
    seconds = time.monotonic() - started
    raw = model.predict_proba(x_test)
    proba = np.zeros((x_test.shape[0], n_classes))
    proba[:, [int(c) for c in model.classes_]] = raw
    return proba, seconds, model


def row_logloss(y, proba):
    import numpy as np

    return -np.log(np.clip(proba[np.arange(len(y)), y], EPS, 1.0))


def multiclass_brier_rows(y, proba):
    import numpy as np

    onehot = np.zeros_like(proba)
    onehot[np.arange(len(y)), y] = 1.0
    return np.sum((proba - onehot) ** 2, axis=1)


def paired_game_bootstrap(delta, games, *, reps: int = 200, seed: int = 0, confidence: float = 0.95):
    """Mean of per-row differences with a game-level Poisson bootstrap. Returns (mean, lo, hi, se)."""

    import numpy as np

    delta = np.asarray(delta, dtype=float)
    _, codes = np.unique(np.asarray(games), return_inverse=True)
    g = int(codes.max()) + 1
    sum_d = np.bincount(codes, weights=delta, minlength=g)
    count = np.bincount(codes, minlength=g).astype(float)
    weights = np.random.default_rng(seed).poisson(1.0, size=(reps, g)).astype(float)
    estimates = (weights @ sum_d) / (weights @ count)
    lo, hi = (1 - confidence) / 2 * 100, (1 + confidence) / 2 * 100
    return float(delta.mean()), float(np.percentile(estimates, lo)), float(np.percentile(estimates, hi)), float(estimates.std(ddof=1))


def calibration_rows(y, proba, classes: Sequence[str], bins: int = 10) -> list[dict[str, Any]]:
    """Per class: quantile bins of the predicted probability with the mean prediction and the observed frequency."""

    import numpy as np

    out = []
    for c, name in enumerate(classes):
        p = proba[:, c]; observed = (y == c).astype(float)
        edges = np.unique(np.quantile(p, np.linspace(0, 1, bins + 1)))
        index = np.clip(np.searchsorted(edges, p, side="right") - 1, 0, max(len(edges) - 2, 0)) if len(edges) > 1 else np.zeros(len(p), dtype=int)
        for b in range(max(len(edges) - 1, 1)):
            mask = index == b
            if mask.any():
                out.append({"class": name, "bin": b, "n": int(mask.sum()), "mean_pred": float(p[mask].mean()), "observed": float(observed[mask].mean())})
    return out


def table_consistency(cell_keys, y, proba, classes: Sequence[str], *, min_n: int, confidence: float = 0.95) -> list[dict[str, Any]]:
    """Per class, compare the mean predicted probability with the observed frequency in large cells.

    ``cell_keys``: one hashable key per test row (e.g. (count_state, pitch_type)). z uses the variance sum(p(1-p))/n^2 of a
    sum of independent Bernoulli(p_i). Returns one summary row per class."""

    import numpy as np

    keys = {}
    for i, key in enumerate(cell_keys):
        keys.setdefault(key, []).append(i)
    z_crit = z_value(confidence)
    out = []
    for c, name in enumerate(classes):
        diffs, zs = [], []
        for idx in keys.values():
            if len(idx) < min_n:
                continue
            idx = np.asarray(idx); p = proba[idx, c]; n = len(idx)
            observed = float((y[idx] == c).mean()); predicted = float(p.mean())
            variance = float(np.sum(p * (1 - p))) / (n * n)
            diffs.append(abs(observed - predicted))
            if variance > 0:
                zs.append((observed - predicted) / math.sqrt(variance))
        out.append({"class": name, "cells": len(diffs), "mean_abs_diff": float(np.mean(diffs)) if diffs else None,
                    "max_abs_diff": float(np.max(diffs)) if diffs else None,
                    "share_abs_z_above_critical": float(np.mean([abs(z) > z_crit for z in zs])) if zs else None})
    return out


def value_table(train_classes, train_states, train_values, n_classes: int, min_n: int) -> tuple[dict[tuple[int, int], float], list[float]]:
    """V(class, state) = mean pitch value in the training rows; classes with fewer than ``min_n`` rows in a state fall back to the
    class mean (reported by the caller). Returns (cell means, class means)."""

    import numpy as np

    sums: dict[tuple[int, int], list[float]] = {}
    for c, s, v in zip(train_classes, train_states, train_values):
        if v is None:
            continue
        cell = sums.setdefault((int(c), int(s)), [0, 0.0]); cell[0] += 1; cell[1] += float(v)
    class_sum = np.zeros(n_classes); class_n = np.zeros(n_classes)
    for (c, _), (n, total) in sums.items():
        class_sum[c] += total; class_n[c] += n
    class_mean = [float(class_sum[c] / class_n[c]) if class_n[c] else 0.0 for c in range(n_classes)]
    return {key: total / n for key, (n, total) in sums.items() if n >= min_n}, class_mean


def expected_values(proba, states, table, class_mean) -> tuple[Any, int]:
    """EV per row = sum_c p_c * V(c, state); returns (values, number of (row, class) lookups served by the class-mean fallback)."""

    import numpy as np

    n, k = proba.shape
    v = np.zeros((n, k)); fallback = 0
    for c in range(k):
        for i in range(n):
            cell = table.get((c, int(states[i])))
            if cell is None:
                v[i, c] = class_mean[c]; fallback += 1
            else:
                v[i, c] = cell
    return np.sum(proba * v, axis=1), fallback
