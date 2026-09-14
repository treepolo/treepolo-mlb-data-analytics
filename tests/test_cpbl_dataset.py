from __future__ import annotations

import sqlite3
from datetime import date

from treepolo_mlb_data.cpbl_normalize import canonical_game_pk, normalize_game, rows_to_csv
from treepolo_mlb_data.cpbl_semantics import canonical_pitch_type
from treepolo_mlb_data.storage import StatcastStore


def _tracked_game():
    def pitch(balls, strikes, auto, call, speed, batter="0000000002"):
        return {
            "InningSeq": 1,
            "BallCnt": balls,
            "StrikeCnt": strikes,
            "OutCnt": 0,
            "PitchCnt": 10 + balls + strikes,
            "PitcherAcnt": "0000000001",
            "PitcherName": "投手",
            "HitterAcnt": batter,
            "HitterName": "打者",
            "Trackman": {
                "Play": {"PitchTag": {
                    "PitchCall": call,
                    "AutoPitchType": auto,
                    "TaggedPitchType": auto,
                }},
                "Pitch": {
                    "Release": {
                        "RelSpeed": speed,
                        "SpinRate": 2200,
                        "Extension": 1.8,
                        "RelHeight": 1.75,
                        "RelSide": -0.4,
                    },
                    "Location": {
                        "PlateLocSide": 0.0,
                        "PlateLocHeight": 0.8,
                        "ZoneSpeed": speed - 10,
                        "HorzApprAngle": 1.2,
                        "VertApprAngle": -5.0,
                    },
                    "Flight": {"PolyFit": {"PitchTrajectory": {
                        "X": [1, 2, 3], "Y": [4, 5, 6], "Z": [7, 8, 9],
                    }}},
                },
            },
        }

    return {
        "GameId": "2026-A-328",
        "KindCode": "A",
        "PreExeDate": "2026-09-13T00:00:00",
        "Field": {"No": "C", "Abbe": "澄清湖"},
        "LiveLog": [
            pitch(0, 0, "FourSeamFastBall", "StrikeCalled", 148.0),
            pitch(0, 1, "Slider", "StrikeSwinging", 136.0),
            pitch(0, 0, "ChangeUp", "BallCalled", 132.0, batter="0000000003"),
            pitch(1, 0, "ChangeUp", "InPlay", 133.0, batter="0000000003"),
        ],
    }


def test_cpbl_normalization_builds_deterministic_pitch_grain():
    game = _tracked_game()
    first = normalize_game(game)
    second = normalize_game(game)
    assert first == second
    assert [row["at_bat_number"] for row in first] == [1, 1, 2, 2]
    assert [row["pitch_number"] for row in first] == [1, 2, 1, 2]
    assert [row["pitch_type"] for row in first] == ["FF", "SL", "CH", "CH"]
    assert first[1]["auto_pitch_type"] == "Slider"
    assert first[1]["pitch_call"] == "StrikeSwinging"
    assert first[1]["description"] == "swinging_strike"
    assert first[1]["vert_appr_angle"] == -5.0
    assert first[0]["game_pk"] == canonical_game_pk("2026-A-328")


def test_cpbl_public_coarse_tagged_type_beats_degenerate_auto_type():
    # Full-season 2026 source census shows AutoPitchType is commonly the
    # low-information value "breakingball" even when TaggedPitchType says
    # "fastball". Preserve the source-supported coarse class instead of
    # inventing a four-seam classification.
    assert canonical_pitch_type("breakingball", "fastball") == "fastball"
    assert canonical_pitch_type("breakingball", "breakingball") == "breakingball"
    assert canonical_pitch_type(None, "fastball") == "fastball"
    assert canonical_pitch_type(None, "breakingball") == "breakingball"
    # If a future/detail feed really provides a detailed automatic class, it
    # remains more informative and therefore takes precedence.
    assert canonical_pitch_type("FourSeamFastBall", "fastball") == "FF"
    assert canonical_pitch_type("Slider", "breakingball") == "SL"


def test_cpbl_store_is_idempotent_and_keeps_numeric_trackman_fields(tmp_path):
    rows = normalize_game(_tracked_game(), fallback_date=date(2026, 9, 13))
    payload = rows_to_csv(rows)
    db = tmp_path / "cpbl.sqlite3"
    with StatcastStore(db) as store:
        first = store.ingest_csv(payload, "cpbl:test:1")
        second = store.ingest_csv(payload, "cpbl:test:2")
        assert first.inserted == 4
        assert second.unchanged == 4
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM pitches").fetchone()[0] == 4
        row = conn.execute(
            "SELECT cpbl_game_id,auto_pitch_type,pitch_type,description,vert_appr_angle,release_speed FROM pitches "
            "WHERE at_bat_number=1 AND pitch_number=2"
        ).fetchone()
        assert row == ("2026-A-328", "Slider", "SL", "swinging_strike", -5.0, 136.0)


def test_identifiable_pitch_without_trackman_is_retained_with_null_measurements():
    game = {
        "GameId": "2026-A-329",
        "KindCode": "A",
        "PreExeDate": "2026-09-14T00:00:00",
        "LiveLog": [{
            "InningSeq": 1,
            "BallCnt": 0,
            "StrikeCnt": 0,
            "PitchCnt": 1,
            "PitcherAcnt": "0000000001",
            "HitterAcnt": "0000000002",
            "Content": "tracking missing",
        }],
    }
    rows = normalize_game(game)
    assert len(rows) == 1
    assert rows[0]["cpbl_has_trackman"] == 0
    assert rows[0]["release_speed"] is None
    assert rows[0]["pitch_type"] is None
    assert rows[0]["cpbl_source_pitch_cnt"] == 1


def test_non_pitch_live_log_without_trackman_is_valid_zero_pitch_game():
    game = {
        "GameId": "2026-D-105",
        "KindCode": "D",
        "PreExeDate": "2026-09-13T00:00:00",
        "SkipTrackman": True,
        "LiveLog": [{"Content": "四壞", "PitcherAcnt": "1", "HitterAcnt": "2"}],
    }
    assert normalize_game(game) == []
