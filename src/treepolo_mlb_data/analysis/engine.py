from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ..duckdb_mirror import DuckDBMirror
from .compiler import CompiledQuery, SQLCompiler
from .model import Grain, Node, output_grain

ProgressCallback = Callable[[str, float | None, str | None], None]


class _StdDevBase:
    ddof = 0

    def __init__(self):
        self.count = 0
        self.mean = 0.0
        self.m2 = 0.0

    def step(self, value):
        if value is None:
            return
        x = float(value)
        self.count += 1
        delta = x - self.mean
        self.mean += delta / self.count
        self.m2 += delta * (x - self.mean)

    def finalize(self):
        if self.count <= self.ddof:
            return None
        variance = self.m2 / (self.count - self.ddof)
        return math.sqrt(max(variance, 0.0))


class _StdDevPop(_StdDevBase):
    ddof = 0


class _StdDevSamp(_StdDevBase):
    ddof = 1


class _Median:
    def __init__(self):
        self.values: list[float] = []

    def step(self, value):
        if value is not None:
            self.values.append(float(value))

    def finalize(self):
        if not self.values:
            return None
        self.values.sort()
        size = len(self.values)
        middle = size // 2
        if size % 2:
            return self.values[middle]
        return (self.values[middle - 1] + self.values[middle]) / 2.0


def _register_statistical_aggregates(conn: sqlite3.Connection) -> None:
    conn.create_aggregate("TA_STDDEV_POP", 1, _StdDevPop)
    conn.create_aggregate("TA_STDDEV_SAMP", 1, _StdDevSamp)
    conn.create_aggregate("TA_MEDIAN", 1, _Median)


def _notify(callback: ProgressCallback | None, stage: str, percentage: float | None, detail: str | None = None) -> None:
    if callback is not None:
        callback(stage, percentage, detail)


@dataclass(frozen=True, slots=True)
class ExecutionPlan:
    backend: str
    query: CompiledQuery
    grain: Grain


@dataclass(frozen=True, slots=True)
class AnalysisResult:
    columns: tuple[str, ...]
    rows: tuple[dict[str, Any], ...]
    grain: Grain
    backend: str = "sqlite"


class ExecutionPlanner:
    """Plan relational analysis nodes for SQL; numerical nodes can route elsewhere later."""

    def __init__(self, compiler: SQLCompiler | None = None):
        self.compiler = compiler or SQLCompiler()

    def plan(self, node: Node) -> ExecutionPlan:
        return ExecutionPlan("sqlite", self.compiler.compile(node), output_grain(node))


class SQLiteExecutor:
    def __init__(self, path: Path):
        self.path = Path(path)

    def execute(self, plan: ExecutionPlan, progress: ProgressCallback | None = None) -> AnalysisResult:
        if plan.backend != "sqlite":
            raise ValueError(f"unsupported backend for SQLiteExecutor: {plan.backend}")
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        _register_statistical_aggregates(conn)
        _notify(progress, "sqlite_query", None, "Running SQLite fallback query")
        if progress is not None:
            conn.set_progress_handler(lambda: (_notify(progress, "sqlite_query", None, "Running SQLite fallback query") or 0), 250_000)
        try:
            cursor = conn.execute(plan.query.sql, plan.query.params)
            raw_columns = tuple(item[0] for item in (cursor.description or ()))
            columns = tuple(name for name in raw_columns if not name.startswith("__ta_"))
            rows = tuple({name: row[name] for name in columns} for row in cursor.fetchall())
            _notify(progress, "formatting", 97.0, "Formatting analysis result")
            return AnalysisResult(columns, rows, plan.grain, "sqlite")
        finally:
            conn.close()


class AnalysisEngine:
    def __init__(
        self,
        database_path: Path,
        planner: ExecutionPlanner | None = None,
        *,
        analytics_database_path: Path | None = None,
        backend: str = "sqlite",
    ):
        self.database_path = Path(database_path)
        self.analytics_database_path = Path(analytics_database_path) if analytics_database_path is not None else None
        self.backend = backend
        self.planner = planner or ExecutionPlanner()
        self.executor = SQLiteExecutor(self.database_path)

    def explain(self, node: Node) -> ExecutionPlan:
        return self.planner.plan(node)

    def execute(self, node: Node, progress: ProgressCallback | None = None) -> AnalysisResult:
        plan = self.planner.plan(node)
        if self.backend in {"duckdb", "auto"} and self.analytics_database_path is not None:
            try:
                _notify(progress, "planning", 1.0, "Preparing analytical execution plan")
                DuckDBMirror(self.database_path, self.analytics_database_path).ensure(progress)
                from .duckdb_executor import DuckDBExecutor

                return DuckDBExecutor(self.analytics_database_path).execute(plan.query, plan.grain, progress)
            except Exception as exc:
                _notify(progress, "sqlite_fallback", None, f"DuckDB unavailable; using SQLite fallback: {exc}")
        return self.executor.execute(plan, progress)
