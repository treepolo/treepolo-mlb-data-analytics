import sqlite3
import threading

import pytest

from research_fixtures import INGESTED, make_research_db
from treepolo_mlb_data.config import AppConfig
from treepolo_mlb_data.research.config_schema import ConfigError, ConfigField
from treepolo_mlb_data.research.methods import ArtifactData, ResearchMethod, ResearchResult, register_method
from treepolo_mlb_data.research.service import ResearchService, code_version, make_run_key
from treepolo_mlb_data.web_analysis import AnalysisFacade


class Counting(ResearchMethod):
    kind = "counting_for_service_test"
    version = 1
    requires_scope = True
    fields = (ConfigField("n", "int", default=1, minimum=1), ConfigField("fail", "bool", default=False),
              ConfigField("vary", "bool", default=False), ConfigField("sleep", "bool", default=False))
    calls = 0
    gate = threading.Event()
    entered = threading.Event()

    def validate(self, config, ctx):
        if config["n"] == 99:
            raise ConfigError("n=99 is not allowed")

    def run(self, ctx, config):
        type(self).calls += 1
        if config["sleep"]:
            type(self).entered.set()
            type(self).gate.wait(5)
        if config["fail"]:
            raise RuntimeError("boom")
        rows = [{"v": i} for i in range(5)]
        extra = {"calls": type(self).calls} if config["vary"] else {}
        section = {"title": "S", "columns": ["v"], "rows": rows, "grain": {"keys": ["v"], "label": "x"}, "row_count": 5, "backend": "t"}
        art = ArtifactData("detail", "per-row", ("v",), tuple(rows))
        return ResearchResult(sections=(section,), extras=extra, artifacts=(art,))


METHOD = Counting()
register_method(METHOD)
SCOPE = {"game_years": [2024]}


@pytest.fixture()
def env(tmp_path):
    db = make_research_db(tmp_path / "statcast.sqlite3")
    config = AppConfig(data_dir=str(tmp_path), analysis_backend="sqlite")
    service = ResearchService(config, AnalysisFacade(db, backend="sqlite"))
    Counting.calls = 0
    Counting.gate = threading.Event()
    Counting.entered = threading.Event()
    yield service, db
    service.close()


def test_config_errors_create_no_records(env):
    service, _ = env
    for cfg in ({"scope": SCOPE, "typo": 1}, {"scope": SCOPE, "n": 0}, {"scope": SCOPE, "n": 99}, {}, {"scope": {"game_years": [2019]}}):
        with pytest.raises(ConfigError):
            service.run("counting_for_service_test", cfg)
    assert service.list_runs() == [] and Counting.calls == 0
    with pytest.raises(ConfigError, match="Unknown research method"):
        service.run("nope", {})


def test_second_identical_run_is_reused_without_running(env):
    service, _ = env
    first = service.run("counting_for_service_test", {"scope": SCOPE}, study_name="A")
    second = service.run("counting_for_service_test", {"scope": SCOPE, "n": 1}, study_name="B")
    assert first["reused"] is False and second["reused"] is True
    assert first["run"]["id"] == second["run"]["id"] and Counting.calls == 1
    assert [s["name"] for s in second["run"]["studies"]] == ["A", "B"]
    run = first["run"]
    assert run["status"] == "success" and run["summary"]["total_rows"] == 5 and run["summary"]["artifact_names"] == ["detail"]
    assert run["config"] == {"n": 1, "fail": False, "vary": False, "sleep": False, "scope": {"game_years": [2024], "game_types": ["R"]}}
    assert run["data_fingerprint"]["total_rows"] == 4 and run["code_version"] == code_version()
    assert service.store.load_result(run["id"])["sections"][0]["rows"][4] == {"v": 4}
    assert service.store.load_artifact(run["id"], "detail")["rows"][0] == {"v": 0}


def test_default_study_and_explicit_study_id(env):
    service, _ = env
    run = service.run("counting_for_service_test", {"scope": SCOPE})["run"]
    assert [s["name"] for s in run["studies"]] == ["未分類 Unsorted"]
    study = service.create_study("mine")
    other = service.run("counting_for_service_test", {"scope": SCOPE, "n": 2}, study_id=study["id"])["run"]
    assert [s["name"] for s in other["studies"]] == ["mine"]
    with pytest.raises(ConfigError, match="Study not found"):
        service.run("counting_for_service_test", {"scope": SCOPE}, study_id=9999)


def test_data_changes_inside_scope_make_a_new_run_but_outside_do_not(env):
    service, db = env
    first = service.run("counting_for_service_test", {"scope": SCOPE})["run"]
    conn = sqlite3.connect(db)
    conn.execute("UPDATE settings SET value='rev-2'")
    conn.execute("UPDATE pitches SET _ingested_at='2030-01-01T00:00:00+00:00' WHERE game_year=2023")
    conn.commit()
    again = service.run("counting_for_service_test", {"scope": SCOPE})
    assert again["reused"] is True and again["run"]["id"] == first["id"]
    conn.execute("UPDATE settings SET value='rev-3'")
    conn.execute("UPDATE pitches SET _ingested_at='2030-02-01T00:00:00+00:00' WHERE game_year=2024")
    conn.commit(); conn.close()
    changed = service.run("counting_for_service_test", {"scope": SCOPE})
    assert changed["reused"] is False and changed["run"]["id"] != first["id"]


def test_empty_scope_is_an_error(env):
    service, _ = env
    with pytest.raises(ConfigError, match="no pitches"):
        service.run("counting_for_service_test", {"scope": {"game_years": [2024], "game_types": ["W"]}})


def test_failed_run_is_recorded_and_can_be_retried(env):
    service, _ = env
    with pytest.raises(RuntimeError, match="boom"):
        service.run("counting_for_service_test", {"scope": SCOPE, "fail": True})
    runs = service.list_runs()
    assert len(runs) == 1 and runs[0]["status"] == "failed" and "boom" in runs[0]["error"]
    with pytest.raises(RuntimeError):
        service.run("counting_for_service_test", {"scope": SCOPE, "fail": True})
    assert len(service.list_runs()) == 1  # same row reused for the retry
    # a different config succeeds independently
    ok = service.run("counting_for_service_test", {"scope": SCOPE})
    assert ok["run"]["status"] == "success" and len(service.list_runs()) == 2


def test_force_reports_reproducibility(env):
    service, _ = env
    first = service.run("counting_for_service_test", {"scope": SCOPE})
    same = service.run("counting_for_service_test", {"scope": SCOPE}, force=True)
    assert same["reused"] is False and same["reproduced"] is True and same["run"]["id"] == first["run"]["id"]
    varying = service.run("counting_for_service_test", {"scope": SCOPE, "vary": True})
    diff = service.run("counting_for_service_test", {"scope": SCOPE, "vary": True}, force=True)
    assert varying["reproduced"] is None and diff["reproduced"] is False
    assert diff["run"]["summary"]["reproduced"] is False
    assert diff["run"]["summary"]["previous_result_hash"] == varying["run"]["result_hash"]
    assert diff["run"]["result_hash"] != varying["run"]["result_hash"]


def test_same_run_cannot_start_twice_concurrently(env):
    service, _ = env
    outcome = {}

    def first():
        outcome["first"] = service.run("counting_for_service_test", {"scope": SCOPE, "sleep": True})

    thread = threading.Thread(target=first)
    thread.start()
    assert Counting.entered.wait(5)
    with pytest.raises(ConfigError, match="already in progress"):
        service.run("counting_for_service_test", {"scope": SCOPE, "sleep": True})
    Counting.gate.set()
    thread.join(5)
    assert outcome["first"]["run"]["status"] == "success"


def test_local_data_match_and_rerun(env):
    service, db = env
    run = service.run("counting_for_service_test", {"scope": SCOPE})["run"]
    assert service.run_detail(run["id"])["local_data_match"] is True
    conn = sqlite3.connect(db)
    conn.execute("UPDATE settings SET value='rev-9'")
    conn.execute("UPDATE pitches SET _ingested_at='2031-01-01T00:00:00+00:00' WHERE game_year=2024")
    conn.commit(); conn.close()
    assert service.run_detail(run["id"])["local_data_match"] is False
    rerun = service.rerun(run["id"])
    assert rerun["reused"] is False and rerun["run"]["id"] != run["id"] and rerun["run"]["parent_run_id"] == run["id"]
    assert service.run_detail(rerun["run"]["id"])["local_data_match"] is True
    assert service.run_detail(9999) is None
    with pytest.raises(ConfigError):
        service.rerun(9999)


def test_analysis_payload_without_scope_and_match_is_none(env):
    service, _ = env
    payload = {"mode": "basic", "group_by": ["pitch_type"], "metrics": [{"function": "count"}]}
    out = service.run("analysis_payload", {"payload": payload}, study_name="payloads")
    assert out["run"]["scope"] == {} and out["run"]["data_fingerprint"]["scope_fingerprint"] == "revision:rev-1"
    assert service.run_detail(out["run"]["id"])["local_data_match"] is None
    assert service.run("analysis_payload", {"payload": payload})["reused"] is True


def test_result_page_paging_and_limits(env):
    service, _ = env
    run = service.run("counting_for_service_test", {"scope": SCOPE})["run"]
    page = service.result_page(run["id"], 0, offset=3, limit=10)
    assert page["total"] == 5 and page["rows"] == [{"v": 3}, {"v": 4}] and page["section"]["title"] == "S" and "rows" not in page["section"]
    for kwargs in ({"limit": 0}, {"limit": 1001}, {"offset": -1}, {"section_index": 3}):
        with pytest.raises(ConfigError):
            service.result_page(run["id"], **({"section_index": 0} | kwargs))
    with pytest.raises(ConfigError, match="no result"):
        service.result_page(9999)


def test_run_key_is_stable_and_sensitive():
    base = make_run_key("k", 1, {"a": 1, "b": [1, 2]}, "fp")
    assert base == make_run_key("k", 1, {"b": [1, 2], "a": 1}, "fp")
    assert base != make_run_key("k", 2, {"a": 1, "b": [1, 2]}, "fp")
    assert base != make_run_key("k", 1, {"a": 2, "b": [1, 2]}, "fp")
    assert base != make_run_key("k", 1, {"a": 1, "b": [1, 2]}, "fp2")


def test_link_run_actions(env):
    service, _ = env
    run = service.run("counting_for_service_test", {"scope": SCOPE})["run"]
    study = service.create_study("x")
    assert service.link_run(run["id"], study["id"], "add") is True
    assert service.link_run(run["id"], study["id"], "remove") is True
    assert service.link_run(run["id"], study["id"], "remove") is False
    for args in ((run["id"], study["id"], "bad"), (9999, study["id"], "add"), (run["id"], 9999, "add")):
        with pytest.raises(ConfigError):
            service.link_run(*args)
