from __future__ import annotations

import statistics
import time
from pathlib import Path
from typing import Any

from .config import AppConfig
from .duckdb_mirror import DuckDBMirror
from .fast_status import read_fast_status
from .web_analysis import AnalysisFacade


def canonical_velocity_payload(year: int) -> dict[str, Any]:
    return {
        "mode": "basic",
        "filters": [{"field": "game_year", "op": "eq", "value": int(year)}],
        "group_by": ["pitch_type"],
        "metrics": [
            {"function": "count", "field": "", "distinct": False},
            {"function": "avg", "field": "release_speed", "distinct": False},
        ],
        "result_sort": [{"field": "avg_release_speed", "descending": True}],
        "limit": 200,
    }


def _measure(facade: AnalysisFacade, payload: dict[str, Any], runs: int) -> dict[str, Any]:
    times: list[float] = []
    result: dict[str, Any] | None = None
    for _ in range(max(1, runs)):
        started = time.perf_counter()
        result = facade.analyze(payload)
        times.append(time.perf_counter() - started)
    assert result is not None
    return {
        "actual_backend": result.get("backend"),
        "runs": len(times),
        "seconds": times,
        "min_seconds": min(times),
        "median_seconds": statistics.median(times),
        "max_seconds": max(times),
        "row_count": result.get("row_count"),
    }


def run_benchmark(config: AppConfig, *, year: int = 2026, runs: int = 3, backend: str = "both") -> dict[str, Any]:
    if backend not in {"sqlite", "duckdb", "both"}:
        raise ValueError("backend must be sqlite, duckdb, or both")
    payload = canonical_velocity_payload(year)
    report: dict[str, Any] = {
        "benchmark": "season_pitch_type_average_velocity",
        "year": int(year),
        "database": str(config.database_path),
        "analytics_database": str(config.analytics_database_path),
        "pitch_rows": read_fast_status(config.database_path).get("pitch_rows"),
        "results": {},
    }

    if backend in {"sqlite", "both"}:
        facade = AnalysisFacade(config.database_path, backend="sqlite")
        # One unmeasured warm-up lets SQLite populate OS/page caches; reported
        # runs then reflect interactive repeated-use performance.
        facade.analyze(payload)
        report["results"]["sqlite"] = _measure(facade, payload, runs)

    if backend in {"duckdb", "both"}:
        mirror_started = time.perf_counter()
        mirror = DuckDBMirror(config.database_path, config.analytics_database_path).ensure()
        mirror_seconds = time.perf_counter() - mirror_started
        facade = AnalysisFacade(config.database_path, config.analytics_database_path, backend="duckdb")
        facade.analyze(payload)
        result = _measure(facade, payload, runs)
        result["mirror_prepare_seconds"] = mirror_seconds
        result["mirror_rebuilt"] = bool(mirror.get("rebuilt"))
        report["results"]["duckdb"] = result

    return report
