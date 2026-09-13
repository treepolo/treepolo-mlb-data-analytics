from __future__ import annotations

import tempfile
from pathlib import Path

from treepolo_mlb_data._lazy_duckdb import duckdb
from treepolo_mlb_data import stage4d
from treepolo_mlb_data.stage4d_export_v2 import install, parquet_bytes_bulk


def _section(row_count: int = 18_887) -> dict:
    rows = [
        {
            "pitcher": 600000 + (index % 500),
            "game_pk": 820000 + index,
            "unit_value": 90.0 + (index % 100) / 10.0,
            "baseline_value": 89.5 + (index % 100) / 10.0,
            "difference": 0.5,
        }
        for index in range(row_count)
    ]
    return {
        "columns": ["pitcher", "game_pk", "unit_value", "baseline_value", "difference"],
        "rows": rows,
        "row_count": row_count,
    }


def test_parquet_bulk_export_round_trips_full_realistic_row_count():
    body = parquet_bytes_bulk(_section())
    assert body[:4] == b"PAR1"
    assert body[-4:] == b"PAR1"

    with tempfile.TemporaryDirectory(prefix="treepolo-parquet-test-") as temp:
        path = Path(temp) / "result.parquet"
        path.write_bytes(body)
        conn = duckdb.connect()
        try:
            count, min_pitcher, max_game = conn.execute(
                f"SELECT count(*), min(pitcher), max(game_pk) FROM read_parquet('{path.as_posix()}')"
            ).fetchone()
        finally:
            conn.close()

    assert count == 18_887
    assert min_pitcher == 600000
    assert max_game == 820000 + 18_886


def test_install_replaces_only_stage4d_parquet_serializer():
    original = stage4d._parquet_bytes
    try:
        if hasattr(stage4d, "_stage4d_export_v2_installed"):
            delattr(stage4d, "_stage4d_export_v2_installed")
        install()
        assert stage4d._parquet_bytes is parquet_bytes_bulk
    finally:
        stage4d._parquet_bytes = original
        if hasattr(stage4d, "_stage4d_export_v2_installed"):
            delattr(stage4d, "_stage4d_export_v2_installed")
