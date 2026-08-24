import sqlite3

from treepolo_mlb_data.analysis import (
    AnalysisEngine, Binary, Column, Filter, Literal, NamedExpr, PITCH_GRAIN,
    Project, SetOperation, Source,
)


def test_set_difference(tmp_path):
    path = tmp_path / "statcast.sqlite3"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE pitches (pitch_uid TEXT, pitcher INTEGER, pitch_type TEXT)")
    conn.executemany("INSERT INTO pitches VALUES (?,?,?)", [
        ("1", 100, "FF"), ("2", 100, "SL"), ("3", 100, "SL"),
        ("4", 200, "FF"), ("5", 200, "CH"),
    ])
    conn.commit(); conn.close()

    source = Source("pitches", PITCH_GRAIN)
    left = Project(
        Filter(source, Binary(Column("pitcher"), "=", Literal(100))),
        (NamedExpr("pitch_uid", Column("pitch_uid")),),
    )
    right = Project(
        Filter(source, Binary(Column("pitch_type"), "=", Literal("FF"))),
        (NamedExpr("pitch_uid", Column("pitch_uid")),),
    )
    result = AnalysisEngine(path).execute(SetOperation(left, right, "except"))
    assert {row["pitch_uid"] for row in result.rows} == {"2", "3"}
