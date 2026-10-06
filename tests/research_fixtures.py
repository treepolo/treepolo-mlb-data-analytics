from __future__ import annotations

import sqlite3
from pathlib import Path

PITCH_COLUMNS = (
    "pitch_uid", "game_pk", "at_bat_number", "pitch_number", "game_year", "game_type", "game_date",
    "pitcher", "batter", "pitch_type", "description", "events", "des", "release_speed",
    "plate_x", "plate_z", "p_throws", "stand", "_ingested_at",
)
INGESTED = "2026-01-01T00:00:00+00:00"


def default_rows() -> list[tuple]:
    rows: list[tuple] = []
    layout = [
        (2023, "R", "2023-05-01", 1), (2023, "S", "2023-03-05", 2),
        (2024, "R", "2024-05-01", 3), (2024, "R", "2024-06-01", 4),
        (2025, "R", "2025-05-01", 5), (2025, "F", "2025-10-05", 6),
    ]
    for year, game_type, day, game in layout:
        for number, (pitch_type, description) in enumerate([("FF", "ball"), ("SL", "called_strike")], 1):
            rows.append((
                f"{game}:1:{number}", game, 1, number, year, game_type, day, 10, 100, pitch_type,
                description, None, None, 90.0 + number, 0.1 * number, 2.0, "R", "R", INGESTED,
            ))
    return rows


def make_research_db(path: Path, rows: list[tuple] | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL);
        INSERT INTO settings VALUES ('data_revision','rev-1','2026-01-01T00:00:00+00:00');
        CREATE TABLE pitches (
            pitch_uid TEXT PRIMARY KEY, game_pk INTEGER, at_bat_number INTEGER, pitch_number INTEGER,
            game_year INTEGER, game_type TEXT, game_date TEXT, pitcher INTEGER, batter INTEGER,
            pitch_type TEXT, description TEXT, events TEXT, des TEXT, release_speed REAL,
            plate_x REAL, plate_z REAL, p_throws TEXT, stand TEXT, _ingested_at TEXT
        );
        """
    )
    marks = ",".join("?" for _ in PITCH_COLUMNS)
    conn.executemany(f"INSERT INTO pitches VALUES ({marks})", rows if rows is not None else default_rows())
    conn.commit()
    conn.close()
    return path
