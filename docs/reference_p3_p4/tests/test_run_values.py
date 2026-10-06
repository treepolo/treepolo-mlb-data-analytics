from __future__ import annotations

import pytest

from pitch_fixtures import run_both
from run_fixtures import EXPECTED, EXPECTED_RE, RE_TABLE, make_run_db
from treepolo_mlb_data.analysis import Column, Literal, NamedExpr, OrderKey, PITCH_GRAIN, Project, Sort, Source
from treepolo_mlb_data.analysis.model import Binary
from treepolo_mlb_data.analysis.run_values import (
    STATE_COUNT, all_state_codes, decode_state, lookup_tree, pitch_value_field, re_table_node, run_context,
)


def _context():
    return run_context(Source("pitches", PITCH_GRAIN), ("description",))


def _by_uid(rows):
    return {row["pitch_uid"]: row for row in rows}


def test_run_context_matches_hand_computation(tmp_path):
    db = make_run_db(tmp_path / "run.sqlite3")
    sqlite_result, duck_result = run_both(db, Sort(_context(), (OrderKey(Column("pitch_uid")),)))
    for result in (sqlite_result, duck_result):
        rows = _by_uid(result.rows)
        assert set(rows) == set(EXPECTED)
        for uid, (state, nxt, to_end, on_pitch, walkoff, _) in EXPECTED.items():
            row = rows[uid]
            assert (row["state_code"], row["next_state_code"], row["runs_to_end"], row["runs_on_pitch"], row["is_walkoff_half"]) == (
                state, nxt, to_end, on_pitch, walkoff), uid


def test_re_table_counts_means_and_cluster_sums(tmp_path):
    db = make_run_db(tmp_path / "run.sqlite3")
    sqlite_result, duck_result = run_both(db, Sort(re_table_node(_context()), (OrderKey(Column("state_code")),)))
    for result in (sqlite_result, duck_result):
        rows = {int(r["state_code"]): r for r in result.rows}
        assert set(rows) == set(EXPECTED_RE)  # the walk-off rows (states 1 and 1001 in game 1) add nothing
        for state, (n, mean) in EXPECTED_RE.items():
            assert rows[state]["n"] == n and rows[state]["s"] / rows[state]["n"] == pytest.approx(mean)
        # state 0: two pitches in ONE half-inning -> one cluster with s=2, n=2
        assert (rows[0]["n"], rows[0]["s"], rows[0]["ss"], rows[0]["sn"], rows[0]["nn"], rows[0]["g"]) == (2, 2, 4, 4, 4, 1)
        # state 20: three half-innings, all zero runs
        assert (rows[20]["n"], rows[20]["s"], rows[20]["g"]) == (3, 0, 3)


def test_pitch_value_matches_hand_computation_and_is_null_in_walkoff_half(tmp_path):
    db = make_run_db(tmp_path / "run.sqlite3")
    node = Project(_context(), (NamedExpr("pitch_uid", Column("pitch_uid")), pitch_value_field(RE_TABLE)), PITCH_GRAIN)
    sqlite_result, duck_result = run_both(db, Sort(node, (OrderKey(Column("pitch_uid")),)))
    for result in (sqlite_result, duck_result):
        rows = _by_uid(result.rows)
        for uid, expected in ((u, v[5]) for u, v in EXPECTED.items()):
            if expected is None:
                assert rows[uid]["pitch_value"] is None, uid
            else:
                assert rows[uid]["pitch_value"] == pytest.approx(expected), uid


def test_pitch_value_is_null_when_a_state_is_missing_from_the_table(tmp_path):
    db = make_run_db(tmp_path / "run.sqlite3")
    table = {k: v for k, v in RE_TABLE.items() if k != 1000}
    node = Project(_context(), (NamedExpr("pitch_uid", Column("pitch_uid")), pitch_value_field(table)), PITCH_GRAIN)
    _, duck_result = run_both(db, node)
    rows = _by_uid(duck_result.rows)
    assert rows["1:1:1"]["pitch_value"] is None and rows["1:1:2"]["pitch_value"] is None  # next state / state 1000
    assert rows["1:2:1"]["pitch_value"] == pytest.approx(0.5 + 2 - 0.9)


def test_lookup_tree_returns_every_key_and_null_for_unknown_keys(tmp_path):
    from pitch_fixtures import make_pitches_db, make_row
    table = {code: code / 7.0 for code in all_state_codes()}
    rows = [make_row(pitch_number=i + 1, balls=code // 1000, strikes=(code // 100) % 10) for i, code in enumerate(all_state_codes())]
    rows.append(make_row(pitch_number=len(rows) + 1, balls=9, strikes=9))  # code 9900: not in the table
    db = make_pitches_db(tmp_path / "tree.sqlite3", rows)
    code = Binary(Binary(Column("balls"), "*", Literal(1000)), "+", Binary(Column("strikes"), "*", Literal(100)))
    node = Project(Source("pitches", PITCH_GRAIN), (NamedExpr("pitch_uid", Column("pitch_uid")), NamedExpr("code", code),
                                                   NamedExpr("value", lookup_tree(code, {c: c / 7.0 for c in (0, 100, 200, 1000, 3200)}))), PITCH_GRAIN)
    sqlite_result, duck_result = run_both(db, node)
    for result in (sqlite_result, duck_result):
        for row in result.rows:
            expected = row["code"] / 7.0 if row["code"] in (0, 100, 200, 1000, 3200) else None
            assert (row["value"] is None) if expected is None else row["value"] == pytest.approx(expected)
    assert len(table) == STATE_COUNT == len(set(all_state_codes()))


def test_decode_state():
    assert decode_state(2217) == {"balls": 2, "strikes": 2, "outs": 1, "bases": 7}
    assert decode_state(0) == {"balls": 0, "strikes": 0, "outs": 0, "bases": 0}


def test_run_context_rejects_reserved_carry_names():
    with pytest.raises(ValueError):
        run_context(Source("pitches", PITCH_GRAIN), ("description", "state_code"))
