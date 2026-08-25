from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .compiler import CompiledQuery, SQLCompiler
from .model import Grain, Node, output_grain


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


class ExecutionPlanner:
    """Plan relational analysis nodes for SQL; future compute nodes can route elsewhere."""

    def __init__(self, compiler: SQLCompiler | None = None):
        self.compiler = compiler or SQLCompiler()

    def plan(self, node: Node) -> ExecutionPlan:
        return ExecutionPlan("sqlite", self.compiler.compile(node), output_grain(node))


class SQLiteExecutor:
    def __init__(self, path: Path):
        self.path = Path(path)

    def execute(self, plan: ExecutionPlan) -> AnalysisResult:
        if plan.backend != "sqlite":
            raise ValueError(f"unsupported backend for SQLiteExecutor: {plan.backend}")
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        _register_statistical_aggregates(conn)
        try:
            cursor = conn.execute(plan.query.sql, plan.query.params)
            raw_columns = tuple(item[0] for item in (cursor.description or ()))
            columns = tuple(name for name in raw_columns if not name.startswith("__ta_"))
            rows = tuple({name: row[name] for name in columns} for row in cursor.fetchall())
            return AnalysisResult(columns, rows, plan.grain)
        finally:
            conn.close()


class AnalysisEngine:
    def __init__(self, database_path: Path, planner: ExecutionPlanner | None = None):
        self.planner = planner or ExecutionPlanner()
        self.executor = SQLiteExecutor(database_path)

    def explain(self, node: Node) -> ExecutionPlan:
        return self.planner.plan(node)

    def execute(self, node: Node) -> AnalysisResult:
        return self.executor.execute(self.planner.plan(node))
