from __future__ import annotations

import pytest

from pitch_fixtures import make_pitches_db, make_row, run_both
from treepolo_mlb_data.analysis import OUTCOME_CATEGORIES, PITCH_GRAIN, Source
from treepolo_mlb_data.analysis.pitch_table import build_pitch_table, outcome_cells_node
from treepolo_mlb_data.config import AppConfig
from treepolo_mlb_data.research.config_schema import ConfigError
from treepolo_mlb_data.research.service import ResearchService


def _rows():
    return [
        make_row(pitch_number=1, description="foul", pitch_type="FF", launch_angle=10.0, bat_speed=70.0, balls=0, strikes=0),
        make_row(pitch_number=2, description="foul", pitch_type="FF", launch_angle=30.0, balls=0, strikes=1),
        make_row(pitch_number=3, description="foul", pitch_type="FF", launch_angle=None, bat_speed=74.0, balls=0, strikes=2),
        make_row(pitch_number=4, description="ball", pitch_type="FF", balls=0, strikes=2),
    ]


def test_cells_count_and_average_only_the_non_null_values(tmp_path):
    db = make_pitches_db(tmp_path / "w.sqlite3", _rows())
    table = build_pitch_table(Source("pitches", PITCH_GRAIN), memory=1, extra_columns=("launch_angle", "bat_speed"))
    node = outcome_cells_node(table, cell_fields=("pitch_type",), cluster_fields=("pitcher",), categories=OUTCOME_CATEGORIES,
                              swing_metrics=("launch_angle", "bat_speed"), with_values=False)
    for result in run_both(db, node):
        row = result.rows[0]
        assert row["n"] == 4 and (row["w_launch_angle_n"], row["w_launch_angle_s"]) == (2, 40.0)
        assert (row["w_bat_speed_n"], row["w_bat_speed_s"]) == (2, 144.0)


def test_method_outputs_n_and_mean_and_rejects_unknown_fields(tmp_path):
    data = tmp_path / "data"; data.mkdir()
    make_pitches_db(data / "statcast.sqlite3", _rows())
    service = ResearchService(AppConfig(data_dir=str(data)), None)
    scope = {"game_years": [2024], "game_types": ["R"]}
    run = service.run("outcome_table", {"scope": scope, "use_values": False, "situation_fields": [], "swing_metrics": ["launch_angle", "miss_distance"]})["run"]
    cell = service.result_page(run["id"], 0, 0, 10)["rows"][0]
    assert (cell["launch_angle_n"], cell["launch_angle_mean"]) == (2, 20.0) and cell["miss_distance_n"] == 0 and cell["miss_distance_mean"] is None
    assert service.store.load_result(run["id"])["extras"]["swing_metric_coverage"]["launch_angle"] == pytest.approx(0.5)
    with pytest.raises(ConfigError):
        service.run("outcome_table", {"scope": scope, "swing_metrics": ["plate_x"]})
