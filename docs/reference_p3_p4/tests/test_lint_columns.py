from __future__ import annotations

from pitch_fixtures import COLUMNS
from treepolo_mlb_data.analysis import Column, NamedExpr, PITCH_GRAIN, Project, Source
from treepolo_mlb_data.analysis.lint import lint_columns
from treepolo_mlb_data.analysis.pitch_table import build_pitch_table


def test_lint_flags_unknown_columns_and_accepts_good_nodes():
    source = Source("pitches", PITCH_GRAIN)
    good = Project(source, (NamedExpr("pitch_uid", Column("pitch_uid")), NamedExpr("x", Column("plate_x"))), PITCH_GRAIN)
    assert lint_columns(good, COLUMNS) == []
    bad = Project(source, (NamedExpr("pitch_uid", Column("pitch_uid")), NamedExpr("x", Column("no_such_column"))), PITCH_GRAIN)
    problems = lint_columns(bad, COLUMNS)
    assert len(problems) == 1 and "no_such_column" in problems[0]


def test_lint_catches_a_column_the_upstream_project_dropped():
    table = build_pitch_table(Source("pitches", PITCH_GRAIN), memory=1)
    node = Project(table, (NamedExpr("pitch_uid", Column("pitch_uid")), NamedExpr("x", Column("prev2_pitch_type"))), PITCH_GRAIN)
    assert any("prev2_pitch_type" in p for p in lint_columns(node, COLUMNS))
