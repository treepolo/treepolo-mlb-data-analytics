import sqlite3

from treepolo_mlb_data.analysis import (
    Aggregate, AnalysisEngine, Binary, Case, Column, EventPattern, Filter,
    FollowEvent, Grain, Join, Literal, Metric, NamedExpr, OrderKey, PITCH_GRAIN,
    Project, SetOperation, Source, Window, WindowField, arsenal_table,
    empirical_percentile, node_from_dict, node_to_dict, pitch_usage,
    rank_pitch_roles,
)


def make_db(path):
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE pitches (
        pitch_uid TEXT, game_pk INTEGER, at_bat_number INTEGER, pitch_number INTEGER,
        game_date TEXT, pitcher INTEGER, batter INTEGER, pitch_type TEXT,
        release_speed REAL, description TEXT
    )""")
    rows = [
        ("a1", 1, 1, 1, "2026-04-01", 10, 101, "ST", 84, "called_strike"),
        ("a2", 1, 1, 2, "2026-04-01", 10, 101, "ST", 85, "ball"),
        ("a3", 1, 1, 3, "2026-04-01", 10, 101, "ST", 86, "swinging_strike"),
        ("b1", 1, 2, 1, "2026-04-01", 10, 102, "ST", 82, "ball"),
        ("b2", 1, 2, 2, "2026-04-01", 10, 102, "FF", 96, "foul"),
        ("b3", 1, 2, 3, "2026-04-01", 10, 102, "ST", 83, "called_strike"),
        ("b4", 1, 2, 4, "2026-04-01", 10, 102, "CH", 88, "ball"),
        ("b5", 1, 2, 5, "2026-04-01", 10, 102, "ST", 84, "hit_into_play"),
        ("c1", 2, 1, 1, "2026-04-02", 20, 103, "ST", 83, "ball"),
        ("c2", 2, 1, 2, "2026-04-02", 20, 103, "ST", 84, "foul"),
        ("c3", 2, 1, 3, "2026-04-02", 20, 103, "FF", 95, "ball"),
        ("c4", 2, 1, 4, "2026-04-02", 20, 103, "ST", 85, "swinging_strike"),
        ("d1", 3, 1, 1, "2026-04-03", 20, 104, "FF", 94, "ball"),
        ("d2", 3, 1, 2, "2026-04-03", 20, 104, "FF", 95, "foul"),
        ("d3", 3, 1, 3, "2026-04-03", 20, 104, "CH", 87, "called_strike"),
        ("e1", 4, 1, 1, "2026-04-04", 20, 105, "FF", 96, "ball"),
        ("e2", 4, 1, 2, "2026-04-04", 20, 105, "FF", 97, "foul"),
        ("e3", 4, 1, 3, "2026-04-04", 20, 105, "SL", 86, "swinging_strike"),
    ]
    conn.executemany("INSERT INTO pitches VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
    conn.commit(); conn.close()


def pa_partition():
    return (NamedExpr("game_pk", Column("game_pk")), NamedExpr("at_bat_number", Column("at_bat_number")))


def test_01_sweeper_extreme_patterns(tmp_path):
    path = tmp_path / "db.sqlite"; make_db(path); src = Source("pitches", PITCH_GRAIN)
    common = dict(source=src, partition_by=pa_partition(), order_by=(OrderKey(Column("pitch_number")),),
                  event=Binary(Column("pitch_type"), "=", Literal("ST")), occurrence=3,
                  exact_count=3, require_last_event=True)
    consecutive = AnalysisEngine(path).execute(EventPattern(**common, arrangement="consecutive"))
    separated = AnalysisEngine(path).execute(EventPattern(**common, arrangement="none_adjacent"))
    assert [r["pitch_uid"] for r in consecutive.rows] == ["a3"]
    assert [r["pitch_uid"] for r in separated.rows] == ["b5"]


def test_02_arsenal_group_and_relative_pitch_role(tmp_path):
    path = tmp_path / "db.sqlite"; make_db(path); src = Source("pitches", PITCH_GRAIN)
    usage = pitch_usage(src)
    arsenal = arsenal_table(usage, min_usage=0.05)
    ranked = rank_pitch_roles(usage)
    combined = Join(
        ranked, arsenal,
        Binary(Column("pitcher", "left"), "=", Column("pitcher", "right")),
        (
            NamedExpr("pitcher", Column("pitcher", "left")),
            NamedExpr("pitch_type", Column("pitch_type", "left")),
            NamedExpr("usage_rate", Column("usage_rate", "left")),
            NamedExpr("role_rank", Column("role_rank", "left")),
            NamedExpr("arsenal", Column("arsenal", "right")),
        ), Grain(("pitcher", "pitch_type"), "pitcher_pitch_type"),
    )
    ff = Project(
        Filter(combined, Binary(Column("pitch_type"), "=", Literal("FF"))),
        (
            NamedExpr("pitcher", Column("pitcher")), NamedExpr("arsenal", Column("arsenal")),
            NamedExpr("ff_primary", Case(((Binary(Column("role_rank"), "=", Literal(1)), Literal(1)),), Literal(0))),
            NamedExpr("ff_usage", Column("usage_rate")),
        ), Grain(("pitcher",), "pitcher"),
    )
    secondary = Project(
        Filter(combined, Binary(Column("role_rank"), "=", Literal(2))),
        (
            NamedExpr("pitcher", Column("pitcher")), NamedExpr("secondary_pitch", Column("pitch_type")),
            NamedExpr("secondary_usage", Column("usage_rate")),
        ), Grain(("pitcher",), "pitcher"),
    )
    compared = Join(
        ff, secondary,
        Binary(Column("pitcher", "left"), "=", Column("pitcher", "right")),
        (
            NamedExpr("pitcher", Column("pitcher", "left")), NamedExpr("arsenal", Column("arsenal", "left")),
            NamedExpr("ff_primary", Column("ff_primary", "left")), NamedExpr("secondary_pitch", Column("secondary_pitch", "right")),
            NamedExpr("usage_gap", Binary(Column("ff_usage", "left"), "-", Column("secondary_usage", "right"))),
        ), Grain(("pitcher",), "pitcher"),
    )
    rows = AnalysisEngine(path).execute(compared).rows
    by_pitcher = {r["pitcher"]: r for r in rows}
    assert by_pitcher[20]["ff_primary"] == 1
    assert by_pitcher[20]["secondary_pitch"] in {"ST", "SL", "CH"}
    assert "|" in by_pitcher[20]["arsenal"]
    assert by_pitcher[10]["ff_primary"] == 0


def test_03_lag_across_games(tmp_path):
    path = tmp_path / "db.sqlite"; make_db(path); src = Source("pitches", PITCH_GRAIN)
    game_ff = Aggregate(
        Filter(src, Binary(Column("pitch_type"), "=", Literal("FF"))),
        (NamedExpr("pitcher", Column("pitcher")), NamedExpr("game_pk", Column("game_pk"))),
        (Metric("avg_ff_velo", "avg", Column("release_speed")),),
        Grain(("pitcher", "game_pk"), "pitcher_game"),
    )
    lagged = Window(game_ff, (WindowField("prev_velo", "lag", (Column("avg_ff_velo"),), (Column("pitcher"),), (OrderKey(Column("game_pk")),)),))
    rows = AnalysisEngine(path).execute(lagged).rows
    p20 = sorted((r for r in rows if r["pitcher"] == 20), key=lambda r: r["game_pk"])
    assert p20[-1]["prev_velo"] == p20[-2]["avg_ff_velo"]


def test_04_dynamic_reference_pitch_by_rank(tmp_path):
    path = tmp_path / "db.sqlite"; make_db(path)
    ranked = rank_pitch_roles(pitch_usage(Source("pitches", PITCH_GRAIN)))
    second = Filter(ranked, Binary(Column("role_rank"), "=", Literal(2)))
    rows = AnalysisEngine(path).execute(second).rows
    assert {r["pitcher"] for r in rows} == {10, 20}


def test_05_nested_percentile_grouping(tmp_path):
    path = tmp_path / "db.sqlite"; make_db(path)
    usage = pitch_usage(Source("pitches", PITCH_GRAIN))
    ranked = empirical_percentile(usage, value_field="usage_rate", alias="usage_percentile", partition_fields=("pitcher",))
    rows = AnalysisEngine(path).execute(ranked).rows
    assert all(0 < r["usage_percentile"] <= 1 for r in rows)


def test_06_variable_gap_follow_event(tmp_path):
    path = tmp_path / "db.sqlite"; make_db(path); src = Source("pitches", PITCH_GRAIN)
    node = FollowEvent(
        src, pa_partition(), (OrderKey(Column("pitch_number")),),
        Binary(Column("pitch_type"), "=", Literal("ST")), Binary(Column("pitch_type"), "=", Literal("ST")),
        3, (NamedExpr("ff_between", Binary(Column("pitch_type"), "=", Literal("FF"))),),
    )
    rows = AnalysisEngine(path).execute(node).rows
    pairs = {(r["game_pk"], r["at_bat_number"], r["pitch_uid"]): r["ff_between"] for r in rows}
    assert pairs[(1, 2, "b3")] == 1


def test_07_cross_grain_join(tmp_path):
    path = tmp_path / "db.sqlite"; make_db(path); src = Source("pitches", PITCH_GRAIN)
    ff = Filter(src, Binary(Column("pitch_type"), "=", Literal("FF")))
    season = Aggregate(ff, (NamedExpr("pitcher", Column("pitcher")),), (Metric("season_velo", "avg", Column("release_speed")),), Grain(("pitcher",), "pitcher"))
    games = Aggregate(ff, (NamedExpr("pitcher", Column("pitcher")), NamedExpr("game_pk", Column("game_pk"))), (Metric("game_velo", "avg", Column("release_speed")),), Grain(("pitcher", "game_pk"), "pitcher_game"))
    joined = Join(games, season, Binary(Column("pitcher", "left"), "=", Column("pitcher", "right")), (
        NamedExpr("pitcher", Column("pitcher", "left")), NamedExpr("game_pk", Column("game_pk", "left")),
        NamedExpr("game_velo", Column("game_velo", "left")), NamedExpr("season_velo", Column("season_velo", "right")),
        NamedExpr("delta", Binary(Column("game_velo", "left"), "-", Column("season_velo", "right"))),
    ), Grain(("pitcher", "game_pk"), "pitcher_game"))
    rows = AnalysisEngine(path).execute(joined).rows
    assert any(r["delta"] != 0 for r in rows if r["pitcher"] == 20)


def test_08_set_difference_for_changed_arsenal(tmp_path):
    path = tmp_path / "db.sqlite"; make_db(path); src = Source("pitches", PITCH_GRAIN)
    first_usage = pitch_usage(Filter(src, Binary(Column("game_pk"), "<=", Literal(2))))
    second_usage = pitch_usage(Filter(src, Binary(Column("game_pk"), ">", Literal(2))))
    fields = (NamedExpr("pitcher", Column("pitcher")), NamedExpr("pitch_type", Column("pitch_type")))
    grain = Grain(("pitcher", "pitch_type"), "pitcher_pitch_type")
    first_set = Project(first_usage, fields, grain)
    second_set = Project(second_usage, fields, grain)
    newly_added = SetOperation(second_set, first_set, "except")
    rows = AnalysisEngine(path).execute(newly_added).rows
    assert {r["pitch_type"] for r in rows if r["pitcher"] == 20} >= {"CH", "SL"}


def test_09_entity_specific_percentile_threshold(tmp_path):
    path = tmp_path / "db.sqlite"; make_db(path); src = Source("pitches", PITCH_GRAIN)
    ff = Filter(src, Binary(Column("pitch_type"), "=", Literal("FF")))
    pct = empirical_percentile(ff, value_field="release_speed", alias="velo_pct", partition_fields=("pitcher",))
    high = Filter(pct, Binary(Column("velo_pct"), ">=", Literal(0.8)))
    rows = AnalysisEngine(path).execute(high).rows
    assert rows and all(r["release_speed"] >= 95 for r in rows)


def test_10_new_nodes_roundtrip_serialization():
    node = EventPattern(
        Source("pitches", PITCH_GRAIN), pa_partition(), (OrderKey(Column("pitch_number")),),
        Binary(Column("pitch_type"), "=", Literal("ST")), 3, 3, True, "none_adjacent",
    )
    assert node_from_dict(node_to_dict(node)) == node
