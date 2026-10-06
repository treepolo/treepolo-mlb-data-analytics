from seq_fixtures import make_seq_db, normalize, run_both
from treepolo_mlb_data.analysis import (
    Binary, Boolean, Column, Filter, Literal, NamedExpr, OrderKey, PITCH_GRAIN, Project, Sort, Source, node_from_dict, node_to_dict,
)
from treepolo_mlb_data.analysis.compiler import SQLCompiler


def uids(result):
    return [r["pitch_uid"] for r in result.rows]


def like_node(*patterns):
    base = Source("pitches", PITCH_GRAIN)
    terms = [Binary(Column("des"), "LIKE", Literal(pattern)) for pattern in patterns]
    predicate = terms[0] if len(terms) == 1 else Boolean("or", tuple(terms))
    return Sort(
        Project(Filter(base, predicate), (NamedExpr("pitch_uid", Column("pitch_uid")),), PITCH_GRAIN),
        (OrderKey(Column("pitch_uid")),),
    )


def test_like_is_case_insensitive_in_sqlite_but_not_duckdb_so_both_cases_are_listed(tmp_path):
    db = make_seq_db(tmp_path / "s.sqlite3")
    sqlite_result, duck_result = run_both(db, like_node("%bunt%"))
    assert uids(sqlite_result) == ["1:4:2", "1:5:1", "1:6:1"]  # SQLite also matches "Lee Bunt Ground Out"
    assert uids(duck_result) == ["1:4:2", "1:5:1"]             # DuckDB is case-sensitive
    # The two-pattern form used by the bunt rules gives the same answer on both backends.
    sqlite_result, duck_result = run_both(db, like_node("%bunt%", "%Bunt%"))
    assert uids(sqlite_result) == uids(duck_result) == ["1:4:2", "1:5:1", "1:6:1"]


def test_concat_works_on_both_backends(tmp_path):
    db = make_seq_db(tmp_path / "s.sqlite3")
    concat = Sort(
        Project(
            Filter(Source("pitches", PITCH_GRAIN), Binary(Column("game_pk"), "=", Literal(2))),
            (NamedExpr("pitch_uid", Column("pitch_uid")),
             NamedExpr("label", Binary(Binary(Column("pitch_type"), "||", Literal(">")), "||", Column("description")))),
            PITCH_GRAIN,
        ),
        (OrderKey(Column("pitch_uid")),),
    )
    sqlite_result, duck_result = run_both(db, concat)
    assert normalize(sqlite_result.rows) == normalize(duck_result.rows) == [{"pitch_uid": "2:1:1", "label": "FF>hit_into_play"}]


def test_new_operators_survive_serialization(tmp_path):
    for node in (like_node("%bunt%", "%Bunt%"),):
        again = node_from_dict(node_to_dict(node))
        assert SQLCompiler().compile(again).sql == SQLCompiler().compile(node).sql
