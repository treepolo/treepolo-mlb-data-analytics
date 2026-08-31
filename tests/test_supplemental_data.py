from __future__ import annotations

import sqlite3

from treepolo_mlb_data.config import AppConfig
from treepolo_mlb_data.supplemental_data import (
    SupplementalClient,
    SupplementalStore,
    handle_supplemental_action,
)


class FakeResponse:
    def __init__(self, content: bytes, content_type: str):
        self.content = content
        self.headers = {"Content-Type": content_type, "Last-Modified": "Mon, 31 Aug 2026 00:00:00 GMT"}


def config(tmp_path):
    return AppConfig(data_dir=str(tmp_path / "data"), request_pause_seconds=0, request_retries=0)


def seed_pitcher(cfg: AppConfig, pitcher: int = 453286):
    cfg.root.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(cfg.database_path)
    conn.execute("CREATE TABLE IF NOT EXISTS pitches (pitcher REAL)")
    conn.execute("INSERT INTO pitches(pitcher) VALUES(?)", (pitcher,))
    conn.commit()
    conn.close()


def test_pitch3d_backfill_resume_verify_and_rebuild(tmp_path, monkeypatch):
    cfg = config(tmp_path)
    seed_pitcher(cfg)
    payload = (
        "game_pk,play_id,pitcher,api_pitch_type,api_p_release_speed,polynomial_x_1\n"
        "1,abc,453286,FC,88.1,1.25\n"
        "1,def,453286,SL,86.5,1.30\n"
    ).encode()
    monkeypatch.setattr(
        SupplementalClient,
        "pitch3d",
        lambda self, pitcher, dataset: FakeResponse(payload, "text/csv; charset=utf-8"),
    )

    first = handle_supplemental_action(
        cfg, "supplemental-run",
        {"source": "pitch3d", "dataset": "mlb", "mode": "backfill", "pitcher_ids": [453286]},
    )
    assert first["status"] == "success"
    assert first["rows_received"] == 2

    status = handle_supplemental_action(cfg, "supplemental-status", {"source": "pitch3d", "dataset": "mlb"})
    assert status["pitch3d_mlb_rows"] == 2
    assert status["pitch3d_mlb_pitchers"] == 1

    with SupplementalStore(cfg) as store:
        columns = {row[1]: row[2] for row in store.conn.execute("PRAGMA table_info(pitch3d_pitches)")}
        assert "api_p_release_speed" in columns
        assert columns["api_p_release_speed"] == "REAL"
        assert "polynomial_x_1" in columns
        stored = store.conn.execute(
            "SELECT api_pitch_type,api_p_release_speed FROM pitch3d_pitches ORDER BY row_key"
        ).fetchall()
        assert len(stored) == 2

    second = handle_supplemental_action(
        cfg, "supplemental-run",
        {"source": "pitch3d", "dataset": "mlb", "mode": "backfill", "pitcher_ids": [453286], "resume": True},
    )
    assert second["status"] == "success"
    progress = handle_supplemental_action(
        cfg, "supplemental-progress", {"source": "pitch3d", "dataset": "mlb"}
    )["progress"]
    assert progress["skipped_units"] == 1
    assert progress["percent"] == 100.0

    verified = handle_supplemental_action(
        cfg, "supplemental-verify", {"source": "pitch3d", "dataset": "mlb"}
    )
    assert verified["ok"] is True
    assert verified["raw_snapshots"] == 1

    with SupplementalStore(cfg) as store:
        store.conn.execute("DELETE FROM pitch3d_pitches")
        store.conn.commit()
    rebuilt = handle_supplemental_action(
        cfg, "supplemental-rebuild",
        {"source": "pitch3d", "dataset": "mlb", "confirmation": "REBUILD"},
    )
    assert rebuilt["snapshots_rebuilt"] == 1
    assert rebuilt["rows"] == 2


def test_spin_aggregate_preserves_all_source_fields(tmp_path, monkeypatch):
    cfg = config(tmp_path)
    seed_pitcher(cfg, 660271)
    rows = [
        {
            "season": 2026,
            "api_pitch_type": "FF",
            "image_spin_x": -0.619,
            "image_spin_y": -0.658,
            "image_spin_z": -0.412,
            "image_orientation_angle": 85,
            "hawkeye_measured": 146.57,
            "movement_inferred": 159.51,
            "active_spin": 0.746,
            "alan_active_spin_pct": 74.6,
            "spin_rate": 2400,
            "n_pitches": 602,
            "future_unknown_field": "preserve-me",
        }
    ]
    import json
    html = f'<html><script>window.serverVals={{"spinAxis":{json.dumps(rows)}}};</script></html>'.encode()
    monkeypatch.setattr(
        SupplementalClient,
        "spin_aggregate",
        lambda self, pitcher: FakeResponse(html, "text/html; charset=utf-8"),
    )

    result = handle_supplemental_action(
        cfg, "supplemental-run",
        {"source": "spin_aggregate", "dataset": "mlb", "mode": "update", "pitcher_ids": [660271]},
    )
    assert result["status"] == "success"
    assert result["rows_received"] == 1

    with SupplementalStore(cfg) as store:
        row = store.conn.execute(
            "SELECT image_spin_x,image_orientation_angle,hawkeye_measured,future_unknown_field FROM spin_orientation_aggregates"
        ).fetchone()
        assert row[0] == -0.619
        assert row[1] == 85
        assert row[2] == 146.57
        assert row[3] == "preserve-me"
        schema = {
            item[0]: item[1]
            for item in store.conn.execute(
                "SELECT original_name,column_name FROM supplemental_schema WHERE source='spin_aggregate'"
            )
        }
        assert schema["future_unknown_field"] == "future_unknown_field"


def test_pitch3d_mlb_and_milb_are_separate_namespaces(tmp_path):
    cfg = config(tmp_path)
    payload = b"game_pk,play_id,pitcher,api_pitch_type\n1,same,1,FF\n"
    response = FakeResponse(payload, "text/csv")
    with SupplementalStore(cfg) as store:
        mlb_snapshot = store.save_snapshot("pitch3d", "mlb", "1", payload, response)
        milb_snapshot = store.save_snapshot("pitch3d", "milb", "1", payload, response)
        store.replace_pitch3d(1, "mlb", payload, mlb_snapshot)
        store.replace_pitch3d(1, "milb", payload, milb_snapshot)
        assert store.conn.execute("SELECT COUNT(*) FROM pitch3d_pitches").fetchone()[0] == 2
        assert store.conn.execute("SELECT COUNT(*) FROM pitch3d_pitches WHERE dataset='mlb'").fetchone()[0] == 1
        assert store.conn.execute("SELECT COUNT(*) FROM pitch3d_pitches WHERE dataset='milb'").fetchone()[0] == 1
