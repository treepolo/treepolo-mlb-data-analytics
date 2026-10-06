"""One-pitch half-innings that cover all 288 base-out-count states (appendix B of docs/PITCH_SEQUENCING_DEV_P3_P4.md)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from pitch_fixtures import create_pitches_db, insert_rows, make_row
from treepolo_mlb_data.analysis.run_values import all_state_codes, decode_state


def state_rows(year: int = 2024, game_offset: int = 0) -> list[tuple]:
    """Two half-innings per state; the batting team scores 0 in one and 2 in the other, so RE(state) = 1.0 for every state,
    each pitch has run value -1.0 or +1.0, and the cluster-robust SE of every RE is 1.0."""

    rows = []
    for code in all_state_codes():
        s = decode_state(code)
        for index, runs in enumerate((0, 2)):
            game = game_offset + code * 10 + index + 1
            rows.append(make_row(
                game_pk=game, game_year=year, game_date=f"{year}-05-01", at_bat_number=1, pitch_number=1, inning="1", inning_topbot="Top",
                description="ball", balls=s["balls"], strikes=s["strikes"], outs_when_up=s["outs"],
                on_1b=101 if s["bases"] & 1 else None, on_2b=102 if s["bases"] & 2 else None, on_3b=103 if s["bases"] & 4 else None,
                bat_score=0, post_bat_score=runs, post_away_score=runs, post_home_score=0, pitcher=10 + index, batter=100 + code % 7,
            ))
    return rows


def make_state_db(path: Path, years=(2023, 2024)) -> Path:
    conn = create_pitches_db(path)
    for i, year in enumerate(years):
        insert_rows(conn, state_rows(year, game_offset=i * 100_000))
    conn.commit(); conn.close()
    return Path(path)
