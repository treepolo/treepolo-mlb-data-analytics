from __future__ import annotations

import numpy as np
import pytest

from treepolo_mlb_data.config import AppConfig
from treepolo_mlb_data.research import modeling as md
from treepolo_mlb_data.research.config_schema import ConfigError
from treepolo_mlb_data.research.service import ResearchService
from treepolo_mlb_data.research.synthetic import MODEL_WORLDS, simulate, write_world_db


def test_design_learns_on_training_rows_only_and_handles_unseen_and_missing():
    train = [{"x": 1.0, "c": "a"}, {"x": 3.0, "c": "b"}, {"x": None, "c": None}]
    design = md.Design(["x"], ["c"]).fit(train)
    assert design.columns == ["num:x", "missing:x", f"cat:c=__missing__", "cat:c=a", "cat:c=b"]
    x = design.transform([{"x": 2.0, "c": "zzz"}, {"x": None, "c": "a"}])
    assert x.shape == (2, 5) and x[0, 0] == pytest.approx(0.0) and x[0, 3:].sum() == 0      # unseen level: all zeros
    assert x[1, 1] == 1.0 and x[1, 3] == 1.0


def test_losses_and_known_values():
    y = np.array([0, 1]); p = np.array([[0.8, 0.2], [0.5, 0.5]])
    assert md.row_logloss(y, p) == pytest.approx([-np.log(0.8), -np.log(0.5)])
    assert md.multiclass_brier_rows(y, p) == pytest.approx([0.08, 0.5])


def test_paired_game_bootstrap_interval_covers_the_mean_and_excludes_zero_for_a_real_gain():
    rng = np.random.default_rng(1)
    games = np.repeat(np.arange(400), 25)
    delta = 0.01 + rng.normal(0, 0.05, size=len(games))
    mean, lo, hi, se = md.paired_game_bootstrap(delta, games, reps=300, seed=2)
    assert lo < mean < hi and lo > 0 and se == pytest.approx(0.05 / np.sqrt(len(games)), rel=0.3)


def test_calibration_of_a_calibrated_model_and_table_consistency():
    rng = np.random.default_rng(3)
    p1 = rng.uniform(0.05, 0.95, size=40_000); y = (rng.random(40_000) < p1).astype(int)
    proba = np.column_stack([1 - p1, p1])
    for row in md.calibration_rows(y, proba, ["no", "yes"], bins=5):
        assert abs(row["mean_pred"] - row["observed"]) < 0.03
    keys = [i % 4 for i in range(40_000)]
    out = md.table_consistency(keys, y, proba, ["no", "yes"], min_n=1000)
    assert out[1]["cells"] == 4 and out[1]["max_abs_diff"] < 0.03


def test_value_table_and_expected_values_use_the_class_mean_fallback():
    table, class_mean = md.value_table([0, 0, 1], [10, 10, 10], [1.0, 3.0, 5.0], 2, min_n=2)
    assert table == {(0, 10): 2.0} and class_mean == [2.0, 5.0]
    ev, fallback = md.expected_values(np.array([[0.5, 0.5]]), [10], table, class_mean)
    assert ev[0] == pytest.approx(0.5 * 2.0 + 0.5 * 5.0) and fallback == 1


def _service(tmp_path, world, years=(2023, 2024)):
    data = tmp_path / "data"; data.mkdir()
    db = data / "statcast.sqlite3"
    for i, year in enumerate(years):
        write_world_db(db, simulate(n_pa=30_000, seed=11 + i, **world), year=year, append=i > 0, pa_offset=i * 10_000_000)
    return ResearchService(AppConfig(data_dir=str(data)), None)


SMALL = {"groups": ["RvR"], "models": ["logistic"], "ev_check": False, "base_numeric": [], "base_categorical": ["pitch_type", "count_state"],
         "sequence_numeric": [], "sequence_categorical": ["prev1_pitch_type", "streak_cap"], "bootstrap_reps": 40, "min_class_count": 20,
         "table_consistency": False}


def test_outcome_model_through_the_service_finds_the_true_decay(tmp_path):
    service = _service(tmp_path, MODEL_WORLDS["M1"])
    run = service.run("outcome_model", {"scope": {"game_years": [2023, 2024], "game_types": ["R"]}, **SMALL})["run"]
    titles = run["summary"]["section_titles"]
    assert titles[0].startswith("Fit summary") and titles[1].startswith("Sequence gain")
    gain = service.result_page(run["id"], 1, 0, 10)["rows"][0]
    assert gain["lo"] > 0 and gain["delta_logloss"] > 0
    assert run["summary"]["artifact_names"] == ["logistic_coefficients"]


def test_reserved_year_guards_for_the_model(tmp_path):
    service = _service(tmp_path, MODEL_WORLDS["M1"], years=(2023, 2024, 2025))
    scope3 = {"game_years": [2023, 2024, 2025], "game_types": ["R"]}
    with pytest.raises(ConfigError, match="reserved"):
        service.run("outcome_model", {"scope": scope3, **SMALL})                                            # tuning with a test season
    with pytest.raises(ConfigError, match="final_test"):
        service.run("outcome_model", {"scope": scope3, "purpose": "final_test", "train_years": [2023, 2025], "test_years": [2024], **SMALL})
    with pytest.raises(ConfigError, match="grouped_kfold"):
        service.run("outcome_model", {"scope": scope3, "purpose": "extra_study", **SMALL})
    ok = service.run("outcome_model", {"scope": scope3, "purpose": "final_test", "train_years": [2023, 2024], "test_years": [2025], **SMALL})
    assert ok["run"]["status"] == "success" and service.store.load_result(ok["run"]["id"])["extras"]["final_test"] is True
    cv = service.run("outcome_model", {"scope": scope3, "purpose": "extra_study", "split": "grouped_kfold", "n_folds": 3, **SMALL})
    assert cv["run"]["status"] == "success"


@pytest.mark.parametrize("bad", [
    {"groups": ["XvY"]}, {"models": ["forest"]}, {"variants": []}, {"base_numeric": ["plate_y"]}, {"sequence_categorical": ["prev3_pitch_type"], "memory": 2},
    {"train_years": [2024], "test_years": [2024]}, {"train_years": [2022], "test_years": [2024]}, {"class_merge": {}},
])
def test_outcome_model_rejects_invalid_settings(tmp_path, bad):
    service = _service(tmp_path, MODEL_WORLDS["M0"])
    with pytest.raises(ConfigError):
        service.run("outcome_model", {"scope": {"game_years": [2023, 2024], "game_types": ["R"]}, **{**SMALL, **bad}})


def test_class_with_too_few_training_rows_is_an_error(tmp_path):
    service = _service(tmp_path, MODEL_WORLDS["M0"])
    with pytest.raises(ConfigError, match="min_class_count"):
        service.run("outcome_model", {"scope": {"game_years": [2023, 2024], "game_types": ["R"]}, **{**SMALL, "min_class_count": 10_000_000}})
