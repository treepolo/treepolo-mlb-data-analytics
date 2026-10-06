"""Full-width pitches table fixtures for the P3/P4 tests (see docs/PITCH_SEQUENCING_DEV_P3_P4.md, appendix B)."""
from __future__ import annotations

from pathlib import Path

from treepolo_mlb_data.analysis import AnalysisEngine

from treepolo_mlb_data.research.synthetic import (  # noqa: F401  (re-exported for the tests)
    COLUMNS, DEFAULTS, INGESTED, PITCHES_DDL, create_pitches_db, insert_rows, make_pitches_db, make_row,
)


def run_both(db: Path, node):
    """Execute on SQLite and DuckDB; assert DuckDB really ran (the engine silently falls back to SQLite on any DuckDB error)."""
    sqlite_result = AnalysisEngine(db, backend="sqlite").execute(node)
    duck_result = AnalysisEngine(db, analytics_database_path=db.with_suffix(".duckdb"), backend="duckdb").execute(node)
    assert sqlite_result.backend == "sqlite"
    assert duck_result.backend == "duckdb", "DuckDB silently fell back to SQLite"
    return sqlite_result, duck_result
