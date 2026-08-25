from __future__ import annotations

import re
import threading
from pathlib import Path
from typing import Callable

import duckdb

from .compiler import CompiledQuery
from .model import Grain

ProgressCallback = Callable[[str, float | None, str | None], None]

_NULL_SAFE_IS = re.compile(
    r'((?:"[A-Za-z_][A-Za-z0-9_]*"\.)?"[A-Za-z_][A-Za-z0-9_]*")\s+IS\s+((?:"[A-Za-z_][A-Za-z0-9_]*"\.)?"[A-Za-z_][A-Za-z0-9_]*")'
)


def _duckdb_sql(sql: str) -> str:
    sql = sql.replace("TA_MEDIAN(", "MEDIAN(")
    sql = sql.replace("TA_STDDEV_POP(", "STDDEV_POP(")
    sql = sql.replace("TA_STDDEV_SAMP(", "STDDEV_SAMP(")
    return _NULL_SAFE_IS.sub(r"\1 IS NOT DISTINCT FROM \2", sql)


def _notify(callback: ProgressCallback | None, stage: str, percentage: float | None, detail: str | None = None) -> None:
    if callback is not None:
        callback(stage, percentage, detail)


class DuckDBExecutor:
    def __init__(self, path: Path):
        self.path = Path(path)

    def execute(self, query: CompiledQuery, grain: Grain, progress: ProgressCallback | None = None):
        from .engine import AnalysisResult

        conn = duckdb.connect(str(self.path), read_only=True)
        sql = _duckdb_sql(query.sql)
        holder: dict[str, object] = {}
        done = threading.Event()

        def worker() -> None:
            try:
                cursor = conn.execute(sql, list(query.params))
                description = tuple(item[0] for item in (cursor.description or ()))
                holder["columns"] = description
                holder["rows"] = cursor.fetchall()
            except BaseException as exc:  # propagate into caller thread
                holder["error"] = exc
            finally:
                done.set()

        _notify(progress, "duckdb_query", 22.0, "Running DuckDB analytical query")
        thread = threading.Thread(target=worker, name="treepolo-duckdb-query", daemon=True)
        thread.start()
        while not done.wait(0.12):
            percentage = None
            try:
                raw = float(conn.query_progress())
                if raw >= 0:
                    if raw <= 1.0:
                        raw *= 100.0
                    percentage = 22.0 + max(0.0, min(raw, 100.0)) * 0.72
            except Exception:
                percentage = None
            _notify(progress, "duckdb_query", percentage, "Running DuckDB analytical query")
        thread.join()
        try:
            if "error" in holder:
                raise holder["error"]  # type: ignore[misc]
            raw_columns = tuple(holder.get("columns", ()))
            columns = tuple(name for name in raw_columns if not str(name).startswith("__ta_"))
            raw_rows = holder.get("rows", ())
            rows = tuple(
                {name: row[index] for index, name in enumerate(raw_columns) if name in columns}
                for row in raw_rows  # type: ignore[union-attr]
            )
            _notify(progress, "formatting", 97.0, "Formatting analysis result")
            return AnalysisResult(columns, rows, grain, "duckdb")
        finally:
            conn.close()
