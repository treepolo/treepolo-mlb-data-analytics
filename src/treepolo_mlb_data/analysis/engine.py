from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .compiler import CompiledQuery, SQLCompiler
from .model import Grain, Node, output_grain


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
