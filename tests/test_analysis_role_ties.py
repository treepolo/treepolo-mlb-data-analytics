import sqlite3

from treepolo_mlb_data.analysis import AnalysisEngine, PITCH_GRAIN, Source, pitch_usage, rank_pitch_roles


def test_pitch_role_ranking_preserves_ties_and_can_break_them(tmp_path):
    path = tmp_path / "db.sqlite"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE pitches (pitch_uid TEXT, pitcher INTEGER, pitch_type TEXT)")
    conn.executemany("INSERT INTO pitches VALUES (?,?,?)", [
        ("1", 10, "FF"), ("2", 10, "FF"),
        ("3", 10, "CH"), ("4", 10, "CH"),
        ("5", 10, "ST"), ("6", 10, "ST"), ("7", 10, "ST"),
    ])
    conn.commit(); conn.close()

    usage = pitch_usage(Source("pitches", PITCH_GRAIN))
    tied = AnalysisEngine(path).execute(rank_pitch_roles(usage)).rows
    tied_rank = {row["pitch_type"]: row["role_rank"] for row in tied}
    assert tied_rank["ST"] == 1
    assert tied_rank["FF"] == tied_rank["CH"] == 2

    single = AnalysisEngine(path).execute(rank_pitch_roles(usage, method="row_number")).rows
    single_rank = {row["pitch_type"]: row["role_rank"] for row in single}
    assert sorted(single_rank.values()) == [1, 2, 3]
