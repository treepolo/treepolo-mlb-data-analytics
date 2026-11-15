"""The protection of the user's computer: what counts as 'enough is left', waiting and stopping safely, pieces sized from what is free now, priority and threads, the monitor log, and that
every research run goes through it. The machine is replaced by a fake that returns the numbers a test wants."""
from __future__ import annotations

import threading
import time

import pytest

from treepolo_mlb_data.research.resource_guard import GB, Guard, Limits, ResourceExhausted, Snapshot

TOTAL = 16 * GB


class Machine:
    """A fake computer: ``available`` and the others can be changed by a test while the guard looks at it."""

    def __init__(self, available=10 * GB, **kw):
        self.values = dict(ram_total=TOTAL, ram_available=int(available), cpu_system=10.0, cpu_ours=5.0, rss_ours=1 * GB)
        self.values.update(kw)
        self.calls = 0

    def __call__(self) -> Snapshot:
        self.calls += 1
        return Snapshot(**self.values)


def make(machine, **kw):
    sleeps: list[float] = []
    g = Guard(machine, sleep=sleeps.append, **kw)
    g._sleeps = sleeps
    return g


def test_what_is_left_for_the_user_comes_from_the_size_of_the_machine():
    lim = Limits.for_total(TOTAL)
    assert lim.ram_soft == int(0.2 * TOTAL) and lim.ram_hard == max(int(1.5 * GB), int(0.1 * TOTAL))
    small = Limits.for_total(8 * GB)
    assert small.ram_soft == int(3.0 * GB) and small.ram_hard == int(1.5 * GB)


@pytest.mark.parametrize("available,state", [(10 * GB, "ok"), (3.0 * GB, "pressure"), (1.0 * GB, "critical")])
def test_the_state_follows_the_memory_that_is_free(available, state):
    g = make(Machine(available))
    assert g.judge(g.snapshot(max_age=0))[0] == state


def test_the_virtual_memory_and_the_graphics_memory_count_too():
    g = make(Machine(10 * GB, commit_limit=20 * GB, commit_used=18 * GB))
    assert g.judge(g.snapshot(max_age=0))[0] == "critical"                           # 2 GB of virtual memory left: the pagefile is about to run out
    g = make(Machine(10 * GB, gpu_total=4 * GB, gpu_used=int(3.8 * GB)))
    assert g.judge(g.snapshot(max_age=0))[0] == "critical"
    g = make(Machine(10 * GB, gpu_total=4 * GB, gpu_used=int(3.3 * GB)))
    assert g.judge(g.snapshot(max_age=0))[0] == "pressure"                            # less than the 1 GB kept for the display


def test_a_checkpoint_stops_safely_when_the_computer_is_nearly_out_of_memory():
    g = make(Machine(1.0 * GB))
    with pytest.raises(ResourceExhausted, match="memory"):
        g.checkpoint("training")


def test_a_checkpoint_waits_while_resources_are_scarce_and_goes_on_when_they_come_back():
    m = Machine(3.0 * GB)
    g = make(m)
    g._sleep = lambda s: m.values.update(ram_available=int(10 * GB))               # the user closes a program while the run waits
    assert g.checkpoint("training") == "ok"
    m2 = Machine(3.0 * GB)
    g2 = make(m2)
    with pytest.raises(ResourceExhausted, match="stayed scarce"):
        g2.checkpoint("training", wait=5)                                            # nothing changes: stop instead of squeezing the machine
    assert sum(g2._sleeps) >= 5


def test_a_step_that_needs_more_than_the_machine_can_spare_is_refused():
    g = make(Machine(6 * GB))
    g.require(int(2.0 * GB), "a small step")                                          # 6 - 2 = 4 GB stays free, above the 3.2 GB kept for the user
    with pytest.raises(ResourceExhausted, match="needs about"):
        g.require(int(4.0 * GB), "loading")


def test_pieces_of_work_are_sized_from_the_memory_free_now_and_shrink_under_pressure():
    big = make(Machine(12 * GB)).chunk_rows(1000.0)
    small = make(Machine(5 * GB)).chunk_rows(1000.0)
    assert big > small >= 2000
    g = make(Machine(12 * GB)); g._state = "pressure"
    assert g.chunk_rows(1000.0) < big
    assert make(Machine(1 * GB)).chunk_rows(1000.0) == 2000                          # never zero


def test_the_threads_leave_at_least_two_cores_to_the_others(monkeypatch):
    monkeypatch.setattr("os.cpu_count", lambda: 6)
    g = make(Machine(10 * GB, cpu_system=5.0, cpu_ours=0.0))
    assert 1 <= g.pick_threads() <= 4
    busy = make(Machine(10 * GB, cpu_system=100.0, cpu_ours=0.0))
    assert busy.pick_threads() == 1                                                    # the others use everything: one thread
    monkeypatch.setattr("os.cpu_count", lambda: 2)
    assert make(Machine()).pick_threads() == 1


def test_a_run_lowers_its_priority_writes_the_monitor_log_and_restores_everything(tmp_path):
    import psutil

    before = psutil.Process().nice()
    g = make(Machine(10 * GB))
    g.interval = 0.05
    log = tmp_path / "monitor.log"
    with g.job("test run", log_path=log):
        if psutil.Process().nice() == before and psutil.Process().nice() != psutil.BELOW_NORMAL_PRIORITY_CLASS:
            pytest.fail("the priority was not lowered")
        time.sleep(0.3)
        with g.job("nested"):                                                           # a run inside a run changes nothing
            assert g._depth == 2
    assert psutil.Process().nice() == before
    text = log.read_text(encoding="utf-8")
    assert "start test run" in text and "ram_free=10.00GB" in text and "end test run" in text and g.peaks["rss_ours_gb"] == pytest.approx(1.0)
    assert g._thread is None and g._depth == 0


def test_a_run_that_never_reaches_a_checkpoint_is_interrupted_when_the_machine_stays_in_trouble(tmp_path):
    m = Machine(10 * GB)
    g = make(m)
    g.interval, g.critical_grace = 0.05, 0.2
    caught = []

    def work():
        try:
            with g.job("stuck", log_path=tmp_path / "m.log"):
                m.values["ram_available"] = int(1.0 * GB)
                end = time.time() + 10
                while time.time() < end:                                               # long Python code without a checkpoint
                    sum(range(1000))
        except ResourceExhausted as exc:
            caught.append(exc)

    t = threading.Thread(target=work); t.start(); t.join(15)
    assert caught and not t.is_alive()


def test_work_on_the_card_is_cut_into_short_pieces_that_adapt_to_how_long_a_piece_takes():
    clock = {"t": 0.0}
    g = Guard(Machine(), sleep=lambda s: None, clock=lambda: clock["t"])
    seen = []
    for sl in g.gpu_chunks(1_000_000, 100.0, cap=64_000):
        seen.append(sl.stop - sl.start)
        clock["t"] += 0.5                                                               # every piece "takes" half a second: far over the target
    assert sum(seen) == 1_000_000 and seen[0] == 64_000 and min(seen) < seen[0] and seen[-1] <= seen[0]
    fast = []
    for sl in g.gpu_chunks(1_000_000, 100.0, cap=64_000):
        fast.append(sl.stop - sl.start)
        clock["t"] += 0.001
    assert sum(fast) == 1_000_000 and max(fast) == 64_000                              # quick pieces grow back to the cap


def test_the_card_is_not_used_when_it_was_not_asked_for():
    assert Guard(Machine()).device("cpu") == "cpu"


def test_every_research_run_goes_through_the_guard(tmp_path):
    from treepolo_mlb_data.config import AppConfig
    from treepolo_mlb_data.research.methods import ResearchMethod, ResearchResult, register_method
    from treepolo_mlb_data.research.service import ResearchService

    seen = {}

    class Probe(ResearchMethod):
        kind = "guard_probe"
        version = 1
        fields = ()

        def run(self, ctx, config):
            from treepolo_mlb_data.research.resource_guard import guard

            seen["depth"] = guard._depth; seen["threads"] = guard.threads
            return ResearchResult(sections=(), extras={})

    register_method(Probe())
    data = tmp_path / "data"; data.mkdir()
    service = ResearchService(AppConfig(data_dir=str(data)), None)
    out = service.run("guard_probe", {})
    assert out["run"]["status"] == "success" and seen["depth"] == 1 and seen["threads"] >= 1
    log = service.config.research_state_database_path.with_name("research_resource_monitor.log") if hasattr(service.config.research_state_database_path, "with_name") else None
    from pathlib import Path

    assert Path(log).exists() and "research:guard_probe" in Path(log).read_text(encoding="utf-8")


def test_the_card_rests_only_after_enough_work_has_piled_up_not_after_every_tiny_call():
    g = make(Machine())
    g._rest(5.0)
    assert g._sleeps == []                                                              # all well: no routine pause at all, however long the card works
    g.duty = 0.35                                                                       # the card is hot: now it rests
    for _ in range(2):
        g._rest(0.05)
    assert g._sleeps == []                                                              # 0.10 s of work: no rest yet (a rest is never shorter than 15 ms on Windows)
    g._rest(0.06)
    assert len(g._sleeps) == 1 and g._sleeps[0] == pytest.approx(0.16 * g.duty)
    g._rest(0.01)
    assert len(g._sleeps) == 1
