from datetime import date
import csv, io
import pytest
from treepolo_mlb_data.savant import SavantClient


@pytest.mark.integration
def test_live_savant_one_day_has_pitch_level_fields():
    client = SavantClient(timeout_seconds=120, retries=2, pause_seconds=0)
    payload = client.fetch(date(2024, 4, 1), date(2024, 4, 1))
    reader = csv.DictReader(io.StringIO(payload.decode("utf-8-sig")))
    rows = list(reader)
    assert len(rows) > 1000
    required = {"game_pk", "at_bat_number", "pitch_number", "release_speed", "release_spin_rate", "spin_axis", "pfx_x", "pfx_z", "description", "launch_speed", "launch_angle", "estimated_ba_using_speedangle"}
    assert required.issubset(set(reader.fieldnames or []))
