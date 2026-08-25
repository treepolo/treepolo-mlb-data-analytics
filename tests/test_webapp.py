import sqlite3
from pathlib import Path

import pytest

from treepolo_mlb_data.web_analysis import AnalysisFacade
from treepolo_mlb_data.webapp import STATIC_DIR


def make_db(path: Path):
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
    conn.commit()
    conn.close()


def test_meta_and_basic_analysis(tmp_path):
    path = tmp_path / "db.sqlite"; make_db(path)
    facade = AnalysisFacade(path)
    meta = facade.meta()
    assert meta["ready"] is True
    assert "pitch_type" in {field["name"] for field in meta["fields"]}
    result = facade.analyze({
        "mode": "basic",
        "filters": [{"field": "pitch_type", "op": "in", "value": ["FF", "ST"]}],
        "group_by": ["pitch_type"],
        "metrics": [{"function": "count", "field": ""}, {"function": "avg", "field": "release_speed"}],
        "limit": 20,
    })
    by_type = {row["pitch_type"]: row for row in result["rows"]}
    assert by_type["FF"]["row_count"] == 4
    assert by_type["ST"]["row_count"] == 6


def test_basic_statistics_distinct_nulls_and_metric_sorting(tmp_path):
    path = tmp_path / "db.sqlite"; make_db(path)
    conn = sqlite3.connect(path)
    conn.execute(
        "INSERT INTO pitches VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("n1", 4, 1, 1, "2026-04-04", 2026, 30, 105, "FF", None, "ball", 12, "R", "R"),
    )
    conn.commit(); conn.close()
    facade = AnalysisFacade(path)

    result = facade.analyze({
        "mode": "basic",
        "group_by": ["pitch_type"],
        "metrics": [
            {"function": "avg", "field": "release_speed"},
            {"function": "median", "field": "release_speed"},
            {"function": "stddev_pop", "field": "release_speed"},
            {"function": "stddev_samp", "field": "release_speed"},
        ],
        "sort": {"field": "avg_release_speed", "descending": True},
        "limit": 20,
    })
    assert [row["pitch_type"] for row in result["rows"]] == ["FF", "CH", "SL", "ST"]
    ff = result["rows"][0]
    assert ff["avg_release_speed"] == pytest.approx(95.25)
    assert ff["median_release_speed"] == pytest.approx(95.5)
    assert ff["stddev_pop_release_speed"] == pytest.approx(0.8291561976)
    assert ff["stddev_samp_release_speed"] == pytest.approx(0.9574271078)

    distinct = facade.analyze({
        "mode": "basic",
        "filters": [{"field": "pitch_type", "op": "eq", "value": "FF"}],
        "metrics": [
            {"function": "median", "field": "release_speed", "distinct": True},
            {"function": "stddev_pop", "field": "release_speed", "distinct": True},
        ],
        "sort": {"field": "median_release_speed", "descending": True},
        "limit": 20,
    })
    assert distinct["rows"][0]["median_release_speed"] == pytest.approx(95.0)
    assert distinct["rows"][0]["stddev_pop_release_speed"] == pytest.approx(0.8164965809)

    duplicate_alias = facade.analyze({
        "mode": "basic",
        "group_by": ["pitch_type"],
        "metrics": [
            {"function": "avg", "field": "release_speed"},
            {"function": "avg", "field": "release_speed"},
        ],
        "sort": {"field": "avg_release_speed_2", "descending": True},
        "limit": 1,
    })
    assert duplicate_alias["columns"] == ["pitch_type", "avg_release_speed", "avg_release_speed_2"]
    assert duplicate_alias["rows"][0]["pitch_type"] == "FF"


def test_sequence_pattern_and_follow_event(tmp_path):
    path = tmp_path / "db.sqlite"; make_db(path)
    facade = AnalysisFacade(path)
    result = facade.analyze({
        "mode": "sequence_pattern",
        "event": {"field": "pitch_type", "op": "eq", "value": "ST"},
        "occurrence": 3,
        "exact_count": 3,
        "require_last_event": True,
        "arrangement": "consecutive",
    })
    assert [row["pitch_uid"] for row in result["rows"]] == ["a3"]
    follow = facade.analyze({
        "mode": "follow_event",
        "anchor": {"field": "pitch_type", "op": "eq", "value": "ST"},
        "target": {"field": "pitch_type", "op": "eq", "value": "ST"},
        "max_gap": 3,
        "between": [{"field": "pitch_type", "op": "eq", "value": "FF"}],
    })
    assert any(row["pitch_uid"] == "b3" and row["between_1"] == 1 for row in follow["rows"])


def test_arsenal_role_percentile_and_cross_level(tmp_path):
    path = tmp_path / "db.sqlite"; make_db(path)
    facade = AnalysisFacade(path)
    arsenal = facade.analyze({"mode": "arsenal", "entity_fields": ["pitcher"], "min_usage": 0.05})
    assert any(row["pitcher"] == 10 and "ST" in row["arsenal"] for row in arsenal["rows"])
    role = facade.analyze({
        "mode": "pitch_role", "entity_fields": ["pitcher"], "metric_kind": "usage_rate",
        "rank": 1, "descending": True, "tie_method": "dense_rank",
    })
    assert {row["pitcher"] for row in role["rows"]} == {10, 20}
    percentile = facade.analyze({
        "mode": "percentile", "entity_fields": ["pitcher"], "value_field": "release_speed",
        "threshold": 0.8, "side": "high",
    })
    assert percentile["rows"]
    cross = facade.analyze({
        "mode": "cross_level", "unit_fields": ["pitcher", "game_pk"], "baseline_fields": ["pitcher"],
        "value_field": "release_speed", "function": "avg",
    })
    assert any(row["pitcher"] == 20 and row["difference"] != 0 for row in cross["rows"])


def test_arsenal_change_and_temporal(tmp_path):
    path = tmp_path / "db.sqlite"; make_db(path)
    facade = AnalysisFacade(path)
    change = facade.analyze({
        "mode": "arsenal_change", "entity_fields": ["pitcher"], "min_usage": 0.05,
        "period_a": {"start": "2026-04-01", "end": "2026-04-02"},
        "period_b": {"start": "2026-04-03", "end": "2026-04-03"},
    })
    added = change["sections"][0]["rows"]
    assert any(row["pitcher"] == 20 and row["pitch_type"] == "SL" for row in added)
    temporal = facade.analyze({
        "mode": "temporal", "entity_fields": ["pitcher"], "period_field": "game_pk",
        "value_field": "release_speed", "function": "avg", "direction": "previous", "offset": 1,
    })
    assert any(row["pitcher"] == 20 and row["reference_value"] is not None for row in temporal["rows"])


def test_static_ui_is_bilingual_and_chart_free():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    css = (STATIC_DIR / "app.css").read_text(encoding="utf-8")
    js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    assert "基本分析 Basic Analysis" in html
    assert "球種武器庫 Pitch Arsenal" in html
    assert "Windows XP/7" not in html
    assert "<canvas" not in html.lower()
    assert "new Chart" not in js
    assert "title-bar" in css and "window-buttons" in css
    assert "field-checklist" in js and "field-check-item" in css
    assert "中位數 Median" in js
    assert "母體標準差 Population SD" in js
    assert "樣本標準差 Sample SD" in js
    assert "metricOutputSpecs" in js
