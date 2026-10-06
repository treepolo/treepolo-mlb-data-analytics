import pytest

from seq_fixtures import EXPECTED_CATEGORY, INGESTED, make_seq_db, make_table_db, normalize, run_both
from treepolo_mlb_data.analysis import (
    Column, NamedExpr, OrderKey, OUTCOME_CATEGORIES, PITCH_GRAIN, Project, Sort, Source, outcome_category_expr,
)


def categories(db, **kwargs):
    node = Sort(
        Project(Source("pitches", PITCH_GRAIN), (NamedExpr("pitch_uid", Column("pitch_uid")), NamedExpr("cat", outcome_category_expr(**kwargs))), PITCH_GRAIN),
        (OrderKey(Column("pitch_uid")),),
    )
    sqlite_result, duck_result = run_both(db, node)
    assert normalize(sqlite_result.rows) == normalize(duck_result.rows)
    return {row["pitch_uid"]: row["cat"] for row in sqlite_result.rows}


def test_expected_categories_on_the_sequence_fixture(tmp_path):
    db = make_seq_db(tmp_path / "s.sqlite3")
    assert categories(db) == EXPECTED_CATEGORY


def test_every_in_play_event_and_unknown_values(tmp_path):
    ddl = "pitch_uid TEXT PRIMARY KEY, description TEXT, events TEXT, des TEXT, _ingested_at TEXT"
    cols = ("pitch_uid", "description", "events", "des", "_ingested_at")
    play = lambda uid, event, des=None: (uid, "hit_into_play", event, des, INGESTED)
    rows = [
        play("hr", "home_run"), play("3b", "triple"), play("2b", "double"), play("1b", "single"),
        play("o1", "field_out"), play("o2", "force_out"), play("o3", "grounded_into_double_play"), play("o4", "double_play"),
        play("o5", "sac_fly"), play("o6", "fielders_choice_out"), play("o7", "triple_play"), play("o8", "sac_fly_double_play"),
        play("e1", "field_error"), play("e2", "fielders_choice"), play("e3", "catcher_interf"),
        play("weird", "some_new_event"), play("noevent", None),
        ("bb", "blocked_ball", None, None, INGESTED), ("sb", "swinging_strike_blocked", None, None, INGESTED),
        ("tip", "foul_tip", None, None, INGESTED), ("hbp", "hit_by_pitch", None, None, INGESTED),
        ("mb", "missed_bunt", None, None, INGESTED), ("fb", "foul_bunt", None, None, INGESTED),
        ("nulldesc", None, None, None, INGESTED), ("newdesc", "something_else", None, None, INGESTED),
        play("bunt1", "single", "X bunts for a single"), play("bunt2", "field_out", "Y Bunt Pop Out"), play("bunt3", "sac_bunt_double_play"),
    ]
    db = make_table_db(tmp_path / "o.sqlite3", ddl, rows, cols)
    got = categories(db)
    assert got["hr"] == "in_play_home_run" and got["3b"] == "in_play_triple" and got["2b"] == "in_play_double" and got["1b"] == "in_play_single"
    assert {got[k] for k in ("o1", "o2", "o3", "o4", "o5", "o6", "o7", "o8")} == {"in_play_out"}
    assert {got[k] for k in ("e1", "e2", "e3")} == {"in_play_error_other"}
    assert got["weird"] == "unclassified" and got["noevent"] == "unclassified"
    assert got["bb"] == "ball" and got["sb"] == "whiff" and got["tip"] == "foul_tip" and got["hbp"] == "hit_by_pitch"
    assert got["mb"] == "bunt" and got["fb"] == "bunt"
    assert got["nulldesc"] == "unclassified" and got["newdesc"] == "unclassified"
    assert {got[k] for k in ("bunt1", "bunt2", "bunt3")} == {"bunt"}
    assert set(got.values()) <= set(OUTCOME_CATEGORIES)


def test_merge_renames_categories(tmp_path):
    db = make_seq_db(tmp_path / "s.sqlite3")
    merged = categories(db, merge={"in_play_out": "in_play", "in_play_home_run": "in_play", "foul": "foul_any", "foul_tip": "foul_any"})
    assert merged["1:1:6"] == "in_play" and merged["2:1:1"] == "in_play" and merged["1:1:2"] == "foul_any"
    assert merged["1:1:1"] == "ball" and merged["1:2:2"] == "unclassified"


def test_merge_validation():
    with pytest.raises(ValueError, match="Unknown outcome category"):
        outcome_category_expr(merge={"nope": "x"})
    with pytest.raises(ValueError, match="non-empty"):
        outcome_category_expr(merge={"ball": ""})
