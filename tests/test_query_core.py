"""Situation queries: the league-average table (M0) and the player queries (M1-M3) on a world with known player effects."""
from __future__ import annotations

import time

import numpy as np
import pytest

from treepolo_mlb_data.research import gbm, outcome_model_methods as omm, outcome_models as om, policy_build as pb, policy_core as pc, policy_methods as pm, policy_worlds as pw, query_core as qc
from treepolo_mlb_data.research.profiles import PITCH_TYPES
from treepolo_mlb_data.research.synthetic import simulate_players

BACKEND = "xgboost" if gbm.xgboost_available() else "hgb"
PARAMS = dict(max_depth=4, min_leaf=300, learning_rate=0.15, max_iter=60, l2=1.0, backend=BACKEND, device="cpu")
CONFIG = {"cell_confidence": 0.99, "marginal_shift": 0.10, "min_count": 100, "min_share": 0.05, "trust_region": 0.10}


@pytest.fixture(scope="module")
def p7():
    d = simulate_players("P7", n_pitchers=40, n_batters=60, n_games=1200, pitches_per_game=60, seed=11)
    same = d["prev1_pitch_type"] == d["pitch_type"]
    d["prior_streak"] = np.where(same, d["streak_pos"] - 1, np.where(d["prev1_pitch_type"] != "none", 1.0, 0.0))
    return d


@pytest.fixture(scope="module")
def built(p7):
    """A situation table (the league-average player of the model) and the trained model itself (player effects for pitchers and batters), on the same pitches."""

    shape = pc.Shape(); d = p7
    model = om.new_model("gbm").fit(d, None, {**PARAMS, "control_pitcher": "effect", "control_batter": "effect", "bagging_reps": 4, "seed": 1})
    model.group = "RvR"
    tensor = pb.model_tensor(model, shape, "RvR", effects="population")
    obs, support, n = pb.usage_from_counts(d, shape, min_count=100)
    env = pw.build_world("W0").env
    an = pb.analyze(pb.members_dynamics(tensor, obs, support, n), env, shape, kinds=("observed", "dp_trust"))
    arrays = pm.situation_arrays("RvR", an, tensor, env, CONFIG)
    table = qc.SituationTable.from_rows(pm.pack_arrays(arrays))
    return table, model, env


def test_bases_parsing_and_state_errors(built):
    table, _, _ = built
    assert [qc.parse_bases(v) for v in ("000", "100", "010", "001", "111", 5, "5")] == [0, 1, 2, 4, 7, 5, 5]
    for bad in ("12", "abc", 9, "1111"):
        with pytest.raises(qc.QueryError):
            qc.parse_bases(bad)
    with pytest.raises(qc.QueryError, match="previous"):
        table.state(1, 1, 0, 0, "none", 1)
    with pytest.raises(qc.QueryError, match="first pitch"):
        table.state(0, 0, 0, 0, "FF", 1)
    with pytest.raises(qc.QueryError, match="unknown previous"):
        table.state(1, 1, 0, 0, "ZZ", 1)
    with pytest.raises(qc.QueryError, match="streak"):
        table.state(1, 1, 0, 0, "FF", 4)
    with pytest.raises(qc.QueryError, match="out of range"):
        table.state(4, 1, 0, 0, "FF", 1)


def test_overlap_groups_chain_overlapping_intervals():
    lo = np.array([0.50, 0.52, 0.60, 0.40, np.nan]); hi = np.array([0.54, 0.56, 0.64, 0.45, np.nan])
    sup = np.array([True, True, True, True, False])
    assert qc.overlap_groups(lo, hi, sup) == ["B", "B", "C", "A", None]
    assert not hasattr(qc, "indistinct_groups")


def test_m0_rows_follow_the_table_and_the_closed_forms(built):
    table, _, _ = built
    res = table.lookup(1, 2, 1, "100", "SL", 2)
    assert res["mode"] == "M0" and res["group"] == "RvR" and len(res["rows"]) == 9
    rows = {r["action"]: r for r in res["rows"]}
    assert rows["KC"]["flag"] == "unsupported" and rows["KC"]["supported"] is False and rows["KC"]["overlap_group"] is None
    assert all("detected" not in r and "evidence" not in r and "indistinct_group" not in r for r in res["rows"])        # no verdict, only numbers, intervals and overlap letters
    chance = res["comparisons"]
    sup_rows = [r for r in res["rows"] if r["supported"] and r["adv_lo"] is not None and r["adv_hi"] is not None]
    assert chance["n"] == len(sup_rows) and chance["found"] == sum(1 for r in sup_rows if r["adv_lo"] > 0 or r["adv_hi"] < 0)
    assert chance["expected_by_chance"] == pytest.approx(chance["n"] * 0.01) and "overlap" in qc.format_table(res) and "luck" in qc.format_table(res)
    supported = [r for r in res["rows"] if r["supported"]]
    assert len(supported) >= 2 and all(abs(sum(r["outcomes"].values()) - 1) < 1e-9 for r in supported)
    obs = np.array([r["observed_share"] for r in res["rows"]]); q = np.array([r["q"] for r in res["rows"]])
    assert abs(obs.sum() - 1) < 1e-9
    for ai, r in enumerate(res["rows"]):
        if not r["supported"] or obs[ai] > 0.9:
            continue
        others = (obs.sum() - obs[ai])
        direct = 0.10 * (q[ai] - np.nansum(np.where(np.arange(9) != ai, obs * np.nan_to_num(q), 0.0)) / others)       # Delta * (Q_a - Qbar_{-a})
        assert r["marginal"] == pytest.approx(direct, abs=1e-9)
    assert res["not_covered"] and all(r["q_lo"] <= r["q"] <= r["q_hi"] for r in supported)
    assert table.lookup(0, 0, 2, 7, "none", 1)["rows"][0]["action"] == "FF"


def test_player_queries_use_the_effects_of_the_named_pitcher(built, p7):
    table, model, env = built
    pq = qc.PlayerQuery(table, model, env)
    ids, n = model.players("pitcher")
    velo = {int(i): float(np.mean(p7["velocity_truth"][p7["pitcher"] == i])) for i in ids}
    fast, slow = max(velo, key=velo.get), min(velo, key=velo.get)
    started = time.monotonic()
    a = pq.query(1, 1, 0, "000", "SL", 1, pitcher=fast, members=4)
    elapsed = time.monotonic() - started
    b = pq.query(1, 1, 0, "000", "SL", 1, pitcher=slow, members=4)
    assert a["mode"] == "M1" and a["players"]["pitcher"]["pitches"] > 0 and 0 < a["players"]["pitcher"]["shrinkage"] < 1 and a["players"]["pitcher"]["known"]
    ra = {r["action"]: r for r in a["rows"]}; rb = {r["action"]: r for r in b["rows"]}
    assert ra["FF"]["supported"] and rb["FF"]["supported"]
    assert ra["FF"]["outcomes"]["whiff"] > rb["FF"]["outcomes"]["whiff"]          # P7: faster fastballs get more whiffs, and the pitcher's own effect carries it
    arsenal_a = a["players"]["pitcher"]["arsenal"]
    assert all(ra[t]["supported"] == (t in arsenal_a and pq._arsenal_mask(arsenal_a, 0.05, 100)[PITCH_TYPES.index(t)] and table.arrays["support"][a["state"], PITCH_TYPES.index(t)]) for t in PITCH_TYPES)
    assert elapsed < 30
    again = pq.query(1, 1, 0, "000", "SL", 1, pitcher=fast, members=4)
    assert [r["q"] for r in again["rows"]] == [r["q"] for r in a["rows"]]           # same seed, same answer


def test_batter_and_pair_queries(built, p7):
    table, model, env = built
    pq = qc.PlayerQuery(table, model, env)
    bats, nb = model.players("batter")
    chase = {int(i): float(np.mean(p7["outcome"][p7["batter"] == i] == "whiff")) for i in bats}
    hi, lo = max(chase, key=chase.get), min(chase, key=chase.get)
    m2h = pq.query(0, 2, 1, "000", "FF", 2, batter=hi, members=4)
    m2l = pq.query(0, 2, 1, "000", "FF", 2, batter=lo, members=4)
    assert m2h["mode"] == "M2" and m2h["players"]["batter"]["pitches"] > 0
    qh = np.mean([r["outcomes"]["whiff"] for r in m2h["rows"] if r["supported"]]); ql = np.mean([r["outcomes"]["whiff"] for r in m2l["rows"] if r["supported"]])
    assert qh > ql                                                                     # the batter who whiffs more is predicted to whiff more
    pit = int(model.players("pitcher")[0][0])
    m3 = pq.query(0, 2, 1, "000", "FF", 2, pitcher=pit, batter=hi, members=4)
    assert m3["mode"] == "M3" and m3["sampling"]["pairs"] == 1 and set(m3["players"]) == {"pitcher", "batter"}
    assert any(r["supported"] for r in m3["rows"])
    stranger = pq.query(1, 1, 0, "000", "SL", 1, pitcher=-5)                          # a player the model has not seen is the league average, said so, not an error
    assert stranger["mode"] == "M1" and not stranger["players"]["pitcher"]["known"] and stranger["notice"]
    m0 = pq.query(1, 1, 0, "000", "SL", 1)
    assert m0["mode"] == "M0" and max(abs(x["q"] - y["q"]) for x, y in zip(stranger["rows"], m0["rows"]) if x["q"] is not None and y["q"] is not None) < 1e-6


def test_model_artifact_round_trip(built, p7):
    """The stored model (the artifact of outcome_model_train) loads back into a model that answers the same."""

    table, model, env = built
    loaded = omm.load_model(omm.pack_bytes(model.to_bytes()))
    rows = {k: v[:200] for k, v in p7.items() if isinstance(v, np.ndarray) and len(v) >= 200}
    assert np.allclose(loaded.predict_proba(rows, effects="specific"), model.predict_proba(rows, effects="specific"), atol=1e-7)
    assert loaded.classes == model.classes and len(loaded.members) == 4
    ids, n = loaded.players("pitcher")
    assert len(ids) == 40 and n.sum() > 0 and loaded.player_info("pitcher", int(ids[0]))["known"] and not loaded.player_info("pitcher", 123456789)["known"]
