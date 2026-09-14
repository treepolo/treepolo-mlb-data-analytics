from __future__ import annotations

import sqlite3

from treepolo_mlb_data.cli import build_parser
from treepolo_mlb_data.config import AppConfig
from treepolo_mlb_data.datasets import dataset_spec
from treepolo_mlb_data.storage import StatcastStore
from treepolo_mlb_data.web_analysis import AnalysisFacade


def _seed_one_pitch(path, pitch_type: str) -> None:
    payload = (
        "game_pk,game_date,game_year,at_bat_number,pitch_number,pitcher,batter,pitch_type,description,release_speed\n"
        f"1,2026-04-01,2026,1,1,10,20,{pitch_type},called_strike,145\n"
    ).encode()
    with StatcastStore(path) as store:
        store.ingest_csv(payload, f"seed:{pitch_type}")


def test_dataset_specs_are_physically_isolated_without_moving_legacy_mlb(tmp_path):
    config = AppConfig(data_dir=str(tmp_path / "data"))
    mlb = dataset_spec(config, "mlb")
    cpbl = dataset_spec(config, "cpbl")

    assert mlb.database_path == tmp_path / "data" / "statcast.sqlite3"
    assert cpbl.database_path == tmp_path / "data" / "cpbl" / "cpbl.sqlite3"
    assert cpbl.analytics_database_path == tmp_path / "data" / "cpbl" / "cpbl.duckdb"
    assert cpbl.analysis_state_database_path == tmp_path / "data" / "cpbl" / "analysis_state.sqlite3"
    assert {mlb.database_path, mlb.analytics_database_path, mlb.analysis_state_database_path}.isdisjoint(
        {cpbl.database_path, cpbl.analytics_database_path, cpbl.analysis_state_database_path}
    )

    _seed_one_pitch(mlb.database_path, "FF")
    _seed_one_pitch(cpbl.database_path, "SL")
    with sqlite3.connect(mlb.database_path) as conn:
        assert conn.execute("SELECT pitch_type FROM pitches").fetchone()[0] == "FF"
    with sqlite3.connect(cpbl.database_path) as conn:
        assert conn.execute("SELECT pitch_type FROM pitches").fetchone()[0] == "SL"


def test_cli_selects_one_dataset_per_process():
    parser = build_parser()
    assert parser.parse_args(["status"]).dataset == "mlb"
    assert parser.parse_args(["--dataset", "cpbl", "status"]).dataset == "cpbl"


def test_cpbl_analysis_metadata_declares_dataset_units_and_semantic_choices(tmp_path):
    config = AppConfig(data_dir=str(tmp_path / "data"))
    cpbl = dataset_spec(config, "cpbl")
    payload = (
        "game_pk,game_date,game_year,at_bat_number,pitch_number,pitcher,batter,pitch_type,description,"
        "release_speed,vert_appr_angle,auto_pitch_type,tagged_pitch_type,pitch_call\n"
        "1,2026-04-01,2026,1,1,10,20,FF,called_strike,145,-5.1,FourSeamFastBall,FourSeamFastBall,StrikeCalled\n"
    ).encode()
    with StatcastStore(cpbl.database_path) as store:
        store.ingest_csv(payload, "cpbl:meta")

    meta = AnalysisFacade(
        cpbl.database_path,
        backend="sqlite",
        dataset_id="cpbl",
        dataset_label=cpbl.label,
        speed_unit=cpbl.speed_unit,
        distance_unit=cpbl.distance_unit,
    ).meta()
    assert meta["dataset"] == {
        "id": "cpbl",
        "label": "CPBL / Trackman",
        "speed_unit": "kph",
        "distance_unit": "m",
    }
    assert "FF" in meta["choices"]["pitch_type"]
    fields = {item["name"]: item for item in meta["fields"]}
    assert "numeric" in fields["vert_appr_angle"]["capabilities"]
    assert "pitch_classification" in fields["auto_pitch_type"]["capabilities"]
