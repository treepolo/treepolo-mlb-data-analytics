"""The 11-class scheme (a ball in play that is not a home run is split by the batted-ball type): the split, the engine with a variable class list, and the table of what follows a ball in play.

Small fixed data only; the whole file runs in a few seconds."""
from __future__ import annotations

import numpy as np
import pytest

from treepolo_mlb_data.research import bip_table_methods as bt, league_core as lc, modeling, policy_core as pc

SHAPE = pc.Shape(2, 2, 3)


def test_a_ball_in_play_is_split_by_the_batted_ball_type_and_nothing_else_is():
    outcome = np.array(["ball", "in_play_out", "in_play_single", "in_play_home_run", "in_play_error_other", "whiff", "in_play_double"])
    bb = np.array(["", "ground_ball", "line_drive", "fly_ball", "", "", "popup"])
    out = lc.split_in_play(outcome, bb)
    assert out.tolist() == ["ball", "in_play_ground_ball", "in_play_line_drive", "in_play_home_run", "in_play_unknown", "whiff", "in_play_popup"]
    assert "in_play_unknown" not in lc.encode_outcome(out, modeling.BATTED_BALL_CLASS_MERGE)[1]       # a ball in play with no type is dropped like any unmapped category


def test_the_engine_classes_follow_the_model():
    assert pc.outcomes_for(["ball", "in_play_non_hr", "whiff"]) == pc.OUTCOMES
    assert pc.outcomes_for(["ball", "in_play_ground_ball", "whiff"]) == pc.OUTCOMES_BB and len(pc.OUTCOMES_BB) == 11 and list(pc.OUTCOMES_BB) == sorted(pc.OUTCOMES_BB)


def _world():
    S, A = SHAPE.size, SHAPE.n_actions
    table = 0.4 + 0.02 * np.arange(24).reshape(3, 8)                                           # value after a ball in play by outs and bases, the same for every pitch type
    env8 = pc.Environment(re_pa=0.3 + 0.1 * np.arange(24).reshape(3, 8) / 8.0, re_cnt=0.2 + 0.05 * np.arange(2 * 2 * 3 * 8).reshape(2, 2, 3, 8) / 24.0,
                          jbip=np.broadcast_to(table, (A, 3, 8)).copy(), start=1.0 + np.arange(24).reshape(3, 8) / 24.0)
    env11 = pc.Environment(re_pa=env8.re_pa, re_cnt=env8.re_cnt, jbip=env8.jbip * 0.0 + 9.0, start=env8.start, outcomes=pc.OUTCOMES_BB, bip={c: table for c in pc.BATTED_BALL_OUTCOMES})
    base = np.array([0.25, 0.10, 0.10, 0.05, 0.01, 0.04, 0.30, 0.15])
    P8 = np.zeros((S, A, 8))
    for a in range(A):
        p = base.copy(); p[0] += 0.02 * a; p[6] -= 0.02 * a; P8[:, a, :] = p
    split = np.array([0.45, 0.25, 0.22, 0.08])                                                  # ground ball, line drive, fly ball, popup
    P11 = np.zeros((S, A, 11))
    for c8, name in enumerate(pc.OUTCOMES):
        if name == "in_play_non_hr":
            for j, cls in enumerate(pc.BATTED_BALL_OUTCOMES):
                P11[:, :, pc.OUTCOMES_BB.index(cls)] = P8[:, :, c8] * split[j]
        else:
            P11[:, :, pc.OUTCOMES_BB.index(name)] = P8[:, :, c8]
    s_idx = np.arange(S)[:, None]; a_idx = np.arange(A)[None, :]
    support = ((a_idx + s_idx) % 3) != 0; support[::7] = False
    share = 1.0 + ((a_idx * 3 + s_idx) % 5); obs = share / share.sum(axis=1, keepdims=True)
    return env8, env11, P8, P11, obs, support


def test_with_the_same_end_values_eleven_classes_give_what_eight_classes_give():
    env8, env11, P8, P11, obs, support = _world()
    e8 = pc.Engine(SHAPE, pc.Dynamics(P=P8, obs=obs, support=support, n=None), env8, tol=1e-10, max_iter=400)
    e11 = pc.Engine(SHAPE, pc.Dynamics(P=P11, obs=obs, support=support, n=None), env11, tol=1e-10, max_iter=400)
    for kind in ("observed", "greedy1", "dp_unrestricted", "dp_trust"):
        V8, pi8 = e8.solve(kind, 0.10); V11, pi11 = e11.solve(kind, 0.10)
        assert e8.start_value(V8) == pytest.approx(e11.start_value(V11), abs=1e-9), kind
        assert np.allclose(pi8, pi11, atol=1e-9), kind
    pi = e8.policy_observed()
    assert e8.hr_probability(pi) == pytest.approx(e11.hr_probability(pi), abs=1e-10)           # the home-run terminal finds its class in either list


def test_the_batched_engine_takes_the_class_list_of_the_environment():
    torch = pytest.importorskip("torch")
    from treepolo_mlb_data.research import policy_batch as pbt

    env8, env11, P8, P11, obs, support = _world()
    eng = pbt.BatchEngine(SHAPE, env11, tol=1e-10, max_iter=400).use_device("cpu")
    out = eng.solve_kinds(eng.tensor(P11[None]), eng.tensor(obs[None]), eng.tensor(support[None], dtype=torch.bool), ("dp_trust",), 0.10, hr_kinds=("observed",))
    e8 = pc.Engine(SHAPE, pc.Dynamics(P=P8, obs=obs, support=support, n=None), env8, tol=1e-10, max_iter=400)
    V8, pi8 = e8.solve("dp_trust", 0.10)
    assert out["dp_trust"]["start"][0] == pytest.approx(e8.start_value(V8), abs=1e-9) and np.allclose(out["dp_trust"]["pi"][0].numpy(), pi8, atol=1e-9)
    assert out["observed"]["hr"][0] == pytest.approx(e8.hr_probability(e8.policy_observed()), abs=1e-10)


# ------------------------------------------------------------------------------------------------ the table of what follows a ball in play
def fake_cols():
    return {"outcome": np.array(["in_play_out", "in_play_single", "in_play_out", "ball", "in_play_out"]), "bb_type": np.array(["ground_ball", "line_drive", "ground_ball", "", ""]),
            "outs_when_up": np.array([1, 0, 1, 0, 2]), "bases": np.array([1, 0, 1, 0, 0]), "balls": np.array([0, 1, 0, 0, 0]), "strikes": np.array([1, 2, 1, 0, 0]),
            "pitch_value": np.array([-0.5, 0.2, -0.4, 0.0, -0.1]), "next_state_code": np.array([20.0, 1.0, np.nan, 0.0, 0.0]), "runs_on_pitch": np.array([0.0, 0.0, 0.0, 0.0, 0.0]),
            "events": np.array(["grounded_into_double_play", "single", "field_out", "", "field_out"]), "pitch_type": np.array(["SL", "FF", "SL", "FF", "FF"])}


def test_the_rows_after_a_ball_in_play_hold_the_value_the_outs_and_the_events():
    re_cnt = np.full((4, 3, 3, 8), 0.5)
    rows = bt.bip_rows(fake_cols(), re_cnt, pc.Shape())
    assert len(rows["type"]) == 3                                                               # the ball and the in-play row without a batted-ball type are left out
    assert rows["type"].tolist() == [0, 1, 0] and rows["outs"].tolist() == [1, 0, 1]
    assert rows["value"].tolist() == pytest.approx([0.0, 0.7, 0.1])                              # the run value of the pitch plus the run expectancy of its state
    assert rows["outs_after"].tolist() == [2, 0, 3] and rows["bases_after"].tolist() == [0, 1, 0]    # the next state of the data; no next state: the half-inning is over (three outs)
    assert [bt.EVENT_KINDS[e] for e in rows["event"]] == ["double_play", "single", "out"]


def test_a_cell_with_few_pitches_goes_towards_the_pooled_value_and_one_with_many_stays():
    group = {"n": np.zeros((4, 3, 8)), "sum": np.zeros((4, 3, 8))}; pooled = {"n": np.full((4, 3, 8), 1000.0), "sum": np.full((4, 3, 8), 500.0)}      # pooled mean 0.5
    group["n"][0, 0, 0], group["sum"][0, 0, 0] = 10.0, 10.0 * 1.5                               # 10 pitches that average 1.5
    group["n"][0, 0, 1], group["sum"][0, 0, 1] = 2000.0, 2000.0 * 1.5                            # 2000 pitches that average 1.5
    t = bt.smooth(group, pooled, 50.0)
    assert t[0, 0, 0] == pytest.approx((15.0 + 25.0) / 60.0) and 0.5 < t[0, 0, 0] < 1.5       # pulled a long way towards 0.5
    assert t[0, 0, 1] == pytest.approx(1.5, abs=0.03)                                           # hardly moved
    assert t[1, 1, 1] == pytest.approx(0.5)                                                     # no pitches of its own: the pooled value
    assert bt.smooth(group, pooled, 0.0)[0, 0, 0] == pytest.approx(1.5)                         # kappa 0: the cell mean itself


def test_kappa_follows_how_much_the_groups_own_cells_can_be_trusted():
    rng = np.random.default_rng(1)
    cells = [(0, 0, b) for b in range(8) for _ in range(30)]

    def stats(values):
        n = np.zeros((4, 3, 8)); s = np.zeros((4, 3, 8))
        for v, c in zip(values, cells):
            n[c] += 1; s[c] += v
        return {"n": n, "sum": s}

    def test_rows(true):
        return {"type": np.array([c[0] for c in cells]), "outs": np.array([c[1] for c in cells]), "bases": np.array([c[2] for c in cells]), "value": true + rng.normal(0, 0.05, len(cells))}

    pooled = {"n": np.full((4, 3, 8), 5000.0), "sum": np.full((4, 3, 8), 5000.0 * 0.5)}
    same = stats(0.5 + rng.normal(0, 0.05, len(cells)))                                          # the group's cells look like the pooled value: lean on the pooled value
    k_same, _ = bt.choose_kappa(same, pooled, test_rows(0.5), [0, 10, 100, 1000])
    other = stats(1.5 + rng.normal(0, 0.05, len(cells)))                                         # the group really differs and its cells are reliable: trust them
    k_other, _ = bt.choose_kappa(other, pooled, test_rows(1.5), [0, 10, 100, 1000])
    assert k_same > k_other and k_other <= 10


def test_the_pitch_type_differences_take_the_outs_and_runners_out():
    t = np.zeros(8, dtype=int); o = np.zeros(8, dtype=int); B = np.array([0, 0, 0, 0, 7, 7, 7, 7])
    value = np.array([0.3, 0.3, 0.3, 0.3, 1.0, 1.0, 1.0, 1.0])                                    # the cells differ a lot, the pitch types do not
    rows = {"type": t, "outs": o, "bases": B, "value": value, "pitch_type": np.array(["FF", "SL", "FF", "SL", "FF", "SL", "FF", "SL"])}
    out = {(r["batted_ball"], r["pitch_type"]): r for r in bt.pitch_type_differences(rows, ["FF", "SL"])}
    assert out[("ground_ball", "FF")]["mean_loss"] == pytest.approx(0.65) and out[("ground_ball", "FF")]["mean_after_cells"] == pytest.approx(0.0)
