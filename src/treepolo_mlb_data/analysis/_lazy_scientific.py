from __future__ import annotations

import importlib
from typing import Any


class _LazyModule:
    """Import a heavy module only when one of its attributes is first used."""

    def __init__(self, module_name: str):
        self._module_name = module_name
        self._module = None

    def _load(self):
        if self._module is None:
            self._module = importlib.import_module(self._module_name)
        return self._module

    def __getattr__(self, name: str) -> Any:
        return getattr(self._load(), name)


np = _LazyModule("numpy")
stats = _LazyModule("scipy.stats")


def _construct(module_name: str, class_name: str, *args: Any, **kwargs: Any):
    cls = getattr(importlib.import_module(module_name), class_name)
    return cls(*args, **kwargs)


def KMeans(*args: Any, **kwargs: Any):
    return _construct("sklearn.cluster", "KMeans", *args, **kwargs)


def LogisticRegression(*args: Any, **kwargs: Any):
    return _construct("sklearn.linear_model", "LogisticRegression", *args, **kwargs)


def GaussianMixture(*args: Any, **kwargs: Any):
    return _construct("sklearn.mixture", "GaussianMixture", *args, **kwargs)


def StandardScaler(*args: Any, **kwargs: Any):
    return _construct("sklearn.preprocessing", "StandardScaler", *args, **kwargs)


def accuracy_score(*args: Any, **kwargs: Any):
    fn = getattr(importlib.import_module("sklearn.metrics"), "accuracy_score")
    return fn(*args, **kwargs)


def log_loss(*args: Any, **kwargs: Any):
    fn = getattr(importlib.import_module("sklearn.metrics"), "log_loss")
    return fn(*args, **kwargs)
