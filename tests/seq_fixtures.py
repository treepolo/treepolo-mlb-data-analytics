"""Shared fixtures for the P2 sequence tests (see docs/PITCH_SEQUENCING_DEV.md, appendix B)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from treepolo_mlb_data.analysis import AnalysisEngine

INGESTED = "2026-01-01T00:00:00+00:00"
COLUMNS = (
    "pitch_uid", "game_pk", "at_bat_number", "pitch_number", "game_year", "game_type", "game_date", "pitcher", "batter",
    "pitch_type", "description", "events", "des", "release_speed", "plate_x", "plate_z", "p_throws", "stand", "_ingested_at",
)
CARRY = ("pitch_uid", "game_pk", "at_bat_number", "pitch_number", "pitch_type", "description", "release_speed", "plate_x", "plate_z")

# (game, ab, [(pitch_type, description, speed, plate_x, plate_z), ...], events_last, des_last)
PLATE_APPEARANCES = [
    (1, 1, [("FF", "ball", 92.0, 0.10, 2.50), ("FF", "foul", 93.0, 0.20, 2.60), ("SL", "swinging_strike", 85.0, -0.30, 1.50),
            ("SL", "called_strike", 86.0, -0.10, 1.60), ("SL", "foul", 84.0, 0.00, 1.40), ("CH", "hit_into_play", 88.0, 0.05, 2.00)],
     "field_out", "X grounds out to shortstop"),
    (1, 2, [("SI", "ball", 94.0, 0.5, 2.0), (None, "automatic_ball", None, None, None), ("SI", "foul", 95.0, 0.4, 2.1),
            ("SI", "called_strike", 95.5, 0.3, 2.2)], None, None),
    (1, 3, [("FF", "ball", 93.0, 0.0, 3.0), ("FF", "foul_bunt", 93.0, 0.0, 2.0)], None, None),
    (1, 4, [("CU", "ball", 80.0, 0.0, 1.0), ("CU", "hit_into_play", 81.0, 0.0, 2.0)], "sac_bunt", "Smith sacrifice bunt, pitcher to first"),
    (1, 5, [("FF", "hit_into_play", 92.0, 0.0, 2.0)], "single", "Jones bunts a ground ball single to third"),
    (1, 6, [("SL", "hit_into_play", 85.0, 0.0, 2.0)], "field_out", "Lee Bunt Ground Out"),
    (1, 7, [("FF", "ball", 92.0, 0.0, 2.0), (None, "ball", None, 0.1, 2.0), ("FF", "called_strike", 92.0, 0.0, 2.0)], None, None),
    (2, 1, [("FF", "hit_into_play", 93.0, 0.0, 2.0)], "home_run", "Doe homers"),
]

# uid -> expected row after apply_exclusions + sequence_features(memory=2)
EXPECTED_KEPT = {
    "1:1:1": dict(idx=1, prev1=None, key="^>^", streak=1, complete=1, dspeed=None, dx=None),
    "1:1:2": dict(idx=2, prev1="FF", key="^>FF", streak=2, complete=1, dspeed=1.0, dx=0.1),
    "1:1:3": dict(idx=3, prev1="FF", key="FF>FF", streak=1, complete=1, dspeed=-8.0, dx=-0.5),
    "1:1:4": dict(idx=4, prev1="SL", key="FF>SL", streak=2, complete=1, dspeed=1.0, dx=0.2),
    "1:1:5": dict(idx=5, prev1="SL", key="SL>SL", streak=3, complete=1, dspeed=-2.0, dx=0.1),
    "1:1:6": dict(idx=6, prev1="SL", key="SL>SL", streak=1, complete=1, dspeed=4.0, dx=0.05),
    "1:2:1": dict(idx=1, prev1=None, key="^>^", streak=1, complete=1, dspeed=None, dx=None),
    "1:2:3": dict(idx=2, prev1="SI", key="^>SI", streak=2, complete=1, dspeed=1.0, dx=-0.1),
    "1:2:4": dict(idx=3, prev1="SI", key="SI>SI", streak=3, complete=1, dspeed=0.5, dx=-0.1),
    "1:7:1": dict(idx=1, prev1=None, key="^>^", streak=1, complete=1, dspeed=None, dx=None),
    "1:7:2": dict(idx=2, prev1="FF", key="^>FF", streak=None, complete=0, dspeed=None, dx=0.1),
    "1:7:3": dict(idx=3, prev1=None, key="FF>?", streak=1, complete=0, dspeed=None, dx=-0.1),
    "2:1:1": dict(idx=1, prev1=None, key="^>^", streak=1, complete=1, dspeed=None, dx=None),
}

EXPECTED_CATEGORY = {
    "1:1:1": "ball", "1:1:2": "foul", "1:1:3": "whiff", "1:1:4": "called_strike", "1:1:5": "foul", "1:1:6": "in_play_out",
    "1:2:1": "ball", "1:2:2": "unclassified", "1:2:3": "foul", "1:2:4": "called_strike",
    "1:3:1": "ball", "1:3:2": "bunt", "1:4:1": "ball", "1:4:2": "bunt", "1:5:1": "bunt", "1:6:1": "bunt",
    "1:7:1": "ball", "1:7:2": "ball", "1:7:3": "called_strike", "2:1:1": "in_play_home_run",
}


def seq_rows() -> list[tuple]:
    rows: list[tuple] = []
    for game, ab, items, events_last, des_last in PLATE_APPEARANCES:
        for number, (pitch_type, description, speed, x, z) in enumerate(items, 1):
            last = number == len(items)
            rows.append((
                f"{game}:{ab}:{number}", game, ab, number, 2024, "R", "2024-05-01", 10, 100 + ab, pitch_type, description,
                events_last if last else None, des_last if last else None, speed, x, z, "R", "R", INGESTED,
            ))
    return rows


def make_table_db(path: Path, ddl_columns: str, rows: list[tuple], column_names: tuple[str, ...]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL);"
        "INSERT INTO settings VALUES ('data_revision','rev-1','2026-01-01T00:00:00+00:00');"
        f"CREATE TABLE pitches ({ddl_columns});"
    )
    marks = ",".join("?" for _ in column_names)
    conn.executemany(f"INSERT INTO pitches ({','.join(column_names)}) VALUES ({marks})", rows)
    conn.commit(); conn.close()
    return path


def make_seq_db(path: Path) -> Path:
    ddl = ("pitch_uid TEXT PRIMARY KEY, game_pk INTEGER, at_bat_number INTEGER, pitch_number INTEGER, game_year INTEGER, game_type TEXT, "
           "game_date TEXT, pitcher INTEGER, batter INTEGER, pitch_type TEXT, description TEXT, events TEXT, des TEXT, release_speed REAL, "
           "plate_x REAL, plate_z REAL, p_throws TEXT, stand TEXT, _ingested_at TEXT")
    return make_table_db(path, ddl, seq_rows(), COLUMNS)


def run_both(db: Path, node):
    """Execute a node on SQLite and DuckDB; assert the DuckDB run really used DuckDB (it silently falls back otherwise)."""
    sqlite_result = AnalysisEngine(db, backend="sqlite").execute(node)
    duck_result = AnalysisEngine(db, analytics_database_path=db.with_suffix(".duckdb"), backend="duckdb").execute(node)
    assert sqlite_result.backend == "sqlite"
    assert duck_result.backend == "duckdb", "DuckDB silently fell back to SQLite"
    return sqlite_result, duck_result


def normalize(rows):
    out = []
    for row in rows:
        item = {}
        for key, value in dict(row).items():
            if isinstance(value, float):
                value = round(value, 9)
            elif value is not None and not isinstance(value, (str, float)):
                try:
                    value = int(value)
                except (TypeError, ValueError):
                    pass
            item[key] = value
        out.append(item)
    return out
