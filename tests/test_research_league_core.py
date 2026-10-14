"""League model on synthetic player worlds with known answers: nothing to find (P0), observable effects (P7), latent type effects (P8)."""
from __future__ import annotations

import numpy as np
import pytest

from treepolo_mlb_data.research import gbm
from treepolo_mlb_data.research import league_core as lc
from treepolo_mlb_data.research import modeling as md
from treepolo_mlb_data.research.profiles import ProfileModel
from treepolo_mlb_data.research.synthetic import simulate_players

HAS_XGB = gbm.xgboost_available()
PARAMS = dict(max_depth=4, max_iter=60, min_leaf=100, learning_rate=0.1)
_CACHE: dict = {}


def run_world(kind: str, backend: str, *, n_games=1500, levels=("L0", "L3")):
    key = (kind, backend, n_games, levels)
    if key not in _CACHE:
        d = simulate_players(kind, n_pitchers=120, n_batters=150, n_games=n_games, pitches_per_game=50, seed=2)
        train = d["game_year"] == 2023
        ctr, cte = lc.take(d, np.nonzero(train)[0]), lc.take(d, np.nonzero(~train)[0])
        profile = ProfileModel().fit(ctr)
        _CACHE[key] = lc.evaluate_group(ctr, cte, profile=profile, target="outcome", levels=levels, backend=backend, device="cpu", params=PARAMS, reps=1, seed=1,
                                        merge=md.DEFAULT_CLASS_MERGE, bootstrap_reps=100, cold_by=("pitcher",), cold_folds=3, min_cell_swings=15)
    return _CACHE[key]


def gain(res, mode, comparison="L0→L3"):
    return next(r for r in res["gain"] if r["mode"] == mode and r["comparison"] == comparison)


def contrast(res, mode, side, comparison="L0→L3"):
    return next(r for r in res["contrast"] if r["mode"] == mode and r["side"] == side and r["comparison"] == comparison)


needs_xgb = pytest.mark.skipif(not HAS_XGB, reason="xgboost not installed")


@needs_xgb
def test_p0_nothing_to_find_no_false_gain_on_unseen_players_or_next_season():
    res = run_world("P0", "xgboost")
    for mode in ("cold_pitcher", "season"):
        g = gain(res, mode)
        assert g["lo"] < 0.0005 and g["hi"] < 0.003            # never a confident gain from descriptors that carry no information
    for mode, side in (("cold_pitcher", "pitcher"), ("season", "pitcher"), ("season", "batter")):
        c = contrast(res, mode, side)
        assert c["lo"] < 0.0002 and abs(c["gain"]) < 0.0005


@needs_xgb
def test_p7_observable_effects_are_found_on_unseen_players_and_in_the_type_contrast():
    res = run_world("P7", "xgboost")
    assert gain(res, "cold_pitcher")["lo"] > 0.02 and gain(res, "season")["lo"] > 0.02
    for mode, side in (("cold_pitcher", "pitcher"), ("season", "pitcher"), ("season", "batter")):
        assert contrast(res, mode, side)["lo"] > 0          # G1b: descriptors tell which type works for whom, also for players never trained on


@needs_xgb
def test_p8_latent_type_effects_level_is_found_but_type_contrast_is_not_on_unseen_pitchers():
    res = run_world("P8", "xgboost")
    assert gain(res, "cold_pitcher")["lo"] > 0                      # G1a: outcome-based descriptors carry the stable level offsets
    c = contrast(res, "cold_pitcher", "pitcher")
    assert c["lo"] <= 0 and c["gain"] < 0.001                       # G1b: a latent type-specific effect cannot be recovered for a stranger


def test_hgb_backend_gives_the_same_answers_on_a_small_world():
    res = run_world("P7", "hgb", n_games=900)
    assert gain(res, "cold_pitcher")["lo"] > 0.01
    assert contrast(res, "cold_pitcher", "pitcher")["gain"] > 0


@needs_xgb
def test_calibration_runs_and_keeps_probabilities_valid():
    d = simulate_players("P7", n_pitchers=60, n_batters=80, n_games=600, pitches_per_game=50, seed=4)
    train = d["game_year"] == 2023
    ctr, cte = lc.take(d, np.nonzero(train)[0]), lc.take(d, np.nonzero(~train)[0])
    res = lc.evaluate_group(ctr, cte, profile=ProfileModel().fit(ctr), target="outcome", levels=("L0", "L3"), backend="xgboost", device="cpu", params=PARAMS, reps=2,
                            seed=1, merge=md.DEFAULT_CLASS_MERGE, bootstrap_reps=50, cold_by=("pitcher",), cold_folds=3, calibrate=True)
    cal = [r for r in res["fit"] if r["mode"] == "season_calibrated"]
    assert len(cal) == 2 and all(np.isfinite(r["logloss"]) and r["logloss"] < 2.0 for r in cal)
    assert all(r["member_logloss_sd"] >= 0 for r in res["fit"] if r["mode"] == "season")


@needs_xgb
def test_usage_target_pitch_selection_model_runs():
    d = simulate_players("P7", n_pitchers=60, n_batters=80, n_games=600, pitches_per_game=50, seed=4)
    train = d["game_year"] == 2023
    ctr, cte = lc.take(d, np.nonzero(train)[0]), lc.take(d, np.nonzero(~train)[0])
    res = lc.evaluate_group(ctr, cte, profile=ProfileModel().fit(ctr), target="usage", levels=("L0", "L1"), backend="xgboost", device="cpu", params=PARAMS, reps=1,
                            seed=1, bootstrap_reps=50, cold_by=("pitcher",), cold_folds=3)
    assert res["classes"] == list(lc.PITCH_TYPES)
    assert gain(res, "season", "L0→L1")["lo"] > 0                     # a pitcher's repertoire descriptors say what he throws


def test_unit_folds_keep_players_whole():
    ids = np.repeat(np.arange(30), 10)
    folds = lc.unit_folds(ids, 3, seed=0)
    assert len(folds) == 3
    for tr, te in folds:
        assert not set(ids[tr]) & set(ids[te]) and len(tr) + len(te) == len(ids)
