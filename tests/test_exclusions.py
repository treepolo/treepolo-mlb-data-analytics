import pytest

from seq_fixtures import EXPECTED_KEPT, INGESTED, make_seq_db, make_table_db, normalize, run_both
from treepolo_mlb_data.analysis import PITCH_GRAIN, Project, Source, NamedExpr, Column, Sort, OrderKey, apply_exclusions
from treepolo_mlb_data.analysis.exclusions import exclude_bunt_plate_appearances, exclude_non_pitch_rows


def uid_node(node):
    return Sort(Project(node, (NamedExpr("pitch_uid", Column("pitch_uid")),), PITCH_GRAIN), (OrderKey(Column("pitch_uid")),))


def kept(db, node):
    sqlite_result, duck_result = run_both(db, uid_node(node))
    assert normalize(sqlite_result.rows) == normalize(duck_result.rows)
    return [row["pitch_uid"] for row in sqlite_result.rows]


ALL = {
    "1:1:1", "1:1:2", "1:1:3", "1:1:4", "1:1:5", "1:1:6", "1:2:1", "1:2:2", "1:2:3", "1:2:4", "1:3:1", "1:3:2", "1:4:1", "1:4:2",
    "1:5:1", "1:6:1", "1:7:1", "1:7:2", "1:7:3", "2:1:1",
}


def test_default_policy_removes_bunt_plate_appearances_and_non_pitch_rows(tmp_path):
    db = make_seq_db(tmp_path / "s.sqlite3")
    result = kept(db, apply_exclusions(Source("pitches", PITCH_GRAIN)))
    assert set(result) == set(EXPECTED_KEPT) and len(result) == 13
    # lowercase "bunt", capitalised "Bunt", sac_bunt event, foul_bunt description are all caught
    assert not {"1:3:1", "1:3:2", "1:4:1", "1:4:2", "1:5:1", "1:6:1"} & set(result)
    assert "1:2:2" not in result  # automatic ball


def test_exclude_pitch_policy_keeps_the_rest_of_a_bunt_plate_appearance(tmp_path):
    db = make_seq_db(tmp_path / "s.sqlite3")
    result = set(kept(db, apply_exclusions(Source("pitches", PITCH_GRAIN), bunt_policy="exclude_pitch")))
    assert result == ALL - {"1:3:2", "1:4:2", "1:5:1", "1:6:1", "1:2:2"}


def test_keep_policy_only_drops_non_pitch_rows(tmp_path):
    db = make_seq_db(tmp_path / "s.sqlite3")
    assert set(kept(db, apply_exclusions(Source("pitches", PITCH_GRAIN), bunt_policy="keep"))) == ALL - {"1:2:2"}
    with pytest.raises(ValueError, match="bunt_policy"):
        apply_exclusions(Source("pitches", PITCH_GRAIN), bunt_policy="nope")


def test_null_description_and_null_pitch_type_rows_are_not_dropped(tmp_path):
    ddl = ("pitch_uid TEXT PRIMARY KEY, game_pk INTEGER, at_bat_number INTEGER, pitch_number INTEGER, pitch_type TEXT, "
           "description TEXT, events TEXT, des TEXT, _ingested_at TEXT")
    cols = ("pitch_uid", "game_pk", "at_bat_number", "pitch_number", "pitch_type", "description", "events", "des", "_ingested_at")
    rows = [
        ("a", 1, 1, 1, None, None, None, None, INGESTED),            # everything NULL: keep
        ("b", 1, 1, 2, "FF", None, None, None, INGESTED),            # NULL description: keep
        ("c", 1, 1, 3, None, "ball", None, None, INGESTED),          # NULL pitch type but a real description: keep
        ("d", 1, 1, 4, "PO", "ball", None, None, INGESTED),          # pitchout type: drop
        ("e", 1, 1, 5, "FF", "pitchout", None, None, INGESTED),      # pitchout description: drop
        ("f", 1, 1, 6, "FF", "intent_ball", None, None, INGESTED),   # intentional ball: drop
        ("g", 1, 1, 7, None, "automatic_strike", None, None, INGESTED),
    ]
    db = make_table_db(tmp_path / "n.sqlite3", ddl, rows, cols)
    assert kept(db, exclude_non_pitch_rows(Source("pitches", PITCH_GRAIN))) == ["a", "b", "c"]
    assert kept(db, apply_exclusions(Source("pitches", PITCH_GRAIN))) == ["a", "b", "c"]  # NULL events/des never flag a bunt


def test_bunt_plate_appearance_filter_alone(tmp_path):
    db = make_seq_db(tmp_path / "s.sqlite3")
    result = set(kept(db, exclude_bunt_plate_appearances(Source("pitches", PITCH_GRAIN))))
    assert result == ALL - {"1:3:1", "1:3:2", "1:4:1", "1:4:2", "1:5:1", "1:6:1"}


def test_bunt_foul_tip_and_swinging_pitchout_found_in_real_data(tmp_path):
    ddl = ("pitch_uid TEXT PRIMARY KEY, game_pk INTEGER, at_bat_number INTEGER, pitch_number INTEGER, pitch_type TEXT, "
           "description TEXT, events TEXT, des TEXT, _ingested_at TEXT")
    cols = ("pitch_uid", "game_pk", "at_bat_number", "pitch_number", "pitch_type", "description", "events", "des", "_ingested_at")
    rows = [
        ("a1", 1, 1, 1, "FF", "ball", None, None, INGESTED), ("a2", 1, 1, 2, "FF", "bunt_foul_tip", None, None, INGESTED),   # bunt PA
        ("b1", 1, 2, 1, "FF", "ball", None, None, INGESTED), ("b2", 1, 2, 2, "FF", "swinging_pitchout", None, None, INGESTED),
    ]
    db = make_table_db(tmp_path / "b.sqlite3", ddl, rows, cols)
    assert kept(db, apply_exclusions(Source("pitches", PITCH_GRAIN))) == ["b1"]
    assert kept(db, apply_exclusions(Source("pitches", PITCH_GRAIN), bunt_policy="exclude_pitch")) == ["a1", "b1"]
