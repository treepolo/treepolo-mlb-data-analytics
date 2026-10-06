from __future__ import annotations

import pytest

from pitch_fixtures import COLUMNS, make_pitches_db, make_row, run_both
from run_fixtures import EXPECTED, RE_TABLE, make_run_db
from treepolo_mlb_data.analysis import (
    OUTCOME_CATEGORIES, Column, Filter, NamedExpr, OrderKey, PITCH_GRAIN, Project, Sort, Source,
)
from treepolo_mlb_data.analysis.lint import lint_columns
from treepolo_mlb_data.analysis.pitch_table import (
    apply_filters, build_pitch_table, outcome_cells_node, pitch_table_columns, rate_terms,
)
from treepolo_mlb_data.research.stats import cluster_mean_se

SOURCE = Source("pitches", PITCH_GRAIN)


def _by_uid(rows):
    return {r["pitch_uid"]: r for r in rows}


def test_pitch_table_with_run_values_matches_the_hand_computed_values_on_both_backends(tmp_path):
    db = make_run_db(tmp_path / "run.sqlite3")
    node = Sort(build_pitch_table(SOURCE, memory=2, re_by_state=RE_TABLE), (OrderKey(Column("pitch_uid")),))
    assert lint_columns(node, COLUMNS) == []
    a, b = run_both(db, node)
    for result in (a, b):
        rows = _by_uid(result.rows)
        assert set(rows) == set(EXPECTED)
        for uid, expected in EXPECTED.items():
            if expected[5] is None:
                assert rows[uid]["pitch_value"] is None
            else:
                assert rows[uid]["pitch_value"] == pytest.approx(expected[5])
        assert rows["1:1:3"]["outcome"] == "in_play_single" and rows["1:2:1"]["outcome"] == "in_play_home_run"
        assert rows["1:2:1"]["bases"] == 1 and rows["1:1:1"]["hand_group"] == "RvR"
    assert set(a.columns) == pitch_table_columns(memory=2, with_value=True)


def test_pitch_table_columns_match_the_declared_names(tmp_path):
    db = make_run_db(tmp_path / "run.sqlite3")
    cases = (
        dict(memory=1), dict(memory=3, mirror=True), dict(memory=2, prior_same_type_count=True, loc_x_edges=(-0.5, 0.5), loc_z_edges=(2.0,)),
        dict(memory=2, extra_columns=("pfx_x",)),
    )
    for options in cases:
        node = build_pitch_table(SOURCE, **options)
        _, duck = run_both(db, node)
        declared = pitch_table_columns(
            memory=options["memory"], mirror=options.get("mirror", False), prior_same_type_count=options.get("prior_same_type_count", False),
            loc_x=bool(options.get("loc_x_edges")), loc_z=bool(options.get("loc_z_edges")), extra_columns=options.get("extra_columns", ()))
        assert set(duck.columns) == declared, options


def test_prior_same_type_count_hand_check(tmp_path):
    types = ["FF", "SL", "FF", "FF", "SL"]
    rows = [make_row(pitch_number=i + 1, pitch_type=t, balls=min(i, 3), strikes=0) for i, t in enumerate(types)]
    db = make_pitches_db(tmp_path / "psc.sqlite3", rows)
    node = build_pitch_table(SOURCE, memory=1, prior_same_type_count=True)
    _, duck = run_both(db, node)
    got = {r["pitch_number"]: (r["prior_same_type_count"], r["streak_pos"]) for r in duck.rows}
    assert got == {1: (0, 1), 2: (0, 1), 3: (1, 1), 4: (2, 2), 5: (1, 1)}


def test_location_bins_mirror_and_hand_group(tmp_path):
    rows = [
        make_row(pitch_number=1, plate_x=-0.9, plate_z=1.0, zone=1, p_throws="L", stand="R"),
        make_row(pitch_number=1, plate_x=0.0, plate_z=3.0, zone=4, p_throws="L", stand="R", at_bat_number=2),
        make_row(pitch_number=1, plate_x=0.7, plate_z=2.0, zone=3, p_throws="R", stand="L", at_bat_number=3),
        make_row(pitch_number=1, plate_x=None, plate_z=None, zone=None, at_bat_number=4),
    ]
    db = make_pitches_db(tmp_path / "loc.sqlite3", rows)
    node = build_pitch_table(SOURCE, memory=1, mirror=True, loc_x_edges=(-0.5, 0.5), loc_z_edges=(2.0,))
    _, duck = run_both(db, node)
    got = {(r["at_bat_number"], r["pitch_number"]): r for r in duck.rows}
    left = got[(1, 1)]
    assert left["hand_group"] == "LvR" and left["mirrored"] == 1 and left["plate_x"] == pytest.approx(0.9) and left["zone"] == 3
    assert left["frame_group"] == "opposite_side" and left["loc_x_bin"] == 2 and left["loc_z_bin"] == 0
    assert got[(2, 1)]["zone"] == 6 and got[(2, 1)]["loc_x_bin"] == 1 and got[(2, 1)]["loc_z_bin"] == 1
    assert got[(3, 1)]["mirrored"] == 0 and got[(3, 1)]["loc_x_bin"] == 2
    assert got[(4, 1)]["loc_x_bin"] is None and got[(4, 1)]["loc_z_bin"] is None


def test_filters_apply_after_sequence_features(tmp_path):
    rows = [make_row(pitch_number=i + 1, pitch_type=t, pitcher=10 if i < 3 else 11) for i, t in enumerate(["FF", "FF", "SL", "SL"])]
    db = make_pitches_db(tmp_path / "flt.sqlite3", rows)
    node = build_pitch_table(SOURCE, memory=1, filters=[{"field": "pitcher", "op": "eq", "value": 11}])
    _, duck = run_both(db, node)
    assert [(r["pitch_number"], r["streak_pos"]) for r in duck.rows] == [(4, 2)]      # the streak was counted on the whole PA
    assert apply_filters(SOURCE, []) is SOURCE
    for bad in ({"field": "x", "op": "like", "value": 1}, {"field": "x", "op": "in", "value": []}, {"field": "x", "op": "eq", "value": [1]}):
        with pytest.raises(ValueError):
            apply_filters(SOURCE, [bad])


def test_exclusions_precede_sequence_features(tmp_path):
    rows = [
        make_row(pitch_number=1, pitch_type="FF", description="ball"),
        make_row(pitch_number=2, pitch_type=None, description="automatic_ball", balls=1),
        make_row(pitch_number=3, pitch_type="FF", description="called_strike", balls=2),
        make_row(at_bat_number=2, pitch_number=1, pitch_type="SL", description="foul_bunt"),
    ]
    db = make_pitches_db(tmp_path / "ex.sqlite3", rows)
    _, duck = run_both(db, build_pitch_table(SOURCE, memory=1))
    assert [(r["pitch_number"], r["pitch_index_in_pa"], r["streak_pos"]) for r in duck.rows] == [(1, 1, 1), (3, 2, 2)]


def test_outcome_cells_counts_values_and_cluster_se(tmp_path):
    db = make_run_db(tmp_path / "run.sqlite3")
    table = build_pitch_table(SOURCE, memory=1, re_by_state=RE_TABLE)
    node = outcome_cells_node(table, cell_fields=("outcome",), cluster_fields=("game_pk", "at_bat_number"), categories=OUTCOME_CATEGORIES)
    a, b = run_both(db, node)
    for result in (a, b):
        rows = {r["outcome"]: r for r in result.rows}
        assert {k: v["n"] for k, v in rows.items()} == {"ball": 2, "called_strike": 1, "in_play_single": 1, "in_play_home_run": 2, "in_play_out": 5}
        assert rows["ball"]["nv"] == 1 and rows["in_play_home_run"]["nv"] == 1      # the walk-off rows carry no value
        assert rows["ball"]["c_ball"] == 2 and rows["ball"]["c_whiff"] == 0
        out = rows["in_play_out"]
        assert (out["nv"], out["g"], out["nnv"]) == (5, 5, 5) and out["sv"] == pytest.approx(-0.7) and out["ssv"] == pytest.approx(0.11)
        mean = out["sv"] / out["nv"]
        assert mean == pytest.approx(-0.14)
        assert cluster_mean_se(out["nv"], out["sv"], out["ssv"], out["snv"], out["nnv"], out["g"]) == pytest.approx(0.0244949, abs=1e-6)


def test_rate_terms_reject_unknown_rate():
    with pytest.raises(ValueError):
        rate_terms("nope")
