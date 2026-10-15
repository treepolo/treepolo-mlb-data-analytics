"""The pitch_query method, its runtime (tables, packages, names, gates) and the API / command-line front ends, on a synthetic world with known player effects."""
from __future__ import annotations

import argparse
import sqlite3

import numpy as np
import pytest

from treepolo_mlb_data.config import AppConfig
from treepolo_mlb_data.research import api, cli, gbm, policy_methods as pm, policy_worlds as pw, query_core as qc
from treepolo_mlb_data.research.config_schema import ConfigError
from treepolo_mlb_data.research.service import ResearchService
from treepolo_mlb_data.research.synthetic import simulate_players, write_player_world_db

BACKEND = "xgboost" if gbm.xgboost_available() else "hgb"
SCOPE = {"game_years": [2023, 2024], "game_types": ["R"]}
GBM = {"max_depth": 4, "min_leaf": 300, "max_iter": 50, "learning_rate": 0.15}


class FakeRe:
    def __init__(self, re_cnt):
        self.re_by_state = {b * 1000 + k * 100 + o * 10 + B: float(re_cnt[b, k, o, B]) for b in range(4) for k in range(3) for o in range(3) for B in range(8)}


@pytest.fixture(scope="module")
def setup(tmp_path_factory):
    data = tmp_path_factory.mktemp("pq") / "data"; data.mkdir()
    d = simulate_players("P7", n_pitchers=40, n_batters=60, n_games=1200, pitches_per_game=60, seed=11)
    write_player_world_db(data / "statcast.sqlite3", d)
    conn = sqlite3.connect(data / "statcast.sqlite3")
    conn.execute("ALTER TABLE pitches ADD COLUMN player_name TEXT")
    conn.execute("UPDATE pitches SET player_name = 'Pitcher' || pitcher || ', Test'")
    conn.execute("UPDATE pitches SET des = 'Batter' || batter || ' Test singles on a line drive.' WHERE events IS NOT NULL")
    conn.commit(); conn.close()
    service = ResearchService(AppConfig(data_dir=str(data)), None)
    # the pitch_policy run reads the same pitches, supplied as columns (the player-world database has no plate-appearance structure)
    same = d["prev1_pitch_type"] == d["pitch_type"]
    cols = dict(d, prior_streak=np.where(same, d["streak_pos"] - 1, np.where(d["prev1_pitch_type"] != "none", 1.0, 0.0)), pitch_value=np.zeros(len(d["game_pk"])))
    env = pw.build_world("W0").env
    mp = pytest.MonkeyPatch()
    mp.setattr(pm, "fetch_columns", lambda ctx, config, groups, years, re_by_state=None: cols)
    mp.setattr(pm, "get_re_table", lambda ctx, scope: FakeRe(env.re_cnt))
    try:
        league = service.run("league_model", {"scope": SCOPE, "groups": ["RvR"], "levels": ["L0", "L3"], "backend": BACKEND, "device": "cpu", "cold_folds": 3, "bootstrap_reps": 40,
                                              "min_cell_swings": 15, "min_class_count": 20, "bagging_reps": 4, "save_package": True, "min_gain": 0.003, **GBM})["run"]
        policy = service.run("pitch_policy", {"scope": SCOPE, "re_scope": SCOPE, "groups": ["RvR"], "members": 4, "cross_eval": False, "backend": BACKEND, "device": "cpu",
                                              "min_count": 100, "lambda_grid": [0.0], "min_class_count": 20, "max_depth": 4, "min_leaf": 300, "learning_rate": 0.15, "max_iter": 50})["run"]
    finally:
        mp.undo()
    query = service.run("pitch_query", {"scope": SCOPE, "policy_run_id": policy["id"], "package_run_id": league["id"], "check_situations": 12, "check_samples": 10, "check_members": 2})["run"]
    return service, league, policy, query, d


def rows(service, run, prefix):
    return next(s["rows"] for s in service.store.load_result(run["id"])["sections"] if s["title"].startswith(prefix))


def test_acceptance_checks_pass_and_player_queries_are_fast(setup):
    service, league, policy, query, d = setup
    accept = rows(service, query, "Acceptance")
    assert len(accept) >= 12 * 3 and all(r["passed"] == 1 for r in accept)
    assert {r["check"] for r in accept} >= {"Q equals the table", "marginal value equals Delta*(Q_a - Qbar_-a)", "flags follow the support rule", "M1 repeats and is well formed",
                                            "M2 repeats and is well formed", "M3 repeats and is well formed"}
    latency = {r["mode"]: r for r in rows(service, query, "Latency")}
    assert set(latency) == {"M1", "M2", "M3"} and all(r["seconds"] < 30 for r in latency.values())
    names = {(r["side"]): r["value"] for r in rows(service, query, "Names")}
    assert names["pitcher"] >= 0.95 and names["batter"] >= 0.95                      # plan 3.9: names for at least 95 % of the players
    assert rows(service, query, "Sources")[0]["package_run_id"] == league["id"]


def test_runtime_answers_with_names_and_gate_status(setup):
    service, league, policy, query, d = setup
    meta = service.pitch_query_meta(query["id"])
    assert meta["groups"] == ["RvR"] and meta["players_available"] and meta["package_groups"] == ["RvR"] and "KC" in meta["pitch_types"]
    assert meta["gates"]["RvR"]["pitcher"] in ("full", "level_only", "off", "unknown") and meta["not_covered"]
    found = service.pitch_query_players(query["id"], "RvR", "pitcher", "Pitcher1")["players"]
    assert found and all("Pitcher1" in (p["name"] or "") or "1" in p["id"] for p in found) and found[0]["pitches"] > 0
    top = service.pitch_query_players(query["id"], "RvR", "batter", "")["players"]
    assert [p["pitches"] for p in top] == sorted((p["pitches"] for p in top), reverse=True)
    m0 = service.pitch_query(query["id"], {"group": "RvR", "balls": "1", "strikes": "1", "outs": "0", "bases": "000", "prev": "SL", "streak": "1"})
    assert m0["mode"] == "M0" and len(m0["rows"]) == 9
    pid = found[0]["id"]
    m1 = service.pitch_query(query["id"], {"group": "RvR", "balls": "1", "strikes": "1", "outs": "0", "bases": "000", "prev": "SL", "streak": "1", "pitcher": pid, "samples": "10", "members": "2"})
    gate = meta["gates"]["RvR"]["pitcher"]
    assert (m1["mode"] == "M1" and m1["players"]["pitcher"]["name"] == f"Test Pitcher{pid}") or (gate == "off" and m1["mode"] == "M0" and m1["notice"])
    with pytest.raises(ConfigError, match="group"):
        service.pitch_query(query["id"], {"group": "LvL", "balls": "0", "strikes": "0", "outs": "0"})
    with pytest.raises(ConfigError):
        service.query_runtime(policy["id"])             # not a pitch_query run


def test_api_routes(setup):
    service, league, policy, query, d = setup
    status, body = api.handle_get(service, "/api/research/pitch-query/meta", f"run={query['id']}")
    assert status == 200 and body["groups"] == ["RvR"]
    status, body = api.handle_get(service, "/api/research/pitch-query", f"run={query['id']}&group=RvR&balls=0&strikes=2&outs=1&bases=010&prev=FF&streak=2")
    assert status == 200 and body["mode"] == "M0" and [r["action"] for r in body["rows"]] == list(qc.PITCH_TYPES)
    status, body = api.handle_get(service, "/api/research/pitch-query/players", f"run={query['id']}&group=RvR&side=pitcher&q=Test&limit=5")
    assert status == 200 and len(body["players"]) == 5
    with pytest.raises(ConfigError):
        api.handle_get(service, "/api/research/pitch-query", f"run={query['id']}&group=RvR&balls=0")
    with pytest.raises(ConfigError):
        api.handle_get(service, "/api/research/pitch-query", f"group=RvR&balls=0&strikes=0&outs=0")


def test_command_line_table(setup):
    service, league, policy, query, d = setup
    result = service.pitch_query(query["id"], {"group": "RvR", "balls": "0", "strikes": "0", "outs": "0", "bases": "100", "prev": "none", "streak": "1"})
    text = qc.format_table(result)
    assert text.splitlines()[0].startswith("mode M0") and "FF" in text and "not covered" in text and len(text.splitlines()) >= 11
    parser = argparse.ArgumentParser(); cli.add_arguments(parser)
    args = parser.parse_args(["query", str(query["id"]), "--group", "RvR", "--balls", "1", "--strikes", "2", "--outs", "1", "--bases", "100", "--prev", "SL", "--streak", "2", "--json"])
    assert args.run_id == query["id"] and args.bases == "100" and args.json


def test_method_guards(setup):
    service, league, policy, query, d = setup
    with pytest.raises(ConfigError, match="pitch_policy"):
        service.run("pitch_query", {"scope": SCOPE, "policy_run_id": league["id"]})
    with pytest.raises(ConfigError, match="required"):
        service.run("pitch_query", {"scope": SCOPE})
    with pytest.raises(ConfigError, match="league_model"):
        service.run("pitch_query", {"scope": SCOPE, "policy_run_id": policy["id"], "package_run_id": policy["id"]})
    again = service.run("pitch_query", {"scope": SCOPE, "policy_run_id": policy["id"], "package_run_id": league["id"], "check_situations": 12, "check_samples": 10, "check_members": 2})
    assert again["reused"]
    only_m0 = service.run("pitch_query", {"scope": SCOPE, "policy_run_id": policy["id"], "with_names": False, "check_situations": 6})["run"]
    rt = service.query_runtime(only_m0["id"])
    with pytest.raises(ConfigError, match="M0"):
        rt.search("RvR", "pitcher", "")
