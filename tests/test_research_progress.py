"""The progress the front end shows: the phases of a run, the job list with the time still to go, the estimate from earlier runs, and the wiring of the panel."""
from __future__ import annotations

import time

from treepolo_mlb_data import analysis_jobs as jobs
from treepolo_mlb_data.research import api
from treepolo_mlb_data.research.progress import Progress
from treepolo_mlb_data.research.service import ResearchService
from treepolo_mlb_data.webapp import STATIC_DIR


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def collect(phases=(("a", 1.0), ("b", 3.0)), **kw):
    seen: list[tuple[str, float, str | None]] = []
    clock = Clock()
    return Progress(lambda s, f, d: seen.append((s, f, d)), "x", phases, clock=clock, **kw), seen, clock


def test_the_fraction_follows_the_shares_of_the_phases_and_never_goes_backwards():
    p, seen, clock = collect()
    p.phase("a")
    clock.t = 1.0; p.step(1, 2)                                     # half of phase a (a quarter of the run)
    clock.t = 2.0; p.step(2, 2)
    clock.t = 3.0; p.phase("b")
    clock.t = 4.0; p.step(1, 4, phase="b")
    clock.t = 5.0; p.step(0, 4, phase="b")                           # a smaller count: the bar stays
    fractions = [f for _, f, _ in seen]
    assert fractions[0] == 0.0 and abs(fractions[1] - 0.125) < 1e-9 and abs(fractions[2] - 0.25) < 1e-9
    assert abs(fractions[3] - 0.25) < 1e-9 and abs(fractions[4] - (0.25 + 0.75 * 0.25)) < 1e-9
    assert fractions == sorted(fractions) and all(0.0 <= f <= 1.0 for f in fractions)


def test_updates_are_limited_by_the_clock_so_a_fast_loop_pays_nothing_for_them():
    p, seen, clock = collect(min_interval=0.5)
    p.phase("a")
    for i in range(1000):                                            # a thousand counts inside one second: only a few reports
        clock.t += 0.001; p.step(i, 2000)
    assert len(seen) <= 3
    clock.t += 1.0; p.step(2000, 2000)                               # the end of a phase is always reported
    assert abs(seen[-1][1] - 0.25) < 1e-9


def test_no_callback_means_no_work():
    p = Progress(None, "x", (("a", 1.0),))
    p.phase("a"); p.step(5, 10)                                      # nothing to call, nothing breaks


def test_the_job_list_works_out_the_time_still_to_go_and_leaves_old_finished_jobs_out():
    job_id = jobs.start_analysis_job("research:pitch_policy")
    jobs.update_analysis_job(job_id, stage="pitch_policy", percentage=25.0, detail="group LvL: pitcher engine 5/31")
    time.sleep(0.05)
    mine = [j for j in jobs.list_research_jobs() if j["job_id"] == job_id][0]
    assert mine["status"] == "running" and mine["percentage"] == 25.0
    assert mine["eta_seconds"] is not None and abs(mine["eta_seconds"] - 3 * mine["elapsed_seconds"]) < 1e-6        # 25% done: three times the time used is still to go
    assert not mine["estimated"]
    jobs.update_analysis_job(job_id, percentage=1.0)                  # the bar never goes backwards, so the 25% stays
    jobs.finish_analysis_job(job_id)
    done = [j for j in jobs.list_research_jobs() if j["job_id"] == job_id][0]
    assert done["status"] == "success" and done["eta_seconds"] is None
    assert all(j["job_id"] != job_id for j in jobs.list_research_jobs(recent_seconds=-1.0))
    other = jobs.start_analysis_job("analysis:something")               # a job of the analysis page is not a research job
    assert all(j["job_id"] != other for j in jobs.list_research_jobs())


def test_a_bar_that_follows_earlier_runs_is_labelled_as_an_estimate():
    job_id = jobs.start_analysis_job("research:outcome_model_train")
    jobs.update_analysis_job(job_id, expected_seconds=60.0, percentage=50.0, detail="sweep 12（依先前同類研究約 60 秒估計）")
    time.sleep(0.05)
    mine = [j for j in jobs.list_research_jobs() if j["job_id"] == job_id][0]
    assert mine["estimated"] and mine["expected_seconds"] == 60.0
    jobs.finish_analysis_job(job_id)


def test_the_expected_time_is_the_middle_of_the_last_runs_of_that_kind():
    class Store:
        def list_runs(self, **kw):
            assert kw["kind"] == "outcome_model_train" and kw["status"] == "success"
            return [{"duration_seconds": 80.0}, {"duration_seconds": 400.0}, {"duration_seconds": 90.0}, {"duration_seconds": 1.0}, {"duration_seconds": None}]

    class Fake:
        store = Store()

    assert ResearchService._expected_seconds(Fake(), "outcome_model_train") == 90.0         # 1 s (a trivial or reused run) and the missing one do not count

    class Empty:
        class store:
            @staticmethod
            def list_runs(**kw):
                return []

    assert ResearchService._expected_seconds(Empty(), "x") is None


def test_the_progress_endpoint_lists_the_jobs():
    job_id = jobs.start_analysis_job("research:policy_score")
    jobs.update_analysis_job(job_id, stage="policy_score", percentage=60.0, detail="group LvL: per-pitcher engine 10/31")
    status, body = api.handle_get(None, "/api/research/progress", "")
    assert status == 200 and any(j["job_id"] == job_id and j["percentage"] == 60.0 for j in body["jobs"])
    jobs.finish_analysis_job(job_id)


def test_the_progress_panel_is_wired_into_the_app():
    script = (STATIC_DIR / "research-progress.js").read_text(encoding="utf-8")
    index = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    ci = (STATIC_DIR.parents[2] / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert '<script src="/research-progress.js"></script>' in index and index.index("/research-pipeline-page.js") < index.index("/research-progress.js")
    assert "/api/research/progress" in script and "預估再" in script and "已用" in script
    assert "node --check src/treepolo_mlb_data/web_static/research-progress.js" in ci and "node tests/research-progress.test.cjs" in ci
    assert "innerHTML" not in script and "localStorage" not in script and "sessionStorage" not in script
