from __future__ import annotations

import io
import sqlite3

from treepolo_mlb_data.cli_progress import OptimizeProgressDisplay, optimize_with_progress
from treepolo_mlb_data.storage import StatcastStore


def test_optimize_progress_observes_real_sql_and_keeps_optimizer_single_sourced(tmp_path):
    path = tmp_path / "db.sqlite3"
    store = StatcastStore(path)
    try:
        store.conn.execute("""
            CREATE TABLE pitches (
                pitch_uid TEXT PRIMARY KEY,
                game_date TEXT,
                game_pk INTEGER,
                at_bat_number INTEGER,
                pitch_number INTEGER,
                pitcher INTEGER,
                batter INTEGER,
                pitch_type TEXT,
                game_year INTEGER,
                p_throws TEXT,
                stand TEXT
            )
        """)
        store.conn.executemany(
            "INSERT INTO pitches VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [
                ("1", "2026-04-01", 1, 1, 1, 10, 20, "FF", 2026, "R", "R"),
                ("2", "2026-04-01", 1, 1, 2, 10, 20, "SL", 2026, "R", "R"),
            ],
        )
        store.conn.commit()
        output = io.StringIO()

        optimize_with_progress(store, output)

        text = output.getvalue()
        assert "Build/check analysis indexes" in text
        assert "idx_pitches_game_year" in text
        assert "Analyze planner statistics" in text
        assert "Finalize SQLite optimizer" in text
        assert "Completed" in text
        indexes = {row[0] for row in store.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        )}
        assert "idx_pitches_game_year" in indexes
        assert "idx_pitches_pa_order" in indexes
        assert store.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sqlite_stat1'"
        ).fetchone()
    finally:
        store.close()


def test_progress_display_does_not_fake_per_index_percentage():
    output = io.StringIO()
    display = OptimizeProgressDisplay(output)
    display.start()
    display.trace_sql("CREATE INDEX IF NOT EXISTS idx_demo ON pitches(game_year)")
    display.trace_sql("ANALYZE")
    display.trace_sql("PRAGMA optimize")
    display.finish()

    text = output.getvalue()
    assert "idx_demo" in text
    assert "1/3" in text
    assert "2/3" in text
    assert "%" not in text
