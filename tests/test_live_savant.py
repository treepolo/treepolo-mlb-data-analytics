from datetime import date
import csv
import io

import pytest

from treepolo_mlb_data.config import AppConfig
from treepolo_mlb_data.raw import RawArchive
from treepolo_mlb_data.savant import SavantClient
from treepolo_mlb_data.storage import StatcastStore
from treepolo_mlb_data.sync import SyncEngine


LIVE_DATE = date(2024, 4, 1)
REQUIRED_FIELDS = {
    "game_pk",
    "at_bat_number",
    "pitch_number",
    "release_speed",
    "release_spin_rate",
    "spin_axis",
    "pfx_x",
    "pfx_z",
    "description",
    "launch_speed",
    "launch_angle",
    "estimated_ba_using_speedangle",
}


def _live_client() -> SavantClient:
    return SavantClient(timeout_seconds=120, retries=2, pause_seconds=0)


@pytest.mark.integration
def test_live_savant_one_day_has_pitch_level_fields():
    payload = _live_client().fetch(LIVE_DATE, LIVE_DATE)
    reader = csv.DictReader(io.StringIO(payload.decode("utf-8-sig")))
    rows = list(reader)

    assert len(rows) > 1000
    assert REQUIRED_FIELDS.issubset(set(reader.fieldnames or []))


@pytest.mark.integration
def test_live_savant_end_to_end_ingest_is_idempotent(tmp_path):
    """Exercise the real production path: Savant -> raw archive -> SQLite -> re-fetch."""
    config = AppConfig(
        data_dir=str(tmp_path),
        backfill_chunk_days=1,
        request_pause_seconds=0,
    )

    with StatcastStore(config.database_path) as store:
        engine = SyncEngine(config, store, _live_client(), RawArchive(config.root))

        first = engine.sync("live_e2e", LIVE_DATE, LIVE_DATE, chunk_days=1)
        first_report = store.verify()

        assert first.status == "success"
        assert first.received > 1000
        assert first.inserted == first.received
        assert first.updated == 0
        assert first_report["pitch_rows"] == first.received
        assert first_report["duplicate_pitch_uid"] == 0
        assert first_report["missing_natural_key"] == 0
        assert first_report["failed_chunks"] == 0
        assert first_report["raw_snapshots"] >= 1
        assert first_report["latest_game_date"] == LIVE_DATE.isoformat()

        db_columns = {
            row[1] for row in store.conn.execute("PRAGMA table_info(pitches)")
        }
        assert REQUIRED_FIELDS.issubset(db_columns)

        # Re-run the exact same historical range from the real upstream source.
        # A correct natural key/upsert path must not create additional pitches.
        second = engine.sync("live_e2e_repeat", LIVE_DATE, LIVE_DATE, chunk_days=1)
        second_report = store.verify()

        assert second.status == "success"
        assert second.received == first.received
        assert second.inserted == 0
        assert second_report["pitch_rows"] == first_report["pitch_rows"]
        assert second_report["duplicate_pitch_uid"] == 0
        assert second_report["missing_natural_key"] == 0
        assert second_report["failed_chunks"] == 0
