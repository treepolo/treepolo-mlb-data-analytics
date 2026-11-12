"""Outcome models: one interface for every model that predicts the result of a pitch (see ``base.OutcomeModel``) and the bias controls they share (``controls``).

Importing this package is cheap: the numerical modules load when a model is first asked for (the web app imports the research methods at start-up)."""
from __future__ import annotations

from ..config_schema import ConfigError
from . import controls

_MODELS: dict[str, type] = {}
_UNAVAILABLE: dict[str, str] = {}


def register(cls):
    _MODELS[cls.name] = cls
    return cls


def _load() -> None:
    if _MODELS:
        return
    from .gbm_model import GbmModel
    from .logistic import LogisticModel

    register(LogisticModel); register(GbmModel)
    for module in ("mixed", "nn_embed", "sequence"):         # need PyTorch; they register themselves when it is installed
        try:
            __import__(f"{__name__}.{module}")
        except ImportError as exc:
            _UNAVAILABLE[module] = str(exc)
        except Exception as exc:                              # a broken optional model must not take the others down
            _UNAVAILABLE[module] = repr(exc)


def model_kinds() -> tuple[str, ...]:
    """The models that can be used on this machine."""

    _load()
    return tuple(_MODELS)


def get_model_class(name: str):
    _load()
    if name in _MODELS:
        return _MODELS[name]
    if name in controls.MODEL_KINDS:
        raise ConfigError(f"model {name!r} needs PyTorch, which is not installed here / 模型 {name!r} 需要安裝 PyTorch")
    raise ConfigError(f"unknown model {name!r}; choose from {list(controls.MODEL_KINDS)} / 未知的模型 {name!r}")


def new_model(name: str):
    return get_model_class(name)()


def __getattr__(name: str):
    if name in ("OutcomeModel", "fill_defaults"):
        from . import base

        return getattr(base, name)
    raise AttributeError(name)


__all__ = ["controls", "get_model_class", "model_kinds", "new_model", "register", "OutcomeModel", "fill_defaults"]
