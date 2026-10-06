from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Callable, ClassVar

from ..analysis import AnalysisEngine, Filter, PITCH_GRAIN, Source
from ..config import AppConfig
from .config_schema import ConfigError, ConfigField
from .scope import scope_filter_expr

ProgressCallback = Callable[[str, float | None, str | None], None]


@dataclass(frozen=True, slots=True)
class ArtifactData:
    """A large per-row output stored beside (not inside) the main result."""

    name: str
    description: str
    columns: tuple[str, ...]
    rows: tuple[dict[str, Any], ...]


@dataclass(frozen=True, slots=True)
class ResearchResult:
    sections: tuple[dict[str, Any], ...]
    extras: dict[str, Any] = field(default_factory=dict)
    artifacts: tuple[ArtifactData, ...] = ()


@dataclass(slots=True)
class ResearchContext:
    config: AppConfig
    facade: Any
    scope: dict[str, Any]
    progress: ProgressCallback | None = None

    def source_node(self):
        """The pitch table restricted to the research scope (unrestricted when no scope)."""

        source = Source("pitches", PITCH_GRAIN)
        expr = scope_filter_expr(self.scope)
        return source if expr is None else Filter(source, expr)

    def engine(self) -> AnalysisEngine:
        return AnalysisEngine(
            self.config.database_path,
            analytics_database_path=self.config.analytics_database_path,
            backend=self.config.analysis_backend,
        )


class ResearchMethod:
    """Base class for a research method. Subclasses declare every assumption as a ConfigField."""

    kind: ClassVar[str] = ""
    version: ClassVar[int] = 1
    label_zh: ClassVar[str] = ""
    label_en: ClassVar[str] = ""
    requires_scope: ClassVar[bool] = False
    fields: ClassVar[tuple[ConfigField, ...]] = ()

    def validate(self, config: dict[str, Any], ctx: ResearchContext) -> None:
        """Cross-field checks. Raise ConfigError on invalid combinations."""

    def run(self, ctx: ResearchContext, config: dict[str, Any]) -> ResearchResult:
        raise NotImplementedError

    def describe(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "version": self.version,
            "label_zh": self.label_zh,
            "label_en": self.label_en,
            "requires_scope": self.requires_scope,
            "fields": [item.to_dict() for item in self.fields],
        }


_REGISTRY: dict[str, ResearchMethod] = {}
_REGISTRY_LOCK = threading.Lock()


def register_method(method: ResearchMethod) -> ResearchMethod:
    if not method.kind:
        raise ValueError("A research method needs a kind")
    with _REGISTRY_LOCK:
        existing = _REGISTRY.get(method.kind)
        if existing is not None and existing is not method:
            raise ValueError(f"Research method already registered: {method.kind}")
        _REGISTRY[method.kind] = method
    return method


def get_method(kind: str) -> ResearchMethod:
    with _REGISTRY_LOCK:
        method = _REGISTRY.get(str(kind))
    if method is None:
        raise ConfigError(f"Unknown research method: {kind} / 未知的研究方法: {kind}")
    return method


def list_methods() -> list[ResearchMethod]:
    with _REGISTRY_LOCK:
        return [_REGISTRY[key] for key in sorted(_REGISTRY)]
