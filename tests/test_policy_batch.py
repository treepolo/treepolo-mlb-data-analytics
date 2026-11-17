"""The batched decision engine gives what ``policy_core.Engine`` gives, pitcher by pitcher, and the batch size follows the resource guard.

Everything here is a tiny fixed world (a 2 x 2 count shape, three pitchers, probabilities that end a plate appearance within a few pitches), so the whole file runs in a few seconds."""
from __future__ import annotations

import numpy as np
import pytest

from treepolo_mlb_data.research import policy_batch as pbt, policy_core as pc
from treepolo_mlb_data.research.resource_guard import GB, Guard, Snapshot

torch = pytest.importorskip("torch")

SHAPE = pc.Shape(2, 2, 3)
TOL, MAX_ITER = 1e-10, 400


@pytest.fixture(scope="module")
def world():
    S, A = SHAPE.size, SHAPE.n_actions
    env = pc.Environment(re_pa=0.3 + 0.1 * np.arange(24).reshape(3, 8) / 8.0, re_cnt=0.2 + 0.05 * np.arange(2 * 2 * 3 * 8).reshape(2, 2, 3, 8) / 24.0,
                         jbip=0.5 + 0.037 * (np.arange(A) ** 2)[:, None, None] + 0.01 * np.arange(24).reshape(1, 3, 8) / 24.0, start=1.0 + np.arange(24).reshape(3, 8) / 24.0)
    base = np.array([0.25, 0.10, 0.10, 0.05, 0.01, 0.04, 0.30, 0.15])                              # ball, called strike, foul, foul tip, hit by pitch, home run, in play, whiff: a plate appearance ends fast
    P = np.zeros((3, S, A, pc.C))
    for b in range(3):
        for a in range(A):
            p = base.copy(); p[0] += 0.02 * a; p[6] -= 0.02 * a; p[7] += 0.01 * b; p[6] -= 0.01 * b
            P[b, :, a, :] = p
    s_idx = np.arange(S)[:, None]; a_idx = np.arange(A)[None, :]
    support = np.stack([((a_idx + s_idx + b) % 3) != 0 for b in range(3)])
    support[:, ::7] = False                                                                          # states where nothing may be given: they play what was observed
    support[2, :, 2:] = False                                                                        # the third pitcher may be given only two types
    share = np.stack([1.0 + ((a_idx * 3 + s_idx + b) % 5) for b in range(3)]).astype(float)
    obs = share / share.sum(axis=2, keepdims=True)
    return env, P, obs, support


def test_the_batched_engine_gives_what_the_one_by_one_engine_gives(world):
    env, P, obs, support = world
    kinds = ("const_FF", "greedy1", "dp_unrestricted", "dp_trust")
    eng = pbt.BatchEngine(SHAPE, env, tol=TOL, max_iter=MAX_ITER).use_device("cpu")
    out = eng.solve_kinds(eng.tensor(P), eng.tensor(obs), eng.tensor(support, dtype=torch.bool), kinds, 0.10, hr_kinds=("observed", "dp_trust"))
    for b in range(3):
        one = pc.Engine(SHAPE, pc.Dynamics(P=P[b], obs=obs[b], support=support[b], n=None), env, tol=TOL, max_iter=MAX_ITER)
        for kind in ("observed", *kinds):
            V, pi = one.solve(kind, 0.10)
            got = out[kind]["pi"][b].numpy()
            assert np.allclose(got, pi, atol=1e-10, rtol=0), kind                                    # the shares
            assert (np.argmax(got, axis=1) == np.argmax(pi, axis=1)).all(), kind                     # the recommended type in every state
            assert out[kind]["start"][b] == pytest.approx(one.start_value(V), abs=1e-10), kind       # the value
        for kind in ("observed", "dp_trust"):
            assert out[kind]["hr"][b] == pytest.approx(one.hr_probability(out[kind]["pi"][b].numpy()), abs=1e-10)


def test_a_pitcher_is_not_changed_by_the_others_in_his_batch(world):
    env, P, obs, support = world
    eng = pbt.BatchEngine(SHAPE, env, tol=TOL, max_iter=MAX_ITER).use_device("cpu")
    alone = eng.solve_kinds(eng.tensor(P[:1]), eng.tensor(obs[:1]), eng.tensor(support[:1], dtype=torch.bool), ("dp_trust",), 0.10)
    together = eng.solve_kinds(eng.tensor(P), eng.tensor(obs), eng.tensor(support, dtype=torch.bool), ("dp_trust",), 0.10)
    assert np.array_equal(alone["dp_trust"]["pi"][0].numpy(), together["dp_trust"]["pi"][0].numpy())
    assert alone["dp_trust"]["start"][0] == together["dp_trust"]["start"][0]


def test_the_same_probabilities_for_every_pitcher_give_the_same_answer_as_one_set_per_pitcher(world):
    env, P, obs, support = world
    eng = pbt.BatchEngine(SHAPE, env, tol=TOL, max_iter=MAX_ITER).use_device("cpu")
    shared = eng.solve_kinds(eng.tensor(P[0]), eng.tensor(obs), eng.tensor(support, dtype=torch.bool), ("dp_trust", "dp_unrestricted"), 0.10)
    each = eng.solve_kinds(eng.tensor(np.stack([P[0]] * 3)), eng.tensor(obs), eng.tensor(support, dtype=torch.bool), ("dp_trust", "dp_unrestricted"), 0.10)
    for kind in ("dp_trust", "dp_unrestricted"):
        assert np.array_equal(shared[kind]["pi"].numpy(), each[kind]["pi"].numpy()) and np.array_equal(shared[kind]["start"], each[kind]["start"])


class StubEngine:
    def __init__(self, per_item=50e6):
        self.per_item = per_item

    def use_device(self, device):
        self.device = device
        return self

    def bytes_per_item(self, copies=6.0):
        return self.per_item


class Machine:
    def __init__(self, available):
        self.available = available

    def __call__(self):
        return Snapshot(ram_total=16 * GB, ram_available=int(self.available), cpu_system=10.0, cpu_ours=5.0, rss_ours=1 * GB)


def _batches(monkeypatch, available, n=400, per_item=50e6):
    g = Guard(Machine(available), sleep=lambda s: None)
    monkeypatch.setattr(pbt, "guard", g)
    monkeypatch.setattr(g, "device", lambda requested="auto": "cpu")
    sizes = []
    pbt.run_in_batches(StubEngine(per_item), n, lambda sl, size: sizes.append(size) or list(range(sl.start, sl.stop)))
    return sizes


def test_the_batch_size_shrinks_when_less_memory_is_free_and_every_pitcher_is_done_once(monkeypatch):
    roomy = _batches(monkeypatch, 12 * GB)
    tight = _batches(monkeypatch, 5 * GB)
    assert sum(roomy) == sum(tight) == 400 and max(roomy) > max(tight) >= 1
    assert max(_batches(monkeypatch, 5 * GB, n=20, per_item=40e9)) == 1                                # one pitcher needs more than what is free: one at a time, but it goes on


def test_a_batch_that_runs_out_of_memory_is_halved_and_tried_again(monkeypatch):
    g = Guard(Machine(12 * GB), sleep=lambda s: None)
    monkeypatch.setattr(pbt, "guard", g); monkeypatch.setattr(g, "device", lambda requested="auto": "cpu")
    seen = []

    def work(sl, size):
        seen.append(size)
        if size > 8:
            raise MemoryError("not enough")
        return list(range(sl.start, sl.stop))

    out = pbt.run_in_batches(StubEngine(), 64, work)
    assert sorted(x for part in out for x in part) == list(range(64))                                 # every pitcher is done once
    first_ok = next(i for i, size in enumerate(seen) if size <= 8)
    assert all(seen[i] == seen[i - 1] // 2 for i in range(1, first_ok + 1)) and seen[0] > 8              # each failure halves the size, down to one that fits
    assert all(size <= seen[first_ok] for size in seen[first_ok:])                                     # and the size that failed is not tried again
