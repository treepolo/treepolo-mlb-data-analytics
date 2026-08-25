import sqlite3
from pathlib import Path

import pytest

from treepolo_mlb_data.analysis_state import AnalysisStateStore, analysis_cache_key
from treepolo_mlb_data.config import AppConfig
from treepolo_mlb_data.web_analysis import AnalysisFacade, RequestError
from treepolo_mlb_data.webapp import AppServices, STATIC_DIR


def make_db(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL);
        INSERT INTO settings VALUES ('data_revision','rev-1','now');
        CREATE TABLE pitches (
            pitch_uid TEXT, game_pk INTEGER, at_bat_number INTEGER, pitch_number INTEGER,
            game_date TEXT, game_year INTEGER, pitcher INTEGER, batter INTEGER,
            pitch_type TEXT, release_speed REAL, description TEXT, zone INTEGER,
            p_throws TEXT, stand TEXT
        );
    """)
    conn.executemany(
        "INSERT INTO pitches VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            ("a", 1, 1, 1, "2026-04-01", 2026, 10, 101, "FF", 95.0, "ball", 11, "R", "R"),
            ("b", 1, 1, 2, "2026-04-01", 2026, 10, 101, "FF", 97.0, "foul", 5, "R", "R"),
        ],
    )
    conn.commit(); conn.close()


def config_for(tmp_path: Path) -> AppConfig:
    return AppConfig(
        data_dir=str(tmp_path),
        database_name="statcast.sqlite3",
        analytics_database_name="statcast.duckdb",
        analysis_state_database_name="analysis_state.sqlite3",
        analysis_backend="sqlite",
    )


def test_basic_non_count_metric_requires_field(tmp_path):
    path = tmp_path / "db.sqlite"; make_db(path)
    facade = AnalysisFacade(path)
    with pytest.raises(RequestError, match="Average requires a metric field"):
        facade.analyze({"mode": "basic", "metrics": [{"function": "avg", "field": ""}]})


def test_persistent_result_cache_and_history(tmp_path):
    config = config_for(tmp_path); make_db(config.database_path)
    services = AppServices(config)
    payload = {
        "mode": "basic", "group_by": ["pitch_type"],
        "metrics": [{"function": "avg", "field": "release_speed"}], "limit": 20,
    }
    try:
        first = services.analyze(payload)
        second = services.analyze(payload)
        assert first["cache"]["hit"] is False
        assert first["cache"]["stored"] is True
        assert second["cache"]["hit"] is True
        assert second["rows"] == first["rows"]
        history = services.history()
        assert len(history) == 2
        assert history[0]["status"] == "success"
        restored = services.history_item(history[0]["id"])
        assert restored["result_available"] is True
        assert restored["result"]["rows"] == first["rows"]
    finally:
        services.analysis_state.close()


def test_data_revision_invalidates_cache_key():
    payload = {"mode": "basic", "group_by": ["pitch_type"]}
    assert analysis_cache_key(payload=payload, data_revision="a", backend="duckdb") != analysis_cache_key(
        payload=payload, data_revision="b", backend="duckdb"
    )


def test_saved_analysis_round_trip(tmp_path):
    path = tmp_path / "state.sqlite"
    payload = {"mode": "basic", "group_by": ["game_year"]}
    with AnalysisStateStore(path) as store:
        item = store.save_analysis(name="Season velocity", payload=payload, notes="test", cache_key="abc", data_revision="rev")
        assert item["name"] == "Season velocity"
        listed = store.list_saved()
        assert listed[0]["payload"] == payload
        updated = store.update_saved(item["id"], name="Season FF velocity")
        assert updated["name"] == "Season FF velocity"
        assert store.delete_saved(item["id"]) is True
        assert store.list_saved() == []


def test_stage4_ui_guard_and_library_are_loaded():
    controls = (STATIC_DIR / "stage4-controls.js").read_text(encoding="utf-8")
    webapp = Path(__import__("treepolo_mlb_data.webapp", fromlist=["__file__"]).__file__).read_text(encoding="utf-8")
    assert "Average" in controls and "必須指定計算欄位" in controls
    assert "分析紀錄 Analysis Library" in controls
    assert "/api/analysis/saved" in controls
    assert "stage4-controls.js" in webapp
