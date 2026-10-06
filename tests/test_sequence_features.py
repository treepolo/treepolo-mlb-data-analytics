import pytest

from seq_fixtures import CARRY, EXPECTED_KEPT, make_seq_db, normalize, run_both
from treepolo_mlb_data.analysis import OrderKey, PITCH_GRAIN, Column, Sort, Source, apply_exclusions, sequence_features


def build(memory=2, carry=CARRY, policy="exclude_plate_appearance"):
    node = sequence_features(apply_exclusions(Source("pitches", PITCH_GRAIN), bunt_policy=policy), carry, memory=memory)
    return Sort(node, (OrderKey(Column("game_pk")), OrderKey(Column("at_bat_number")), OrderKey(Column("pitch_number"))))


def test_expected_table_on_both_backends(tmp_path):
    db = make_seq_db(tmp_path / "s.sqlite3")
    sqlite_result, duck_result = run_both(db, build())
    assert normalize(sqlite_result.rows) == normalize(duck_result.rows)
    assert len(sqlite_result.rows) == 13
    for row in sqlite_result.rows:
        want = EXPECTED_KEPT[row["pitch_uid"]]
        assert row["pitch_index_in_pa"] == want["idx"], row["pitch_uid"]
        assert row["prev1_pitch_type"] == want["prev1"], row["pitch_uid"]
        assert row["history_key"] == want["key"], row["pitch_uid"]
        assert row["streak_pos"] == want["streak"], row["pitch_uid"]
        assert row["history_complete"] == want["complete"], row["pitch_uid"]
        for column, expected in (("speed_diff_prev1", want["dspeed"]), ("dx_prev1", want["dx"])):
            if expected is None:
                assert row[column] is None, (row["pitch_uid"], column)
            else:
                assert row[column] == pytest.approx(expected), (row["pitch_uid"], column)
    assert sqlite_result.grain.keys == ("pitch_uid",)
    assert "prev1_description" in sqlite_result.columns and not any(c.startswith("__ta_") for c in sqlite_result.columns)
    by_uid = {row["pitch_uid"]: row for row in sqlite_result.rows}
    assert by_uid["1:1:4"]["prev1_description"] == "swinging_strike" and by_uid["1:1:5"]["prev1_description"] == "called_strike"
    assert by_uid["1:1:1"]["prev1_description"] is None
    assert by_uid["1:1:5"]["dz_prev1"] == pytest.approx(-0.2)


def test_automatic_ball_does_not_break_a_streak_or_count_as_a_pitch(tmp_path):
    db = make_seq_db(tmp_path / "s.sqlite3")
    sqlite_result, _ = run_both(db, build())
    rows = {r["pitch_uid"]: r for r in sqlite_result.rows}
    assert rows["1:2:3"]["streak_pos"] == 2 and rows["1:2:3"]["pitch_index_in_pa"] == 2 and rows["1:2:3"]["pitch_number"] == 3
    assert rows["1:2:4"]["streak_pos"] == 3


def test_history_key_for_other_memory_lengths(tmp_path):
    db = make_seq_db(tmp_path / "s.sqlite3")
    keys = {}
    for memory in (1, 3):
        sqlite_result, duck_result = run_both(db, build(memory=memory))
        assert normalize(sqlite_result.rows) == normalize(duck_result.rows)
        keys[memory] = {r["pitch_uid"]: r["history_key"] for r in sqlite_result.rows}
        assert f"prev{memory}_pitch_type" in sqlite_result.columns and f"prev{memory + 1}_pitch_type" not in sqlite_result.columns
    assert keys[1]["1:1:1"] == "^" and keys[1]["1:1:2"] == "FF" and keys[1]["1:1:3"] == "FF" and keys[1]["1:1:4"] == "SL"
    assert keys[3]["1:1:2"] == "^>^>FF" and keys[3]["1:1:5"] == "FF>SL>SL" and keys[3]["1:7:3"] == "^>FF>?"


def test_keeping_bunts_keeps_their_plate_appearances_in_the_sequence(tmp_path):
    db = make_seq_db(tmp_path / "s.sqlite3")
    sqlite_result, duck_result = run_both(db, build(policy="keep"))
    assert normalize(sqlite_result.rows) == normalize(duck_result.rows)
    rows = {r["pitch_uid"]: r for r in sqlite_result.rows}
    assert rows["1:3:2"]["streak_pos"] == 2 and rows["1:3:2"]["history_key"] == "^>FF"


def test_argument_validation():
    base = Source("pitches", PITCH_GRAIN)
    for bad in (0, 6, True, 1.5, "2"):
        with pytest.raises(ValueError, match="memory"):
            sequence_features(base, CARRY, memory=bad)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="pitch_uid"):
        sequence_features(base, tuple(c for c in CARRY if c != "pitch_uid"))
    with pytest.raises(ValueError, match="generated columns"):
        sequence_features(base, CARRY + ("streak_pos",))
    with pytest.raises(ValueError, match="generated columns"):
        sequence_features(base, CARRY + ("prev2_pitch_type",), memory=2)
