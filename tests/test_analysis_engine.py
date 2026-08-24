import sqlite3

from treepolo_mlb_data.analysis import (
    Aggregate, AnalysisEngine, Binary, Column, Filter, Grain, Literal, Metric,
    NamedExpr, OrderKey, PITCH_GRAIN, Rank, Sort, Source,
)


def make_db(path):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE pitches (pitch_uid TEXT, game_pk INTEGER, pitcher INTEGER, pitch_type TEXT, release_speed REAL)")
    conn.executemany("INSERT INTO pitches VALUES (?,?,?,?,?)", [
        ("1", 10, 100, "FF", 95.0), ("2", 10, 100, "FF", 97.0), ("3", 10, 100, "SL", 85.0),
        ("4", 11, 100, "FF", 96.0), ("5", 11, 100, "SL", 84.0), ("6", 11, 100, "SL", 86.0),
        ("7", 12, 200, "FF", 94.0), ("8", 12, 200, "CH", 88.0), ("9", 12, 200, "CH", 87.0),
    ])
    conn.commit(); conn.close()


def test_filter_group_and_metrics(tmp_path):
    path = tmp_path / "statcast.sqlite3"; make_db(path)
    node = Sort(
        Aggregate(
            Filter(Source("pitches", PITCH_GRAIN), Binary(Column("pitch_type"), "=", Literal("FF"))),
            (NamedExpr("game_pk", Column("game_pk")),),
            (Metric("pitch_count", "count"), Metric("avg_velocity", "avg", Column("release_speed"))),
            Grain(("game_pk",), "game"),
        ),
        (OrderKey(Column("game_pk")),),
    )
    result = AnalysisEngine(path).execute(node)
    assert result.grain.keys == ("game_pk",)
    assert result.rows == (
        {"game_pk": 10, "pitch_count": 2, "avg_velocity": 96.0},
        {"game_pk": 11, "pitch_count": 1, "avg_velocity": 96.0},
        {"game_pk": 12, "pitch_count": 1, "avg_velocity": 94.0},
    )


def test_rank_pitch_types_within_pitcher(tmp_path):
    path = tmp_path / "statcast.sqlite3"; make_db(path)
    usage = Aggregate(
        Source("pitches", PITCH_GRAIN),
        (NamedExpr("pitcher", Column("pitcher")), NamedExpr("pitch_type", Column("pitch_type"))),
        (Metric("pitch_count", "count"),),
        Grain(("pitcher", "pitch_type"), "pitcher_pitch_type"),
    )
    ranked = Rank(usage, "usage_rank", (OrderKey(Column("pitch_count"), descending=True),), (Column("pitcher"),), "dense_rank")
    result = AnalysisEngine(path).execute(ranked)
    lookup = {(row["pitcher"], row["pitch_type"]): row["usage_rank"] for row in result.rows}
    assert lookup[(100, "FF")] == 1
    assert lookup[(100, "SL")] == 1
    assert lookup[(200, "CH")] == 1
    assert lookup[(200, "FF")] == 2
