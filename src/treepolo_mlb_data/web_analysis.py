from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from .web_analysis_acceptance import AcceptanceFixesMixin
from .web_analysis_advanced import AdvancedModesMixin
from .web_analysis_common import BaseAnalysisMixin, RequestError, _PROGRESS, _jsonable
from .web_analysis_modes import CoreModesMixin
from .web_analysis_stage4_ext import Stage4ExtendedModesMixin


class AnalysisFacade(
    BaseAnalysisMixin,
    CoreModesMixin,
    AcceptanceFixesMixin,
    AdvancedModesMixin,
    Stage4ExtendedModesMixin,
):
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

    @staticmethod
    def _apply_result_limit(result: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        """Limit rows returned to the UI without changing the underlying computation.

        row_count keeps the full pre-display count when the producer supplied it.
        returned_row_count records how many rows are actually present in the payload.
        """
        raw_limit = payload.get("result_limit")
        if raw_limit in (None, ""):
            return result
        limit = max(1, min(int(raw_limit), 5000))
        limited = dict(result)

        def trim(section: dict[str, Any]) -> dict[str, Any]:
            copy = dict(section)
            rows = copy.get("rows")
            if isinstance(rows, list):
                copy["returned_row_count"] = min(len(rows), limit)
                copy["rows"] = rows[:limit]
                copy["result_limit"] = limit
            return copy

        if isinstance(limited.get("sections"), list):
            limited["sections"] = [
                trim(section) if isinstance(section, dict) else section
                for section in limited["sections"]
            ]
        else:
            limited = trim(limited)
        return limited

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
            "cluster_compare": self._cluster_compare,
        }
        token = _PROGRESS.set(progress)
        try:
            if progress is not None:
                progress("building_analysis", 0.5, "Building typed analysis plan")
            result = handlers[mode](payload)
            return self._apply_result_limit(result, payload)
        except KeyError as exc:
            raise RequestError(f"Unsupported analysis mode: {mode}") from exc
        finally:
            _PROGRESS.reset(token)


__all__ = ["AnalysisFacade", "RequestError", "_jsonable"]
