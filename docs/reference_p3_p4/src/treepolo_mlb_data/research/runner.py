from __future__ import annotations

import sqlite3
from typing import Any

from ..analysis.model import Node
from ..analysis.engine import AnalysisResult
from ..analysis.lint import lint_columns
from .config_schema import ConfigError
from .methods import ResearchContext


def pitch_columns(config) -> frozenset[str]:
    conn = sqlite3.connect(f"file:{config.database_path}?mode=ro", uri=True)
    try:
        return frozenset(row[1] for row in conn.execute("PRAGMA table_info(pitches)"))
    finally:
        conn.close()


def run_checked(ctx: ResearchContext, node: Node) -> AnalysisResult:
    """Execute an analysis tree for a research run, refusing the two silent failure modes of the engine.

    1. SQLite reads an unknown double-quoted column name as a text literal, so a typo gives wrong numbers instead of an error:
       every tree is column-linted first.
    2. When DuckDB fails (or no mirror exists) the engine quietly falls back to SQLite, which is 100x slower on window queries:
       with analysis_backend == "duckdb" a fallback is an error.
    """

    problems = lint_columns(node, pitch_columns(ctx.config))
    if problems:
        raise ConfigError("Analysis tree uses unknown columns / 分析樹使用了不存在的欄位: " + "; ".join(problems[:5]))
    result = ctx.engine().execute(node, progress=ctx.progress)
    if ctx.config.analysis_backend == "duckdb" and result.backend != "duckdb":
        raise RuntimeError(
            "The query ran on SQLite although analysis_backend is 'duckdb' (run `analytics-sync`, or set analysis_backend to 'auto' "
            "to accept the much slower SQLite path) / 查詢退回 SQLite 執行，請先執行 analytics-sync 或把 analysis_backend 設為 auto"
        )
    return result
