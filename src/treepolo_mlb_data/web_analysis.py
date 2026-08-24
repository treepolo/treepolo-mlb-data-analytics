from __future__ import annotations

from pathlib import Path
from typing import Any

from .web_analysis_advanced import AdvancedModesMixin
from .web_analysis_common import BaseAnalysisMixin, RequestError, _jsonable
from .web_analysis_modes import CoreModesMixin


class AnalysisFacade(BaseAnalysisMixin, CoreModesMixin, AdvancedModesMixin):
    def __init__(self, database_path: Path):
        self.database_path = Path(database_path)

    def analyze(self, payload: dict[str, Any]) -> dict[str, Any]:
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
        }
        try:
            return handlers[mode](payload)
        except KeyError as exc:
            raise RequestError(f"Unsupported analysis mode: {mode}") from exc


__all__ = ["AnalysisFacade", "RequestError", "_jsonable"]
