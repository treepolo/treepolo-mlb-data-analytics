from __future__ import annotations

import math

import numpy as np
import pytest

from treepolo_mlb_data.config import AppConfig
from treepolo_mlb_data.research.config_schema import ConfigError
from treepolo_mlb_data.research.fixed_effects import codes, demean, dummies, fe_ols
from treepolo_mlb_data.research.service import ResearchService
from treepolo_mlb_data.research.synthetic import WORLDS, simulate, write_world_db

SCOPE = {"game_years": [2024], "game_types": ["R"]}


def test_demean_removes_two_fixed_effects_exactly():
    rng = np.random.default_rng(1)
    a_id = rng.integers(0, 20, size=2000); b_id = rng.integers(0, 15, size=2000)
    noise = rng.normal(0, 0.1, size=2000)
    y = np.linspace(0, 1, 20)[a_id] * 3 + np.linspace(0, 1, 15)[b_id] * (-2) + noise
    resid, converged = demean(y, [a_id, b_id])
    assert converged
    for g in (a_id, b_id):                                   # group means of the residual are zero for both effects
        assert np.abs(np.bincount(g, weights=resid) / np.bincount(g)).max() < 1e-7
    assert resid.std() == pytest.approx(0.1, rel=0.1)         # only the noise is left


def test_fe_ols_recovers_a_known_coefficient_and_matches_a_brute_force_cluster_se():
    rng = np.random.default_rng(2)
    n, groups = 6000, 60
    unit = rng.integers(0, groups, size=n); fe = rng.normal(0, 1, size=groups)[unit]
    d = (rng.random(n) < 0.3).astype(float)
    y = 0.5 * d + fe + rng.normal(0, 1, size=n)
    fit = fe_ols(y, d[:, None], [unit], unit)
    assert fit["beta"][0] == pytest.approx(0.5, abs=0.12) and fit["clusters"] == groups and fit["converged"]
    yt, _ = demean(y, [unit]); xt, _ = demean(d[:, None], [unit])
    resid = yt - xt[:, 0] * fit["beta"][0]
    meat = sum((xt[unit == g, 0] @ resid[unit == g]) ** 2 for g in range(groups))
    brute = math.sqrt(meat / (xt[:, 0] @ xt[:, 0]) ** 2 * groups / (groups - 1))
    assert fit["se"][0] == pytest.approx(brute, rel=1e-6)


def test_one_cluster_gives_no_standard_error():
    fit = fe_ols(np.arange(10.0), np.arange(10.0)[:, None] ** 2, [], np.zeros(10))
    assert fit["se"] == [None]


def test_codes_and_dummies():
    assert list(codes(["b", "a", "b"])) == [1, 0, 1]
    assert dummies(["a", "b", "c", "a"]).shape == (4, 2)


@pytest.fixture(scope="module")
def worlds(tmp_path_factory):
    root = tmp_path_factory.mktemp("regworlds")
    out = {}
    for name in WORLDS:
        data = root / name; data.mkdir()
        write_world_db(data / "statcast.sqlite3", simulate(n_pa=300_000, seed=1, **WORLDS[name]))
        out[name] = ResearchService(AppConfig(data_dir=str(data)), None)
    return out


BASE = {"scope": SCOPE, "pitch_types": ["SL"], "min_type_pitches": 1000, "hand_view": "pooled", "controls": ["count", "exposure"],
        "fixed_effects": ["batter"], "cluster_by": "game", "confidence": 0.95}


def _estimates(service, **extra):
    run = service.run("streak_regression", {**BASE, **extra})["run"]
    rows = service.result_page(run["id"], 0, 0, 100)["rows"]
    return run, {r["term"]: r for r in rows if r["term"]}


@pytest.mark.parametrize("world, k2, k3", [("T0", (-0.015, 0.015), (-0.015, 0.015)), ("T1", (-0.045, -0.010), (-0.095, -0.040)),
                                           ("T2", (-0.015, 0.015), (-0.015, 0.015)), ("T3", (-0.050, -0.005), (-0.110, -0.040))])
def test_known_answers_in_the_synthetic_worlds(worlds, world, k2, k3):
    _, terms = _estimates(worlds[world])
    assert k2[0] < terms["k=2"]["estimate"] < k2[1] and k3[0] < terms["k=3"]["estimate"] < k3[1], (world, terms["k=2"]["estimate"], terms["k=3"]["estimate"])
    assert terms["k=2"]["converged"] == 1 and terms["k=2"]["clusters"] > 1000


def test_without_the_batter_effect_the_survival_bias_in_t0_is_larger_than_with_it(worlds):
    _, with_fe = _estimates(worlds["T0"])
    _, without = _estimates(worlds["T0"], fixed_effects=[])
    assert abs(with_fe["k=3"]["estimate"]) < 0.015 and abs(without["k=2"]["estimate"]) < 0.02   # count dummies already remove most of it


def test_prev1_treatment_is_zero_in_a_world_without_order_effects(worlds):
    run, terms = _estimates(worlds["T0"], treatment="prev1_pitch_type", pitch_types=["SL"])
    assert set(terms) == {"prev=FF"} and abs(terms["prev=FF"]["estimate"]) < 0.02


def test_results_are_reproducible_and_reused(worlds):
    run, _ = _estimates(worlds["T1"])
    again = worlds["T1"].rerun(run["id"])
    assert again["reproduced"] is True


def test_insufficient_types_are_reported_not_filled(worlds):
    run, _ = _estimates(worlds["T1"], min_type_pitches=10_000_000)
    rows = worlds["T1"].result_page(run["id"], 0, 0, 10)["rows"]
    assert rows[0]["insufficient"] == 1 and rows[0]["term"] is None


@pytest.mark.parametrize("bad", [{"controls": ["nope"]}, {"fixed_effects": ["team"]}, {"fixed_effects": ["batter", "batter"]},
                                 {"controls": ["hand"], "hand_view": "four_groups"}, {"rate": "nope"}, {"max_rows": 5}])
def test_invalid_settings_are_rejected(worlds, bad):
    with pytest.raises(ConfigError):
        worlds["T0"].run("streak_regression", {**BASE, **bad})


def test_row_cap_is_an_error_not_a_truncation(worlds):
    with pytest.raises(ConfigError, match="max_rows"):
        worlds["T0"].run("streak_regression", {**BASE, "max_rows": 1000})


def _gaps(service, **extra):
    run, _ = _estimates(service, placebo_shuffles=4, **extra)
    assert run["summary"]["section_titles"][1].startswith("Placebo")
    return {r["term"]: r for r in service.result_page(run["id"], 1, 0, 20)["rows"]}


def test_regression_placebo_is_centred_on_zero_without_an_effect_and_far_from_the_estimate_with_one(worlds):
    t0, t1, t2 = _gaps(worlds["T0"]), _gaps(worlds["T1"]), _gaps(worlds["T2"])
    assert abs(t0["k=2"]["gap"]) < 0.01 and abs(t0["k=3"]["gap"]) < 0.01                     # observed +0.0025 / -0.0008
    assert abs(t2["k=2"]["gap"]) < 0.015 and abs(t2["k=2"]["placebo_mean"]) < 0.015           # differing hazards: still no false effect (+0.0066)
    assert t1["k=3"]["gap"] < -0.03 and abs(t1["k=3"]["placebo_mean"]) < 0.02                  # observed gap -0.064, placebo -0.004


def test_placebo_rejects_unsupported_specifications(worlds):
    with pytest.raises(ConfigError, match="placebo"):
        worlds["T0"].run("streak_regression", {**BASE, "placebo_shuffles": 2, "controls": ["count", "zone"]})
    with pytest.raises(ConfigError, match="placebo"):
        worlds["T0"].run("streak_regression", {**BASE, "placebo_shuffles": 2, "treatment": "prev1_pitch_type"})
