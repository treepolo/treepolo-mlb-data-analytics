from __future__ import annotations

import numpy as np
import pytest

from treepolo_mlb_data.config import AppConfig
from treepolo_mlb_data.research import modeling as md
from treepolo_mlb_data.research.config_schema import ConfigError
from treepolo_mlb_data.research.service import ResearchService
from treepolo_mlb_data.research.synthetic import simulate, write_world_db


def test_design_entities_are_sparse_and_rare_ids_are_pooled():
    train = [{"p": "a"}] * 3 + [{"p": "b"}] * 3 + [{"p": "rare"}]
    design = md.Design([], [], ["p"], entity_min_count=3).fit(train)
    assert design.columns == ["ent:p=a", "ent:p=b", "ent:p=OTHER"]
    x = design.transform([{"p": "a"}, {"p": "rare"}, {"p": "never seen"}, {"p": None}])
    assert x.shape == (4, 3) and x.format == "csr"
    assert x.toarray().tolist() == [[1, 0, 0], [0, 0, 1], [0, 0, 1], [0, 0, 1]]


def test_design_without_entities_is_still_dense():
    design = md.Design(["x"], ["c"]).fit([{"x": 1.0, "c": "a"}, {"x": 2.0, "c": "b"}])
    assert isinstance(design.transform([{"x": 1.5, "c": "a"}]), np.ndarray)


def test_sparse_matrices_fit_with_logistic_regression():
    rng = np.random.default_rng(1)
    rows = [{"p": str(rng.integers(0, 5))} for _ in range(3000)]
    y = np.array([int(r["p"]) % 2 for r in rows])
    design = md.Design([], [], ["p"], entity_min_count=1).fit(rows)
    proba, _, _ = md.fit_predict("logistic", {"C": 1.0, "max_iter": 200}, design.transform(rows), y, design.transform(rows), 2)
    assert (proba.argmax(axis=1) == y).mean() > 0.99


def test_early_stopping_flag_reaches_the_model():
    params = {"hgb_max_iter": 10, "hgb_learning_rate": 0.1, "hgb_max_depth": 3, "hgb_min_samples_leaf": 5, "seed": 1}
    assert md.make_model("hgb", params).early_stopping == "auto"          # library default when the setting is absent
    assert md.make_model("hgb", {**params, "hgb_early_stopping": False}).early_stopping is False


BASE = {"scope": {"game_years": [2023, 2024], "game_types": ["R"]}, "groups": ["RvR"], "models": ["logistic"], "ev_check": False,
        "base_numeric": [], "base_categorical": ["pitch_type", "count_state"], "sequence_numeric": [],
        "sequence_categorical": ["prev1_pitch_type", "streak_cap"], "bootstrap_reps": 100, "min_class_count": 20, "table_consistency": False,
        "entity_min_count": 50}


@pytest.fixture(scope="module")
def service(tmp_path_factory):
    data = tmp_path_factory.mktemp("two_pitchers") / "data"; data.mkdir()
    mix = ((0.8, 0.55, 0.55), (0.2, 0.2, 0.2))      # pitcher 10 throws mostly sliders and gets many whiffs; pitcher 11 the opposite; NO decay
    for i, year in enumerate((2023, 2024)):
        write_world_db(data / "statcast.sqlite3", simulate(n_pa=100_000, seed=21 + i, decay=0.0, swing=0.6, pitcher_mix=mix), year=year,
                       append=i > 0, pa_offset=i * 10_000_000)
    return ResearchService(AppConfig(data_dir=str(data)), None)


def _gain(service, **extra):
    run = service.run("outcome_model", {**BASE, **extra})["run"]
    return run, service.result_page(run["id"], 1, 0, 5)["rows"][0]


def test_sequence_features_leak_pitcher_identity_unless_entity_effects_are_included(service):
    _, without = _gain(service)
    assert without["lo"] > 0.003                                           # observed 0.0048 [0.0045, 0.0051]: a spurious "sequence effect"
    run, with_entity = _gain(service, entity_features=["pitcher"])
    assert abs(with_entity["delta_logloss"]) < 0.0003 and with_entity["hi"] < 0.0003      # observed -0.00002 [-0.00005, 0.00001]
    fit = service.result_page(run["id"], 0, 0, 10)["rows"]
    assert all(r["n_features"] >= 17 for r in fit)                         # pitcher columns (2 ids + OTHER) are in the design


def test_memory_zero_has_no_sequence_features_and_no_gain_section(service):
    run = service.run("outcome_model", {**BASE, "memory": 0})["run"]
    assert run["config"]["variants"] == ["base"] and run["config"]["sequence_categorical"] == []
    assert not any(t.startswith("Sequence gain") for t in run["summary"]["section_titles"])


@pytest.mark.parametrize("bad", [{"entity_features": ["team"]}, {"entity_features": ["pitcher", "pitcher"]},
                                 {"entity_features": ["pitcher"], "models": ["logistic", "hgb"]}])
def test_invalid_entity_settings(service, bad):
    with pytest.raises(ConfigError):
        service.run("outcome_model", {**BASE, **bad})
