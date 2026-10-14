"""Boosting backends: one interface for xgboost (optional, GPU when present) and scikit-learn hgb; serialisation, bagging, calibration."""
from __future__ import annotations

import numpy as np
import pytest

from treepolo_mlb_data.research import gbm

HAS_XGB = gbm.xgboost_available()
BACKENDS = ["hgb"] + (["xgboost"] if HAS_XGB else [])


def _data(n=3000, k=3, seed=0):
    rng = np.random.default_rng(seed)
    x = rng.standard_normal((n, 6)).astype(np.float32)
    logits = np.column_stack([x[:, 0], -x[:, 0] + x[:, 1], 0.5 * x[:, 2]])
    p = np.exp(logits) / np.exp(logits).sum(axis=1, keepdims=True)
    y = np.array([rng.choice(k, p=row) for row in p])
    return x, y, p


@pytest.mark.parametrize("backend", BACKENDS)
def test_fit_predict_beats_the_prior_and_serialises(backend):
    x, y, p = _data()
    fit = gbm.fit_gbm(backend, "cpu", dict(max_depth=3, max_iter=60, min_leaf=20), x[:2400], y[:2400], 3, x_valid=x[2400:], y_valid=y[2400:], seed=1)
    proba = fit.predict_proba(x[2400:])
    assert proba.shape == (600, 3) and np.allclose(proba.sum(axis=1), 1.0, atol=1e-5)
    loss = -np.log(np.clip(proba[np.arange(600), y[2400:]], 1e-12, 1)).mean()
    prior = np.bincount(y[:2400]) / 2400
    assert loss < -np.log(prior[y[2400:]]).mean() - 0.05
    again = gbm.FittedGbm.from_bytes(fit.to_bytes())
    assert np.allclose(again.predict_proba(x[2400:]), proba, atol=1e-6)


def test_class_missing_from_training_gets_zero_probability():
    x, y, _ = _data(1500)
    y = np.where(y == 2, 1, y)            # class 2 never occurs
    fit = gbm.fit_gbm("hgb", "cpu", dict(max_depth=2, max_iter=10, min_leaf=20), x, y, 3, seed=0)
    assert np.all(fit.predict_proba(x[:50])[:, 2] == 0.0)


def test_backend_and_device_errors_are_explicit(monkeypatch):
    with pytest.raises(ValueError, match="cannot use a GPU"):
        gbm.resolve_device("hgb", "cuda")
    with pytest.raises(ValueError, match="unknown backend"):
        gbm.resolve_device("lightgbm", "cpu")
    monkeypatch.setattr(gbm, "xgboost_available", lambda: False)
    with pytest.raises(ValueError, match="optional dependency"):
        gbm.resolve_device("xgboost", "auto")        # never a silent fallback to hgb
    assert gbm.resolve_device("hgb", "auto") == "cpu"


@pytest.mark.skipif(not HAS_XGB, reason="xgboost not installed")
def test_xgboost_auto_device_resolves_to_a_working_device():
    assert gbm.resolve_device("xgboost", "auto") in ("cpu", "cuda")
    assert gbm.resolve_device("xgboost", "cpu") == "cpu"


def test_bagging_uses_game_level_poisson_weights_and_members_differ():
    x, y, _ = _data(2400)
    games = np.repeat(np.arange(120), 20)
    members = gbm.bag_fit("hgb", "cpu", dict(max_depth=3, max_iter=15, min_leaf=20), x, y, games, 3, reps=4, seed=3)
    mean, stack = gbm.bagged_proba(members, x[:200])
    assert stack.shape == (4, 200, 3) and np.allclose(mean.sum(axis=1), 1.0, atol=1e-6)
    assert stack.std(axis=0).mean() > 1e-4          # bootstrap members are not identical
    w = gbm.poisson_game_weights(games, np.random.default_rng(0))
    assert len(set(w[:20])) == 1                    # all rows of one game share a weight


def test_isotonic_calibration_repairs_a_distorted_predictor():
    rng = np.random.default_rng(4)
    p = rng.dirichlet([2, 2, 2], size=6000); y = np.array([rng.choice(3, p=row) for row in p])
    distorted = p ** 2.2; distorted /= distorted.sum(axis=1, keepdims=True)
    cal = gbm.fit_isotonic(distorted[:3000], y[:3000])
    fixed = gbm.apply_isotonic(cal, distorted[3000:])
    ll = lambda q: -np.log(np.clip(q[np.arange(3000), y[3000:]], 1e-12, 1)).mean()
    assert ll(fixed) < ll(distorted[3000:]) - 0.01 and np.allclose(fixed.sum(axis=1), 1.0)
