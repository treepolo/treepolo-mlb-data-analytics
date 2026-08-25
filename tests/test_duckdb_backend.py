from __future__ import annotations

import csv
import io

from treepolo_mlb_data.duckdb_mirror import DuckDBMirror
from treepolo_mlb_data.storage import StatcastStore
from treepolo_mlb_data.web_analysis import AnalysisFacade


HEADERS = [
    "game_pk", "at_bat_number", "pitch_number", "game_date", "game_year",
    "pitcher", "batter", "pitch_type", "release_speed", "description", "zone",
    "p_throws", "stand",
]


def payload(rows):
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(HEADERS)
    writer.writerows(rows)
    return buffer.getvalue().encode()


def seed(path):
    rows = [
        (1, 1, 1, "2026-04-01", 2026, 10, 101, "ST", 84, "called_strike", 5, "R", "R"),
        (1, 1, 2, "2026-04-01", 2026, 10, 101, "ST", 85, "ball", 12, "R", "R"),
        (1, 1, 3, "2026-04-01", 2026, 10, 101, "ST", 86, "swinging_strike", 6, "R", "R"),
        (1, 2, 1, "2026-04-01", 2026, 10, 102, "FF", 96, "foul", 4, "R", "L"),
        (2, 1, 1, "2026-04-02", 2026, 20, 103, "FF", 94, "ball", 11, "R", "R"),
        (2, 1, 2, "2026-04-02", 2026, 20, 103, "CH", 87, "called_strike", 3, "R", "R"),
    ]
    with StatcastStore(path) as store:
        store.ingest_csv(payload(rows), "seed")


def canonical_basic():
    return {
        "mode": "basic",
        "filters": [{"field": "game_year", "op": "eq", "value": 2026}],
        "group_by": ["pitch_type"],
        "metrics": [
            {"function": "count", "field": ""},
            {"function": "avg", "field": "release_speed"},
            {"function": "median", "field": "release_speed"},
            {"function": "stddev_pop", "field": "release_speed"},
        ],
        "result_sort": [{"field": "avg_release_speed", "descending": True}],
        "limit": 50,
    }


def normalized_rows(result):
    return [
        {key: (round(value, 9) if isinstance(value, float) else value) for key, value in row.items()}
        for row in result["rows"]
    ]


def test_duckdb_basic_matches_sqlite_and_reports_backend(tmp_path):
    sqlite_path = tmp_path / "statcast.sqlite3"
    duckdb_path = tmp_path / "statcast.duckdb"
    seed(sqlite_path)

    mirror = DuckDBMirror(sqlite_path, duckdb_path).ensure()
    assert mirror["state"] == "ready"
    assert mirror["rebuilt"] is True

    sqlite_result = AnalysisFacade(sqlite_path, backend="sqlite").analyze(canonical_basic())
    duck_result = AnalysisFacade(sqlite_path, duckdb_path, backend="duckdb").analyze(canonical_basic())
    assert duck_result["backend"] == "duckdb"
    assert normalized_rows(duck_result) == normalized_rows(sqlite_result)


def test_duckdb_handles_sequence_and_join_heavy_arsenal(tmp_path):
    sqlite_path = tmp_path / "statcast.sqlite3"
    duckdb_path = tmp_path / "statcast.duckdb"
    seed(sqlite_path)
    DuckDBMirror(sqlite_path, duckdb_path).ensure()
    facade = AnalysisFacade(sqlite_path, duckdb_path, backend="duckdb")

    sequence = facade.analyze({
        "mode": "sequence_pattern",
        "event": {"field": "pitch_type", "op": "eq", "value": "ST"},
        "occurrence": 3,
        "exact_count": 3,
        "arrangement": "consecutive",
        "require_last_event": True,
        "result_sort": [{"field": "release_speed", "descending": True}],
    })
    assert sequence["backend"] == "duckdb"
    assert [row["pitch_uid"] for row in sequence["rows"]] == ["1:1:3"]

    arsenal = facade.analyze({
        "mode": "arsenal",
        "entity_fields": ["pitcher"],
        "min_usage": 0.0,
        "tie_method": "dense_rank",
        "result_sort": [
            {"field": "pitcher", "descending": False},
            {"field": "usage_rate", "descending": True},
        ],
    })
    assert arsenal["backend"] == "duckdb"
    assert arsenal["rows"]
    assert {row["pitcher"] for row in arsenal["rows"]} == {10, 20}


def test_duckdb_mirror_incrementally_applies_inserts_and_updates(tmp_path):
    sqlite_path = tmp_path / "statcast.sqlite3"
    duckdb_path = tmp_path / "statcast.duckdb"
    seed(sqlite_path)
    DuckDBMirror(sqlite_path, duckdb_path).ensure()

    changed = [
        (1, 2, 1, "2026-04-01", 2026, 10, 102, "FF", 99, "foul", 4, "R", "L"),
        (3, 1, 1, "2026-04-03", 2026, 30, 104, "FF", 100, "ball", 12, "R", "R"),
    ]
    with StatcastStore(sqlite_path) as store:
        stats = store.ingest_csv(payload(changed), "update")
        assert stats.updated == 1
        assert stats.inserted == 1

    sync = DuckDBMirror(sqlite_path, duckdb_path).ensure()
    assert sync["rebuilt"] is False
    assert sync["changed_rows"] == 2

    result = AnalysisFacade(sqlite_path, duckdb_path, backend="duckdb").analyze({
        "mode": "basic",
        "filters": [{"field": "pitch_type", "op": "eq", "value": "FF"}],
        "group_by": [],
        "metrics": [{"function": "max", "field": "release_speed"}],
        "limit": 10,
    })
    assert result["backend"] == "duckdb"
    assert result["rows"][0]["max_release_speed"] == 100
