from __future__ import annotations

import pytest

from pitch_fixtures import run_both
from treepolo_mlb_data.research.synthetic import WORLDS, oracle_cells, simulate, write_world_db
from treepolo_mlb_data.analysis import AnalysisEngine, Filter, PITCH_GRAIN, Source
from treepolo_mlb_data.analysis.pitch_table import build_pitch_table, streak_cells_node
from treepolo_mlb_data.research.scope import scope_filter_expr
from treepolo_mlb_data.research.streak import same_point_curve

SCOPE = scope_filter_expr({"game_years": [2024], "game_types": ["R"]})
STRATA = ("pitch_index_in_pa", "balls", "strikes")
N_PA = 300_000


def _curve(db, *, exposure=False, pitch_types=("SL",)):
    strata = STRATA + (("prior_same_type_count",) if exposure else ())
    table = build_pitch_table(Filter(Source("pitches", PITCH_GRAIN), SCOPE), memory=1, prior_same_type_count=exposure)
    node = streak_cells_node(table, group_fields=(), strata_fields=strata, rate="whiff_per_swing", kmax=5, pitch_types=pitch_types)
    result = AnalysisEngine(db, analytics_database_path=db.with_suffix(".duckdb"), backend="duckdb").execute(node)
    assert result.backend == "duckdb", "DuckDB silently fell back to SQLite"
    naive, same = same_point_curve(result.rows, group_fields=(), strata_fields=strata, kmax=5)
    return result, naive, {r["k"]: r for r in same}, strata


@pytest.fixture(scope="module")
def worlds(tmp_path_factory):
    root = tmp_path_factory.mktemp("worlds")
    out = {}
    for name, settings in WORLDS.items():
        d = simulate(n_pa=N_PA, seed=1, **settings)
        db = root / f"{name}.sqlite3"
        write_world_db(db, d)
        out[name] = (d, db)
    return out


def test_ast_cells_equal_the_numpy_oracle_exactly(worlds):
    d, db = worlds["T1"]
    result, _, _, strata = _curve(db)
    ast = {tuple(r[f] for f in strata) + (int(r["k"]),): (int(r["n"]), int(r["sx"])) for r in result.rows if r["n"]}
    assert ast == oracle_cells(d, kmax=5)


def test_exposure_strata_cells_equal_the_numpy_oracle_exactly(worlds):
    d, db = worlds["T3"]
    result, _, _, strata = _curve(db, exposure=True)
    ast = {tuple(r[f] for f in strata) + (int(r["k"]),): (int(r["n"]), int(r["sx"])) for r in result.rows if r["n"]}
    assert ast == oracle_cells(d, kmax=5, exposure=True)


def test_zero_decay_world_naive_curve_decays_but_same_point_does_not(worlds):
    _, db = worlds["T0"]
    _, naive, same, _ = _curve(db)
    means = {r["k"]: r["mean"] for r in naive}
    assert means[1] - means[4] > 0.15            # survival bias: the naive curve shows a large fake decay
    for k in (2, 3):                              # k >= 4 is too thin to assert on
        assert abs(same[k]["estimate"]) < 0.02, (k, same[k])


def test_true_decay_world_same_point_recovers_the_decay(worlds):
    _, db = worlds["T1"]
    _, _, same, _ = _curve(db)
    assert -0.045 < same[2]["estimate"] < -0.020     # truth -0.03 (observed -0.0325, se 0.0038)
    assert -0.080 < same[3]["estimate"] < -0.040     # truth -0.06 (observed -0.0542, se 0.0072)
    assert same[2]["hi"] < 0                         # the interval excludes zero


def test_differing_hazards_leave_a_bias_that_the_exposure_stratum_removes(worlds):
    _, db = worlds["T2"]                             # zero true decay, FF and SL have different whiff hazards
    _, _, base, _ = _curve(db)
    _, _, exposed, _ = _curve(db, exposure=True)
    assert base[2]["estimate"] < -0.03 and base[3]["estimate"] < -0.03     # biased (observed -0.043, -0.044)
    assert abs(exposed[2]["estimate"]) < 0.02 and abs(exposed[3]["estimate"]) < 0.03
    assert exposed[2]["coverage"] < base[2]["coverage"]                    # the price: fewer comparable pitches


def test_exposure_stratum_on_the_true_decay_world_stays_near_the_truth(worlds):
    _, db = worlds["T3"]
    _, _, exposed, _ = _curve(db, exposure=True)
    assert -0.06 < exposed[2]["estimate"] < -0.015   # truth -0.03 (observed -0.0339, se 0.0074)


def test_a_pitcher_who_only_throws_sliders_has_no_comparable_stratum(tmp_path):
    d = simulate(n_pa=20_000, seed=3, p_sl=1.0, **WORLDS["T1"])    # streak position == pitch index: k never meets k=1 at one stratum
    db = tmp_path / "only_sl.sqlite3"
    write_world_db(db, d)
    _, _, same, _ = _curve(db)
    assert all(r["estimate"] is None and r["coverage"] == 0.0 for r in same.values())


def test_sqlite_and_duckdb_give_identical_cells_on_a_small_world(tmp_path):
    d = simulate(n_pa=6_000, seed=5, **WORLDS["T1"])
    db = tmp_path / "small.sqlite3"
    write_world_db(db, d)
    table = build_pitch_table(Filter(Source("pitches", PITCH_GRAIN), SCOPE), memory=1)
    node = streak_cells_node(table, group_fields=(), strata_fields=STRATA, rate="whiff_per_swing", kmax=5, pitch_types=("SL",))
    a, b = run_both(db, node)
    key = lambda r: tuple(r[f] for f in STRATA) + (int(r["k"]),)
    assert {key(r): (int(r["n"]), int(r["sx"])) for r in a.rows} == {key(r): (int(r["n"]), int(r["sx"])) for r in b.rows}
