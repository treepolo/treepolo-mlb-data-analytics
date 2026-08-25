from __future__ import annotations

import dataclasses
import sqlite3
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Callable

from .analysis import (
    AnalysisEngine, Binary, Boolean, Column, Filter, InList, IsNull, Literal,
    NamedExpr, OrderKey, PITCH_GRAIN, Project, Sort, Source,
)

_OPS = {"eq": "=", "ne": "!=", "gt": ">", "ge": ">=", "lt": "<", "le": "<="}
_DEFAULT_RESULT_FIELDS = (
    "pitch_uid", "game_date", "game_pk", "at_bat_number", "pitch_number",
    "pitcher", "batter", "pitch_type", "release_speed", "description", "zone",
)
_PROGRESS: ContextVar[Callable[[str, float | None, str | None], None] | None] = ContextVar(
    "treepolo_analysis_progress", default=None
)


class RequestError(ValueError):
    pass


def _jsonable(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        return {key: _jsonable(item) for key, item in dataclasses.asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


class BaseAnalysisMixin:
    database_path: Path
    analytics_database_path: Path | None
    analysis_backend: str

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        return conn

    def schema(self) -> dict[str, str]:
        if not self.database_path.exists():
            return {}
        with self._connect() as conn:
            exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='pitches'").fetchone()
            if not exists:
                return {}
            return {row[1]: (row[2] or "TEXT").upper() for row in conn.execute("PRAGMA table_info(pitches)")}

    def meta(self) -> dict[str, Any]:
        # Startup metadata must stay O(schema), not O(number of pitches). Distinct
        # data-value discovery is intentionally not performed here.
        schema = self.schema()
        return {
            "database": str(self.database_path),
            "analytics_database": str(self.analytics_database_path) if self.analytics_database_path else None,
            "analysis_backend": getattr(self, "analysis_backend", "sqlite"),
            "ready": bool(schema),
            "fields": [{"name": name, "type": sql_type} for name, sql_type in schema.items()],
            "choices": {},
            "capabilities": [
                "basic", "sequence_pattern", "follow_event", "arsenal", "pitch_role",
                "temporal", "percentile", "cross_level", "arsenal_change",
                "workflow", "clustering", "regression", "bootstrap",
            ],
        }

    def _field(self, name: str) -> str:
        if name not in self.schema():
            raise RequestError(f"Unknown data field: {name}")
        return name

    def _parse_value(self, field: str, value: Any) -> Any:
        sql_type = self.schema().get(field, "TEXT")
        if value is None:
            return None
        if sql_type == "INTEGER":
            try:
                return int(value)
            except (TypeError, ValueError) as exc:
                raise RequestError(f"{field} requires an integer value") from exc
        if sql_type == "REAL":
            try:
                return float(value)
            except (TypeError, ValueError) as exc:
                raise RequestError(f"{field} requires a numeric value") from exc
        return str(value)

    def _condition(self, spec: dict[str, Any]):
        field = self._field(str(spec.get("field", "")))
        op = str(spec.get("op", "eq"))
        column = Column(field)
        if op == "is_null":
            return IsNull(column)
        if op == "not_null":
            return IsNull(column, True)
        if op in {"in", "not_in"}:
            raw = spec.get("value", [])
            if isinstance(raw, str):
                raw = [item.strip() for item in raw.split(",") if item.strip()]
            if not isinstance(raw, list):
                raise RequestError("IN comparison requires a list of values")
            return InList(column, tuple(Literal(self._parse_value(field, value)) for value in raw), op == "not_in")
        if op not in _OPS:
            raise RequestError(f"Unsupported comparison: {op}")
        return Binary(column, _OPS[op], Literal(self._parse_value(field, spec.get("value"))))

    def _filter_source(self, filters: list[dict[str, Any]] | None):
        node = Source("pitches", PITCH_GRAIN)
        terms = tuple(self._condition(spec) for spec in (filters or []) if spec.get("field"))
        if len(terms) == 1:
            return Filter(node, terms[0])
        if len(terms) > 1:
            return Filter(node, Boolean("and", terms))
        return node

    def _result_field_names(self, extra: tuple[str, ...] = ()) -> tuple[str, ...]:
        schema = self.schema()
        fields: list[str] = []
        for name in _DEFAULT_RESULT_FIELDS + extra:
            if name in schema or name in extra:
                if name not in fields:
                    fields.append(name)
        if "pitch_uid" in schema and (not fields or fields[0] != "pitch_uid"):
            fields.insert(0, "pitch_uid")
        return tuple(fields)

    def _result_projection(self, source, extra: tuple[str, ...] = ()):
        fields = tuple(NamedExpr(name, Column(name)) for name in self._result_field_names(extra))
        return Project(source, fields, PITCH_GRAIN)

    def _apply_result_sort(self, node, payload: dict[str, Any], allowed_fields: tuple[str, ...] | list[str] | set[str]):
        raw = payload.get("result_sort")
        if raw is None:
            # Backward compatibility for the pre-shared Basic Analysis sorter.
            legacy = payload.get("sort") or {}
            raw = [legacy] if legacy.get("field") else []
        if isinstance(raw, dict):
            raw = [raw]
        if not isinstance(raw, list):
            raise RequestError("Result sort must be a list")
        allowed = set(allowed_fields)
        keys = []
        for spec in raw:
            if not isinstance(spec, dict):
                raise RequestError("Invalid result sort item")
            field = str(spec.get("field", ""))
            if not field:
                continue
            if field not in allowed:
                raise RequestError(f"Result field cannot be sorted here: {field}")
            keys.append(OrderKey(Column(field), bool(spec.get("descending", False))))
        return Sort(node, tuple(keys)) if keys else node

    def _execute(self, node) -> dict[str, Any]:
        result = AnalysisEngine(
            self.database_path,
            analytics_database_path=getattr(self, "analytics_database_path", None),
            backend=getattr(self, "analysis_backend", "sqlite"),
        ).execute(node, _PROGRESS.get())
        return {
            "columns": list(result.columns),
            "rows": [dict(row) for row in result.rows],
            "grain": {"keys": list(result.grain.keys), "label": result.grain.label},
            "row_count": len(result.rows),
            "backend": result.backend,
        }

    def _tie_method(self, payload: dict[str, Any]) -> str:
        method = str(payload.get("tie_method", "dense_rank"))
        if method not in {"dense_rank", "rank", "row_number"}:
            raise RequestError("Unsupported tie handling method")
        return method

    def _entity_fields(self, payload: dict[str, Any]) -> tuple[str, ...]:
        fields = tuple(self._field(str(field)) for field in payload.get("entity_fields", ["pitcher"]) if field)
        if not fields:
            raise RequestError("At least one entity field is required")
        return fields
