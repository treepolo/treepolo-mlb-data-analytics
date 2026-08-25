from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from .web_analysis_advanced import AdvancedModesMixin
from .web_analysis_common import BaseAnalysisMixin, RequestError, _PROGRESS, _jsonable
from .web_analysis_modes import CoreModesMixin
from .web_analysis_stage4 import Stage4ModesMixin


class AnalysisFacade(BaseAnalysisMixin, CoreModesMixin, AdvancedModesMixin, Stage4ModesMixin):
    def __init__(
        self,
        database_path: Path,
        analytics_database_path: Path | None = None,
        *,
        backend: str = "sqlite",
    ):
        self.database_path = Path(database_path)
        self.analytics_database_path = Path(analytics_database_path) if analytics_database_path is not None else None
        self.analysis_backend = backend

    def analyze(
        self,
        payload: dict[str, Any],
        progress: Callable[[str, float | None, str | None], None] | None = None,
    ) -> dict[str, Any]:
        if not self.schema():
            raise RequestError("Pitch data is not initialized yet")
        mode = str(payload.get("mode", "basic"))
        handlers = {
            "basic": self._basic,
            "sequence_pattern": self._sequence_pattern,
            "follow_event": self._follow_event,
            "arsenal": self._arsenal,
            "pitch_role": self._pitch_role,
            "temporal": self._temporal,
            "percentile": self._percentile,
            "cross_level": self._cross_level,
            "arsenal_change": self._arsenal_change,
            "workflow": self._workflow,
            "clustering": self._clustering,
            "regression": self._regression,
            "bootstrap": self._bootstrap,
        }
        token = _PROGRESS.set(progress)
        try:
            if progress is not None:
                progress("building_analysis", 0.5, "Building typed analysis plan")
            return handlers[mode](payload)
        except KeyError as exc:
            raise RequestError(f"Unsupported analysis mode: {mode}") from exc
        finally:
            _PROGRESS.reset(token)


__all__ = ["AnalysisFacade", "RequestError", "_jsonable"]
