import pytest

from seq_fixtures import INGESTED, make_table_db, normalize, run_both
from treepolo_mlb_data.analysis import MIRROR_RULES, NamedExpr, OrderKey, PITCH_GRAIN, Column, Sort, Source, mirror_to_right_handed_pitcher

DDL = ("pitch_uid TEXT PRIMARY KEY, p_throws TEXT, stand TEXT, plate_x REAL, pfx_x REAL, release_pos_x REAL, vx0 REAL, ax REAL, "
       "spin_axis REAL, zone INTEGER, plate_z REAL, _ingested_at TEXT")
COLS = ("pitch_uid", "p_throws", "stand", "plate_x", "pfx_x", "release_pos_x", "vx0", "ax", "spin_axis", "zone", "plate_z", "_ingested_at")
ROWS = [
    ("a", "R", "R", 0.5, -1.0, -2.0, 3.0, -4.0, 200.0, 1, 2.5, INGESTED),
    ("b", "L", "L", -0.5, 1.0, 2.0, -3.0, 4.0, 160.0, 3, 2.5, INGESTED),
    ("c", "R", "L", 0.2, 0.0, -1.5, 1.0, 2.0, 0.0, 14, 1.0, INGESTED),
    ("d", "L", "R", -0.2, 0.0, 1.5, -1.0, -2.0, 0.0, 13, 1.0, INGESTED),
    ("e", None, "R", 0.1, 0.1, 0.1, 0.1, 0.1, 10.0, 5, 2.0, INGESTED),
]
CARRY = COLS[:-1]
MIRRORED = ("plate_x", "pfx_x", "release_pos_x", "vx0", "ax", "spin_axis", "zone")


def mirrored(tmp_path, **kwargs):
    db = make_table_db(tmp_path / "m.sqlite3", DDL, ROWS, COLS)
    node = Sort(mirror_to_right_handed_pitcher(Source("pitches", PITCH_GRAIN), CARRY, **kwargs), (OrderKey(Column("pitch_uid")),))
    sqlite_result, duck_result = run_both(db, node)
    assert normalize(sqlite_result.rows) == normalize(duck_result.rows)
    return {row["pitch_uid"]: row for row in sqlite_result.rows}


def test_left_handed_pitchers_are_mirrored_into_the_right_handed_frame(tmp_path):
    rows = mirrored(tmp_path)
    a, b, c, d, e = (rows[k] for k in "abcde")
    assert all(a[f] == b[f] for f in MIRRORED) and all(c[f] == d[f] for f in MIRRORED)
    assert (b["plate_x"], b["pfx_x"], b["release_pos_x"], b["vx0"], b["ax"], b["spin_axis"], b["zone"]) == (0.5, -1.0, -2.0, 3.0, -4.0, 200.0, 1)
    assert (d["plate_x"], d["zone"], d["spin_axis"]) == (0.2, 14, 0.0)  # spin_axis 0 stays 0
    assert (a["mirrored"], b["mirrored"], c["mirrored"], d["mirrored"], e["mirrored"]) == (0, 1, 0, 1, 0)
    assert (a["frame_group"], b["frame_group"], c["frame_group"], d["frame_group"], e["frame_group"]) == (
        "same_side", "same_side", "opposite_side", "opposite_side", None)
    assert {f: e[f] for f in MIRRORED} == {"plate_x": 0.1, "pfx_x": 0.1, "release_pos_x": 0.1, "vx0": 0.1, "ax": 0.1, "spin_axis": 10.0, "zone": 5}
    assert b["plate_z"] == 2.5  # vertical values are never mirrored


def test_zone_mirror_is_an_involution_over_all_zones():
    from treepolo_mlb_data.analysis import ZONE_MIRROR
    assert all(ZONE_MIRROR[ZONE_MIRROR[z]] == z for z in ZONE_MIRROR)
    assert {2, 5, 8}.isdisjoint(ZONE_MIRROR)  # middle column is its own mirror image
    assert set(ZONE_MIRROR) == {1, 3, 4, 6, 7, 9, 11, 12, 13, 14}


def test_rules_table_controls_what_is_mirrored(tmp_path):
    rows = mirrored(tmp_path, rules={"plate_x": "negate"})
    assert rows["b"]["plate_x"] == 0.5 and rows["b"]["pfx_x"] == 1.0 and rows["b"]["zone"] == 3 and rows["b"]["spin_axis"] == 160.0
    rows = mirrored(tmp_path, rules={})
    assert rows["b"]["plate_x"] == -0.5 and rows["b"]["mirrored"] == 1  # flag still reports the frame
    assert set(MIRROR_RULES) >= {"plate_x", "pfx_x", "release_pos_x", "vx0", "ax", "spin_axis", "zone"}


def test_validation():
    base = Source("pitches", PITCH_GRAIN)
    with pytest.raises(ValueError, match="pitch_uid"):
        mirror_to_right_handed_pitcher(base, ("plate_x",))
    with pytest.raises(ValueError, match="Unknown mirror rules"):
        mirror_to_right_handed_pitcher(base, CARRY, rules={"plate_x": "flip"})
    with pytest.raises(ValueError, match="generated columns"):
        mirror_to_right_handed_pitcher(base, CARRY + ("mirrored",))
