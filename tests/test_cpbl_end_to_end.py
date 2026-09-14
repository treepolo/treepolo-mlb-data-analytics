from __future__ import annotations

import sqlite3
from datetime import date

from treepolo_mlb_data.cpbl_sync import CPBLSyncEngine
from treepolo_mlb_data.fast_status import prepare_fast_status, read_fast_status
from treepolo_mlb_data.web_analysis import AnalysisFacade


DAY = date(2026, 4, 1)


def _game() -> dict:
    def pitch(number: int, auto: str, call: str, speed: float) -> dict:
        return {
            "InningSeq": 1,
            "BallCnt": 0,
            "StrikeCnt": number - 1,
            "OutCnt": 0,
            "PitchCnt": number,
            "PitcherAcnt": "0000000010",
            "PitcherName": "投手甲",
            "HitterAcnt": "0000000020",
            "HitterName": "打者甲",
            "Trackman": {
                "Play": {"PitchTag": {
                    "PitchCall": call,
                    "AutoPitchType": auto,
                    "TaggedPitchType": auto,
                }},
                "Pitch": {
                    "Release": {
                        "RelSpeed": speed,
                        "SpinRate": 2200.0 + number,
                        "Extension": 1.8,
                        "RelHeight": 1.75,
                        "RelSide": -0.4,
                    },
                    "Location": {
                        "PlateLocSide": 0.02 * number,
                        "PlateLocHeight": 0.80,
                        "ZoneSpeed": speed - 10.0,
                        "HorzApprAngle": 1.0 + number / 10,
                        "VertApprAngle": -5.0 - number / 10,
                    },
                },
            },
        }

    return {
        "GameId": "2026-A-1",
        "KindCode": "A",
        "PreExeDate": "2026-04-01T00:00:00",
        "SkipTrackman": False,
        "Field": {"No": "T", "Abbe": "測試球場"},
        "LiveLog": [
            pitch(1, "FourSeamFastBall", "StrikeCalled", 148.0),
            pitch(2, "Slider", "StrikeSwinging", 136.0),
        ],
    }


class _FakeCPBLClient:
    def __init__(self):
        self.schedule_calls = 0
        self.game_calls = 0

    def schedule(self, day):
        self.schedule_calls += 1
        assert str(day) == DAY.isoformat()
        return [{"GameId": "2026-A-1", "KindCode": "A"}]

    def game(self, game_id):
        self.game_calls += 1
        assert game_id == "2026-A-1"
        return _game()


class _ResumedGameClient:
    GAME_ID = "2026-D-117"

    def schedule(self, day):
        return [{"GameId": self.GAME_ID, "KindCode": "D", "PreExeDate": f"{day.isoformat()}T14:05:00"}]

    def game(self, game_id):
        assert game_id == self.GAME_ID
        payload = _game()
        payload["GameId"] = self.GAME_ID
        payload["KindCode"] = "D"
        payload["PreExeDate"] = "2026-09-19T14:05:00"
        return payload


def test_cpbl_sync_archives_normalizes_and_is_idempotent(tmp_path):
    root = tmp_path / "cpbl"
    db = root / "cpbl.sqlite3"
    client = _FakeCPBLClient()
    prepare_fast_status(db)
    engine = CPBLSyncEngine(db, root, client)

    first = engine.backfill(DAY, DAY, resume=False)
    second = engine.backfill(DAY, DAY, resume=False)

    assert first.days == 1
    assert first.games == 1
    assert first.tracked_games == 1
    assert first.pitches == 2
    assert first.inserted == 2
    assert second.inserted == 0
    assert second.unchanged == 2

    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM pitches").fetchone()[0] == 2
        assert conn.execute(
            "SELECT pitch_type,description,auto_pitch_type,pitch_call,vert_appr_angle "
            "FROM pitches ORDER BY pitch_number"
        ).fetchall() == [
            ("FF", "called_strike", "FourSeamFastBall", "StrikeCalled", -5.1),
            ("SL", "swinging_strike", "Slider", "StrikeSwinging", -5.2),
        ]

    raw = root / "raw"
    assert len(list((raw / "schedule").glob("**/*.json.gz"))) == 1
    assert len(list((raw / "games").glob("**/*.json.gz"))) == 1
    assert read_fast_status(db)["pitch_rows"] == 2


def test_cpbl_resumed_game_keeps_first_pitch_date_across_later_refresh(tmp_path):
    root = tmp_path / "cpbl"
    db = root / "cpbl.sqlite3"
    prepare_fast_status(db)
    engine = CPBLSyncEngine(db, root, _ResumedGameClient())

    original = date(2026, 6, 14)
    later_schedule = date(2026, 8, 30)
    first = engine.backfill(original, original, resume=False)
    second = engine.backfill(later_schedule, later_schedule, resume=False)

    assert first.inserted == 2
    assert second.inserted == 0
    with sqlite3.connect(db) as conn:
        rows = conn.execute(
            "SELECT DISTINCT game_date,cpbl_pre_exe_date FROM pitches WHERE cpbl_game_id=?",
            (_ResumedGameClient.GAME_ID,),
        ).fetchall()
    assert rows == [("2026-06-14", "2026-09-19T14:05:00")]


def test_cpbl_sqlite_and_duckdb_produce_equivalent_analysis(tmp_path):
    root = tmp_path / "cpbl"
    db = root / "cpbl.sqlite3"
    duck = root / "cpbl.duckdb"
    prepare_fast_status(db)
    CPBLSyncEngine(db, root, _FakeCPBLClient()).backfill(DAY, DAY, resume=False)

    payload = {
        "mode": "basic",
        "group_by": ["pitch_type"],
        "metrics": [
            {"function": "count"},
            {"function": "avg", "field": "vert_appr_angle"},
        ],
        "result_sort": [{"field": "pitch_type"}],
    }
    sqlite_result = AnalysisFacade(
        db,
        backend="sqlite",
        dataset_id="cpbl",
        dataset_label="CPBL / Trackman",
        speed_unit="kph",
        distance_unit="m",
    ).analyze(payload)
    duck_result = AnalysisFacade(
        db,
        duck,
        backend="duckdb",
        dataset_id="cpbl",
        dataset_label="CPBL / Trackman",
        speed_unit="kph",
        distance_unit="m",
    ).analyze(payload)

    assert sqlite_result["columns"] == duck_result["columns"]
    assert sqlite_result["rows"] == duck_result["rows"]
    assert sqlite_result["backend"] == "sqlite"
    assert duck_result["backend"] == "duckdb"
