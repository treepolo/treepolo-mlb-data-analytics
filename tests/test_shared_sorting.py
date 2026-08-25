from __future__ import annotations

import sqlite3

from treepolo_mlb_data.web_analysis import AnalysisFacade


def make_db(path):
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE pitches (
        pitch_uid TEXT, game_pk INTEGER, at_bat_number INTEGER, pitch_number INTEGER,
        game_date TEXT, game_year INTEGER, pitcher INTEGER, batter INTEGER,
        pitch_type TEXT, release_speed REAL, description TEXT, zone INTEGER,
        p_throws TEXT, stand TEXT
    )""")
    rows = [
        ("a1", 1, 1, 1, "2026-04-01", 2026, 10, 101, "ST", 84, "called_strike", 5, "R", "R"),
        ("a2", 1, 1, 2, "2026-04-01", 2026, 10, 101, "ST", 85, "ball", 12, "R", "R"),
        ("a3", 1, 1, 3, "2026-04-01", 2026, 10, 101, "ST", 86, "swinging_strike", 6, "R", "R"),
        ("b1", 1, 2, 1, "2026-04-01", 2026, 10, 102, "ST", 82, "ball", 13, "R", "L"),
        ("b2", 1, 2, 2, "2026-04-01", 2026, 10, 102, "FF", 96, "foul", 4, "R", "L"),
        ("b3", 1, 2, 3, "2026-04-01", 2026, 10, 102, "ST", 83, "called_strike", 7, "R", "L"),
        ("b4", 1, 2, 4, "2026-04-01", 2026, 10, 102, "CH", 88, "ball", 11, "R", "L"),
        ("b5", 1, 2, 5, "2026-04-01", 2026, 10, 102, "ST", 84, "hit_into_play", 8, "R", "L"),
        ("c1", 2, 1, 1, "2026-04-02", 2026, 20, 103, "FF", 94, "ball", 11, "R", "R"),
        ("c2", 2, 1, 2, "2026-04-02", 2026, 20, 103, "FF", 95, "foul", 5, "R", "R"),
        ("c3", 2, 1, 3, "2026-04-02", 2026, 20, 103, "CH", 87, "called_strike", 3, "R", "R"),
        ("d1", 3, 1, 1, "2026-04-03", 2026, 20, 104, "FF", 96, "ball", 12, "R", "L"),
        ("d2", 3, 1, 2, "2026-04-03", 2026, 20, 104, "SL", 86, "swinging_strike", 6, "R", "L"),
    ]
    conn.executemany("INSERT INTO pitches VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    conn.commit(); conn.close()


def assert_desc(rows, field):
    values = [row[field] for row in rows if row[field] is not None]
    assert values == sorted(values, reverse=True)


def test_all_nine_modes_accept_shared_result_sort(tmp_path):
    path = tmp_path / "db.sqlite"
    make_db(path)
    facade = AnalysisFacade(path)

    basic = facade.analyze({
        "mode": "basic", "group_by": ["pitch_type"],
        "metrics": [{"function": "avg", "field": "release_speed"}],
        "result_sort": [{"field": "avg_release_speed", "descending": True}], "limit": 50,
    })
    assert_desc(basic["rows"], "avg_release_speed")

    sequence = facade.analyze({
        "mode": "sequence_pattern", "event": {"field": "pitch_type", "op": "eq", "value": "ST"},
        "occurrence": 1, "arrangement": "any",
        "result_sort": [{"field": "release_speed", "descending": True}],
    })
    assert_desc(sequence["rows"], "release_speed")

    follow = facade.analyze({
        "mode": "follow_event", "anchor": {"field": "pitch_type", "op": "eq", "value": "ST"},
        "target": {"field": "pitch_type", "op": "eq", "value": "ST"}, "max_gap": 4,
        "between": [{"field": "pitch_type", "op": "eq", "value": "FF"}],
        "result_sort": [{"field": "release_speed", "descending": True}],
    })
    assert_desc(follow["rows"], "release_speed")

    arsenal = facade.analyze({
        "mode": "arsenal", "entity_fields": ["pitcher"], "min_usage": 0.0,
        "result_sort": [{"field": "usage_rate", "descending": True}],
    })
    assert_desc(arsenal["rows"], "usage_rate")

    role = facade.analyze({
        "mode": "pitch_role", "entity_fields": ["pitcher"], "metric_kind": "usage_rate", "rank": 1,
        "result_sort": [{"field": "usage_rate", "descending": True}],
    })
    assert_desc(role["rows"], "usage_rate")

    temporal = facade.analyze({
        "mode": "temporal", "entity_fields": ["pitcher"], "period_field": "game_pk",
        "value_field": "release_speed", "function": "avg", "direction": "previous", "offset": 1,
        "result_sort": [{"field": "difference", "descending": True}],
    })
    assert_desc(temporal["rows"], "difference")

    percentile = facade.analyze({
        "mode": "percentile", "entity_fields": ["pitcher"], "value_field": "release_speed", "threshold": 0.0,
        "result_sort": [{"field": "percentile", "descending": True}],
    })
    assert_desc(percentile["rows"], "percentile")

    cross = facade.analyze({
        "mode": "cross_level", "unit_fields": ["pitcher", "game_pk"], "baseline_fields": ["pitcher"],
        "value_field": "release_speed", "function": "avg",
        "result_sort": [{"field": "difference", "descending": True}],
    })
    assert_desc(cross["rows"], "difference")

    change = facade.analyze({
        "mode": "arsenal_change", "entity_fields": ["pitcher"], "min_usage": 0.0,
        "period_a": {"start": "2026-04-01", "end": "2026-04-02"},
        "period_b": {"start": "2026-04-03", "end": "2026-04-03"},
        "result_sort": [{"field": "pitcher", "descending": True}],
    })
    for section in change["sections"]:
        assert_desc(section["rows"], "pitcher")


def test_shared_result_sort_supports_multiple_keys(tmp_path):
    path = tmp_path / "db.sqlite"
    make_db(path)
    result = AnalysisFacade(path).analyze({
        "mode": "basic", "group_by": ["pitcher", "pitch_type"],
        "metrics": [{"function": "count", "field": ""}],
        "result_sort": [
            {"field": "pitcher", "descending": True},
            {"field": "row_count", "descending": True},
        ],
        "limit": 100,
    })
    keys = [(row["pitcher"], row["row_count"]) for row in result["rows"]]
    assert keys == sorted(keys, reverse=True)
