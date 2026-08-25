import sqlite3
from pathlib import Path

from treepolo_mlb_data.config import AppConfig
from treepolo_mlb_data.fast_status import (
    prepare_fast_status,
    read_fast_status,
    rebuild_fast_status,
    update_fast_status_after_ingest,
)
from treepolo_mlb_data.storage import StatcastStore
from treepolo_mlb_data.web_analysis import AnalysisFacade
from treepolo_mlb_data.webapp import AppServices, STATIC_DIR


def _legacy_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE pitches (pitch_uid TEXT PRIMARY KEY, game_pk INTEGER, game_date TEXT, pitch_type TEXT)")
    conn.executemany(
        "INSERT INTO pitches VALUES (?,?,?,?)",
        [
            ("1:1:1", 1, "2024-04-01", "FF"),
            ("1:1:2", 1, "2024-04-01", "SL"),
            ("2:1:1", 2, "2024-04-02", "CH"),
            ("fallback:abc", 2, "2024-04-02", "FF"),
        ],
    )
    conn.commit()
    conn.close()


def test_new_database_fast_status_starts_ready(tmp_path: Path):
    path = tmp_path / "db.sqlite"
    with StatcastStore(path):
        pass
    assert prepare_fast_status(path) is False
    status = read_fast_status(path)
    assert status["summary_state"] == "ready"
    assert status["pitch_rows"] == 0
    assert status["games"] == 0


def test_legacy_database_bootstraps_once_and_persists(tmp_path: Path):
    path = tmp_path / "db.sqlite"
    _legacy_db(path)
    assert prepare_fast_status(path) is True
    pending = read_fast_status(path)
    assert pending["summary_state"] == "pending"

    status = rebuild_fast_status(path)
    assert status["summary_state"] == "ready"
    assert status["pitch_rows"] == 4
    assert status["games"] == 2
    assert status["latest_game_date"] == "2024-04-02"
    assert status["missing_natural_key"] == 1

    # Once persisted, later startups do not require another full-table bootstrap.
    assert prepare_fast_status(path) is False
    assert read_fast_status(path)["pitch_rows"] == 4


def test_incremental_ingest_updates_cached_counts_without_full_rescan(tmp_path: Path):
    path = tmp_path / "db.sqlite"
    with StatcastStore(path):
        pass
    prepare_fast_status(path)
    payload = (
        "game_pk,game_date,pitch_type\n"
        "10,2026-08-24,FF\n"
        "10,2026-08-24,SL\n"
        "11,2026-08-25,CH\n"
    ).encode()
    update_fast_status_after_ingest(path, payload, inserted=3)
    status = read_fast_status(path)
    assert status["pitch_rows"] == 3
    assert status["games"] == 2
    assert status["latest_game_date"] == "2026-08-25"
    assert status["integrity_stale"] is True


def test_ui_status_does_not_call_deep_verify(tmp_path: Path, monkeypatch):
    cfg = AppConfig(data_dir=str(tmp_path))
    with StatcastStore(cfg.database_path):
        pass
    prepare_fast_status(cfg.database_path)

    def forbidden_verify(self):
        raise AssertionError("ordinary status must not run deep verify")

    monkeypatch.setattr(StatcastStore, "verify", forbidden_verify)
    status = AppServices(cfg).status()
    assert status["summary_state"] == "ready"
    assert status["pitch_rows"] == 0


def test_meta_does_not_scan_distinct_pitch_values(tmp_path: Path):
    path = tmp_path / "db.sqlite"
    _legacy_db(path)
    meta = AnalysisFacade(path).meta()
    assert meta["ready"] is True
    assert meta["choices"] == {}
    assert "pitch_type" in {field["name"] for field in meta["fields"]}


def test_fast_status_frontend_is_packaged_and_bilingual():
    js = (STATIC_DIR / "fast-status.js").read_text(encoding="utf-8")
    assert "正在背景建立快速摘要" in js
    assert "/api/data/status" in js
