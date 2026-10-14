"""Gradient-boosting backends for the league model: XGBoost (GPU when available) or scikit-learn HistGradientBoosting (CPU).

One interface, two engines. ``xgboost`` is an OPTIONAL dependency (``pip install treepolo-mlb-data-analytics[gpu]``); asking for it when it is
not installed is an error with a clear message, never a silent fallback to another engine.
"""
from __future__ import annotations

import io
import pickle
import time
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

import numpy as np

BACKENDS = ("xgboost", "hgb")
DEVICES = ("auto", "cpu", "cuda")
EPS = 1e-15
DEFAULT_PARAMS: dict[str, Any] = dict(max_depth=6, min_leaf=200, learning_rate=0.1, l2=1.0, max_iter=300, early_stopping_rounds=20)


def xgboost_available() -> bool:
    try:
        import xgboost  # noqa: F401
    except Exception:
        return False
    return True


_CUDA_CACHE: dict[str, bool] = {}


def cuda_works() -> bool:
    """True when XGBoost can train on a GPU here (checked once with a tiny model)."""

    if "ok" in _CUDA_CACHE:
        return _CUDA_CACHE["ok"]
    ok = False
    if xgboost_available():
        try:
            import xgboost as xgb

            rng = np.random.default_rng(0)
            d = xgb.DMatrix(rng.standard_normal((64, 4)).astype(np.float32), label=rng.integers(0, 2, 64))
            xgb.train({"device": "cuda", "tree_method": "hist", "objective": "binary:logistic"}, d, 2)
            ok = True
        except Exception:
            ok = False
    _CUDA_CACHE["ok"] = ok
    return ok


def resolve_device(backend: str, device: str) -> str:
    if backend not in BACKENDS:
        raise ValueError(f"unknown backend {backend!r}; choose from {list(BACKENDS)}")
    if device not in DEVICES:
        raise ValueError(f"unknown device {device!r}; choose from {list(DEVICES)}")
    if backend == "hgb":
        if device == "cuda":
            raise ValueError("backend 'hgb' (scikit-learn) cannot use a GPU / backend hgb 不能用 GPU")
        return "cpu"
    if not xgboost_available():
        raise ValueError("backend 'xgboost' needs the optional dependency: pip install xgboost (or the [gpu] extra); use backend 'hgb' for a CPU-only environment / "
                         "backend xgboost 需要安裝選用依賴 xgboost；只用 CPU 的環境請改用 backend hgb")
    if device == "auto":
        return "cuda" if cuda_works() else "cpu"
    if device == "cuda" and not cuda_works():
        raise ValueError("device 'cuda' was requested but XGBoost cannot use a GPU here / 要求 cuda 但這裡的 XGBoost 無法使用 GPU")
    return device


@dataclass
class FittedGbm:
    backend: str
    device: str
    model: Any
    n_classes: int
    classes: tuple[int, ...]
    n_iter: int
    seconds: float
    params: dict[str, Any] = field(default_factory=dict)

    def predict_proba(self, x) -> np.ndarray:
        if self.backend == "xgboost":
            import xgboost as xgb

            raw = self.model.predict(xgb.DMatrix(np.asarray(x, dtype=np.float32)), iteration_range=(0, self.n_iter)) if self.n_iter else self.model.predict(xgb.DMatrix(np.asarray(x, dtype=np.float32)))
            proba = raw.reshape(len(x), -1)
            out = np.zeros((len(x), self.n_classes)); out[:, list(self.classes)] = proba
            return out
        raw = self.model.predict_proba(x)
        out = np.zeros((x.shape[0], self.n_classes)); out[:, [int(c) for c in self.model.classes_]] = raw
        return out

    def to_bytes(self) -> bytes:
        buf = io.BytesIO()
        if self.backend == "xgboost":
            payload = {"raw": bytes(self.model.save_raw("ubj")), "n_iter": self.n_iter}
        else:
            payload = {"pickle": pickle.dumps(self.model), "n_iter": self.n_iter}
        pickle.dump({"backend": self.backend, "device": self.device, "n_classes": self.n_classes, "classes": self.classes, "payload": payload, "params": self.params}, buf)
        return buf.getvalue()

    @staticmethod
    def from_bytes(data: bytes) -> "FittedGbm":
        d = pickle.loads(data)
        if d["backend"] == "xgboost":
            import xgboost as xgb

            booster = xgb.Booster(); booster.load_model(bytearray(d["payload"]["raw"]))
            model = booster
        else:
            model = pickle.loads(d["payload"]["pickle"])
        return FittedGbm(d["backend"], d["device"], model, d["n_classes"], tuple(d["classes"]), d["payload"]["n_iter"], 0.0, d["params"])


def fit_gbm(backend: str, device: str, params: Mapping[str, Any], x_train, y_train, n_classes: int, *, sample_weight=None,
            x_valid=None, y_valid=None, seed: int = 0) -> FittedGbm:
    """Fit one multi-class boosted-tree model. ``params``: max_depth, min_leaf, learning_rate, l2, max_iter, early_stopping_rounds.

    With a validation set, xgboost stops early (hgb always runs ``max_iter`` rounds: its internal early stopping uses a random
    split, which made P5's boosting results unstable)."""

    p = {**DEFAULT_PARAMS, **dict(params)}
    device = resolve_device(backend, device)
    y_train = np.asarray(y_train, dtype=np.int64)
    classes = tuple(int(c) for c in np.unique(y_train))
    started = time.monotonic()
    if backend == "hgb":
        from sklearn.ensemble import HistGradientBoostingClassifier

        model = HistGradientBoostingClassifier(max_iter=int(p["max_iter"]), learning_rate=float(p["learning_rate"]), max_depth=int(p["max_depth"]),
                                               min_samples_leaf=int(p["min_leaf"]), l2_regularization=float(p["l2"]), early_stopping=False, random_state=int(seed))
        model.fit(x_train, y_train, sample_weight=sample_weight)
        return FittedGbm("hgb", "cpu", model, n_classes, classes, int(p["max_iter"]), time.monotonic() - started, dict(p))
    import xgboost as xgb

    index = {c: i for i, c in enumerate(classes)}
    y_local = np.array([index[int(v)] for v in y_train])
    dtrain = xgb.DMatrix(np.asarray(x_train, dtype=np.float32), label=y_local, weight=None if sample_weight is None else np.asarray(sample_weight, dtype=np.float32))
    xparams = {"objective": "multi:softprob", "num_class": len(classes), "tree_method": "hist", "device": device, "max_depth": int(p["max_depth"]),
               "eta": float(p["learning_rate"]), "lambda": float(p["l2"]), "min_child_weight": max(float(p["min_leaf"]) * 0.1, 1e-3), "seed": int(seed),
               "eval_metric": "mlogloss", "verbosity": 0}
    evals = []; stop = None
    if x_valid is not None and y_valid is not None and len(y_valid):
        yv = np.array([index.get(int(v), -1) for v in y_valid]); keep = yv >= 0
        evals = [(xgb.DMatrix(np.asarray(x_valid, dtype=np.float32)[keep], label=yv[keep]), "valid")]
        stop = int(p["early_stopping_rounds"])
    booster = xgb.train(xparams, dtrain, num_boost_round=int(p["max_iter"]), evals=evals, early_stopping_rounds=stop, verbose_eval=False)
    n_iter = int(booster.best_iteration + 1) if stop and hasattr(booster, "best_iteration") else int(p["max_iter"])
    return FittedGbm("xgboost", device, booster, n_classes, classes, n_iter, time.monotonic() - started, dict(p))


def poisson_game_weights(games: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Per-row weights from one Poisson(1) draw per game (the bootstrap used everywhere in the research tools)."""

    _, codes = np.unique(np.asarray(games), return_inverse=True)
    draw = rng.poisson(1.0, size=int(codes.max()) + 1).astype(np.float64)
    return draw[codes]


def bag_fit(backend: str, device: str, params: Mapping[str, Any], x_train, y_train, games_train, n_classes: int, *, reps: int, seed: int,
            x_valid=None, y_valid=None) -> list[FittedGbm]:
    """``reps`` boosted models, each on a game-level Poisson bootstrap of the training rows (different seeds)."""

    rng = np.random.default_rng(seed)
    members = []
    for r in range(reps):
        w = poisson_game_weights(games_train, rng)
        members.append(fit_gbm(backend, device, params, x_train, y_train, n_classes, sample_weight=w, x_valid=x_valid, y_valid=y_valid, seed=seed + 1000 * (r + 1)))
    return members


def bagged_proba(members: Sequence[FittedGbm], x) -> tuple[np.ndarray, np.ndarray]:
    """(mean probabilities, per-member probabilities [reps, n, classes])."""

    stack = np.stack([m.predict_proba(x) for m in members])
    return stack.mean(axis=0), stack


# ---------------------------------------------------------------------------------------------- calibration
def fit_isotonic(proba_oof: np.ndarray, y: np.ndarray) -> list[Any]:
    """One isotonic regression per class on out-of-fold predictions (P(class) -> observed frequency)."""

    from sklearn.isotonic import IsotonicRegression

    out = []
    for c in range(proba_oof.shape[1]):
        iso = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip")
        iso.fit(proba_oof[:, c], (np.asarray(y) == c).astype(float))
        out.append(iso)
    return out


def apply_isotonic(calibrators: Sequence[Any], proba: np.ndarray) -> np.ndarray:
    cal = np.column_stack([iso.predict(proba[:, c]) for c, iso in enumerate(calibrators)])
    cal = np.clip(cal, EPS, None)
    return cal / cal.sum(axis=1, keepdims=True)
