import sqlite3
from pathlib import Path

import pytest

from treepolo_mlb_data.analysis import (
    Column, Grain, OrderKey, PITCH_GRAIN, Source, Window, WindowField,
    WindowFrame, node_from_dict, node_to_dict,
)
from treepolo_mlb_data.web_analysis import AnalysisFacade
from treepolo_mlb_data.webapp import STATIC_DIR


def make_workflow_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE pitches (
        pitch_uid TEXT PRIMARY KEY,
        game_pk INTEGER,
        at_bat_number INTEGER,
        pitch_number INTEGER,
        game_date TEXT,
        game_year INTEGER,
        pitcher INTEGER,
        batter INTEGER,
        pitch_type TEXT,
        release_speed REAL,
        description TEXT,
        zone INTEGER,
        p_throws TEXT,
        stand TEXT
    )""")
    rows = []
    uid = 0
    # Pitcher 10 has FF usage 1/4 -> 2/4 -> 3/4 -> 2/4 across four games.
    # Average velocity is 90, 91, 92, 99, so the third game's next value is 99.
    for game_pk, ff_count, velo in ((1, 1, 90.0), (2, 2, 91.0), (3, 3, 92.0), (4, 2, 99.0)):
        for pitch_no in range(1, 5):
            uid += 1
            pitch_type = "FF" if pitch_no <= ff_count else "CH"
            rows.append((
                f"p{uid}", game_pk, 1, pitch_no, f"2026-04-{game_pk:02d}", 2026,
                10, 100 + game_pk, pitch_type, velo, "ball", 11, "R", "R",
            ))
    conn.executemany("INSERT INTO pitches VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()


def test_workflow_builds_usage_rate_consecutive_trend_and_next_period(tmp_path):
    path = tmp_path / "workflow.sqlite"
    make_workflow_db(path)
    facade = AnalysisFacade(path, backend="sqlite")
    result = facade.analyze({
        "mode": "workflow",
        "stages": [
            {
                "kind": "aggregate",
                "group_by": ["pitcher", "game_pk"],
                "metrics": [
                    {"function": "count", "alias": "total_count"},
                    {
                        "function": "count", "alias": "ff_count",
                        "condition": {"field": "pitch_type", "op": "eq", "value": "FF"},
                    },
                    {"function": "avg", "field": "release_speed", "alias": "avg_velo"},
                ],
            },
            {
                "kind": "derive", "alias": "ff_usage", "left": "ff_count",
                "operator": "/", "right_field": "total_count",
            },
            {
                "kind": "trend", "alias": "usage_rising", "field": "ff_usage",
                "direction": "up", "periods": 3, "partition_by": ["pitcher"],
                "order_by": [{"field": "game_pk", "descending": False}], "strict": True,
            },
            {
                "kind": "offset", "alias": "next_velo", "field": "avg_velo",
                "direction": "lead", "offset": 1, "partition_by": ["pitcher"],
                "order_by": [{"field": "game_pk", "descending": False}],
            },
            {"kind": "filter", "field": "usage_rising", "op": "eq", "value": 1},
            {
                "kind": "project",
                "fields": ["pitcher", "game_pk", "ff_usage", "avg_velo", "next_velo", "usage_rising"],
            },
        ],
        "limit": 50,
    })
    assert result["backend"] == "sqlite"
    assert result["row_count"] == 1
    row = result["rows"][0]
    assert row["pitcher"] == 10
    assert row["game_pk"] == 3
    assert row["ff_usage"] == pytest.approx(0.75)
    assert row["avg_velo"] == pytest.approx(92.0)
    assert row["next_velo"] == pytest.approx(99.0)
    assert row["usage_rising"] == 1


def test_workflow_rolling_and_last_selection(tmp_path):
    path = tmp_path / "workflow.sqlite"
    make_workflow_db(path)
    facade = AnalysisFacade(path, backend="sqlite")
    result = facade.analyze({
        "mode": "workflow",
        "stages": [
            {
                "kind": "aggregate",
                "group_by": ["pitcher", "game_pk"],
                "metrics": [{"function": "avg", "field": "release_speed", "alias": "avg_velo"}],
            },
            {
                "kind": "rolling", "alias": "rolling_3", "function": "avg", "field": "avg_velo",
                "window_size": 3, "partition_by": ["pitcher"],
                "order_by": [{"field": "game_pk", "descending": False}],
            },
            {
                "kind": "nth", "n": 1, "from_end": True, "partition_by": ["pitcher"],
                "order_by": [{"field": "game_pk", "descending": False}],
            },
        ],
        "limit": 50,
    })
    assert result["row_count"] == 1
    row = result["rows"][0]
    assert row["game_pk"] == 4
    assert row["rolling_3"] == pytest.approx((91 + 92 + 99) / 3)


def test_window_frame_round_trip_codec():
    node = Window(
        Source("pitches", PITCH_GRAIN),
        (WindowField(
            "rolling_velo", "avg", (Column("release_speed"),),
            (Column("pitcher"),), (OrderKey(Column("game_pk")),), WindowFrame(-2, 0),
        ),),
    )
    assert node_from_dict(node_to_dict(node)) == node


def test_stage4_research_pages_are_served_by_main_ui():
    webapp = Path(__import__("treepolo_mlb_data.webapp", fromlist=["__file__"]).__file__).read_text(encoding="utf-8")
    pages = (STATIC_DIR / "stage4-analysis-pages.js").read_text(encoding="utf-8")
    assert "stage4-analysis-pages.js" in webapp
    for label in (
        "研究工作流 Research Workflow",
        "自動分群 Clustering",
        "迴歸分析 Regression",
        "Bootstrap / 信賴區間 Confidence Interval",
    ):
        assert label in pages
