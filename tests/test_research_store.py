import sqlite3
import threading

import pytest

from treepolo_mlb_data.research.config_schema import ConfigError
from treepolo_mlb_data.research.store import ResearchStore


def new_store(tmp_path):
    return ResearchStore(tmp_path / "research.sqlite3", tmp_path / "research_runs")


def add_run(store, key="k1", **overrides):
    args = dict(run_key=key, kind="demo", method_version=1, config={"a": 1}, scope={}, data_fingerprint={"scope_fingerprint": "x"},
                code_version="0.1.0")
    args.update(overrides)
    return store.insert_run(**args)


def section(title="T", rows=None):
    rows = rows if rows is not None else [{"x": 1}, {"x": 2}]
    return {"title": title, "columns": ["x"], "rows": rows, "grain": {"keys": ["x"], "label": "t"}, "row_count": len(rows), "backend": "test"}


def test_schema_is_idempotent_and_format_version_is_enforced(tmp_path):
    store = new_store(tmp_path); store.close()
    store = new_store(tmp_path)
    assert store.conn.execute("SELECT value FROM schema_info WHERE key='format_version'").fetchone()[0] == "research-v1"
    store.conn.execute("UPDATE schema_info SET value='research-v0'"); store.conn.commit(); store.close()
    with pytest.raises(RuntimeError, match="Unsupported research store format"):
        new_store(tmp_path)


def test_blob_roundtrip_dedupes_and_verifies(tmp_path):
    store = new_store(tmp_path)
    h1, size = store.write_json_blob({"b": 1, "a": [1, 2]})
    h2, _ = store.write_json_blob({"a": [1, 2], "b": 1})
    assert h1 == h2 and size > 0
    assert store.read_json_blob(h1) == {"a": [1, 2], "b": 1}
    assert store.conn.execute("SELECT COUNT(*) FROM blobs").fetchone()[0] == 1
    gz = store.read_blob_gzip_bytes(h1)
    other = new_store(tmp_path / "other")
    other.write_blob_from_gzip(h1, gz)
    assert other.read_json_blob(h1) == {"a": [1, 2], "b": 1}
    with pytest.raises(ConfigError, match="corrupted"):
        other.write_blob_from_gzip("0" * 64, gz)
    with pytest.raises(ConfigError, match="corrupted"):
        other.write_blob_from_gzip(h1, b"not gzip")
    with pytest.raises(FileNotFoundError):
        store.read_json_blob("f" * 64)


def test_run_lifecycle_and_result_loading(tmp_path):
    store = new_store(tmp_path)
    run_id = add_run(store)
    assert store.get_run(run_id)["status"] == "running"
    done = store.finish_run(run_id, result={"sections": [section()]}, summary={"total_rows": 2}, duration_seconds=1.5)
    assert done["status"] == "success" and done["summary"] == {"total_rows": 2} and done["duration_seconds"] == 1.5
    assert store.load_result(run_id)["sections"][0]["rows"] == [{"x": 1}, {"x": 2}]
    assert store.find_run_by_key("k1")["id"] == run_id and store.find_run_by_key("nope") is None
    fail_id = add_run(store, "k2")
    store.fail_run(fail_id, "boom", 0.1)
    failed = store.get_run(fail_id)
    assert failed["status"] == "failed" and failed["error"] == "boom" and store.load_result(fail_id) is None
    store.restart_run(fail_id, code_version="0.2.0", data_fingerprint={"scope_fingerprint": "y"})
    restarted = store.get_run(fail_id)
    assert restarted["status"] == "running" and restarted["error"] is None and restarted["code_version"] == "0.2.0"


def test_artifacts_are_stored_separately(tmp_path):
    store = new_store(tmp_path)
    run_id = add_run(store)
    store.finish_run(run_id, result={"sections": []}, artifacts=[{"name": "detail", "description": "d", "columns": ["a", "b"], "rows": [{"a": 1, "b": 2}]}])
    run = store.get_run(run_id)
    assert run["artifacts"] == [{"name": "detail", "description": "d", "columns": ["a", "b"], "row_count": 1,
                                 "blob_hash": run["artifacts"][0]["blob_hash"], "blob_bytes": run["artifacts"][0]["blob_bytes"]}]
    assert store.load_artifact(run_id, "detail") == {"columns": ["a", "b"], "rows": [{"a": 1, "b": 2}]}
    assert store.load_artifact(run_id, "missing") is None


def test_delete_run_collects_only_unreferenced_blobs(tmp_path):
    store = new_store(tmp_path)
    a, b = add_run(store, "ka"), add_run(store, "kb")
    shared = {"sections": [section("shared")]}
    store.finish_run(a, result=shared)
    store.finish_run(b, result=shared, artifacts=[{"name": "only_b", "columns": ["z"], "rows": [{"z": 1}]}])
    shared_hash = store.get_run(a)["result_hash"]
    only_b = store.get_run(b)["artifacts"][0]["blob_hash"]
    assert store.delete_run(a) is True
    assert store.read_json_blob(shared_hash)  # still used by b
    assert store.delete_run(b) is True
    with pytest.raises(FileNotFoundError):
        store.read_json_blob(shared_hash)
    with pytest.raises(FileNotFoundError):
        store.read_json_blob(only_b)
    assert store.conn.execute("SELECT COUNT(*) FROM blobs").fetchone()[0] == 0
    assert store.delete_run(a) is False


def test_recover_interrupted_marks_running_runs_failed(tmp_path):
    store = new_store(tmp_path)
    run_id = add_run(store)
    store.close()
    again = new_store(tmp_path)
    run = again.get_run(run_id)
    assert run["status"] == "failed" and "interrupted" in run["error"]


def test_studies_crud_and_links(tmp_path):
    store = new_store(tmp_path)
    s1 = store.create_study("  Decay  ", purpose="p")
    assert s1["name"] == "Decay" and s1["origin"] == "local" and len(s1["study_uid"]) == 32 and s1["run_count"] == 0
    with pytest.raises(ConfigError, match="already exists"):
        store.create_study("Decay")
    with pytest.raises(ConfigError):
        store.create_study("   ")
    assert store.get_or_create_study("Decay")["id"] == s1["id"]
    s2 = store.get_or_create_study("Other")
    run_id = add_run(store)
    store.link_run(s1["id"], run_id); store.link_run(s1["id"], run_id); store.link_run(s2["id"], run_id)
    assert store.get_run(run_id)["studies"] == [{"id": s1["id"], "name": "Decay"}, {"id": s2["id"], "name": "Other"}]
    assert {s["name"]: s["run_count"] for s in store.list_studies()} == {"Decay": 1, "Other": 1}
    updated = store.update_study(s1["id"], insight_markdown="# idea", name="Decay2")
    assert updated["name"] == "Decay2" and updated["insight_markdown"] == "# idea" and updated["purpose"] == "p"
    with pytest.raises(ConfigError, match="already exists"):
        store.update_study(s1["id"], name="Other")
    assert store.update_study(9999, name="x") is None
    assert store.unlink_run(s2["id"], run_id) is True and store.unlink_run(s2["id"], run_id) is False
    assert store.get_study_by_uid(s1["study_uid"])["id"] == s1["id"]


def test_delete_study_only_removes_exclusive_runs(tmp_path):
    store = new_store(tmp_path)
    s1, s2 = store.create_study("one"), store.create_study("two")
    exclusive, shared = add_run(store, "ke"), add_run(store, "ks")
    store.link_run(s1["id"], exclusive); store.link_run(s1["id"], shared); store.link_run(s2["id"], shared)
    assert store.delete_study(s1["id"], delete_runs=True) is True
    assert store.get_run(exclusive) is None and store.get_run(shared) is not None
    assert store.delete_study(s2["id"]) is True
    assert store.get_run(shared) is not None  # unlinking keeps runs
    assert store.delete_study(s2["id"]) is False


def test_list_runs_filters_and_limits(tmp_path):
    store = new_store(tmp_path)
    study = store.create_study("s")
    ids = [add_run(store, f"k{i}", kind="a" if i % 2 else "b") for i in range(5)]
    store.link_run(study["id"], ids[0]); store.link_run(study["id"], ids[1])
    store.fail_run(ids[1], "x")
    assert [r["id"] for r in store.list_runs()] == ids[::-1]
    assert [r["id"] for r in store.list_runs(study_id=study["id"])] == [ids[1], ids[0]]
    assert {r["kind"] for r in store.list_runs(kind="a")} == {"a"}
    assert [r["id"] for r in store.list_runs(status="failed")] == [ids[1]]
    assert len(store.list_runs(limit=2, offset=1)) == 2
    for bad in ({"limit": 0}, {"limit": 1001}, {"offset": -1}):
        with pytest.raises(ConfigError):
            store.list_runs(**bad)


def test_concurrent_links_are_safe(tmp_path):
    store = new_store(tmp_path)
    study = store.create_study("s")
    ids = [add_run(store, f"k{i}") for i in range(20)]
    errors = []

    def worker(chunk):
        try:
            for run_id in chunk:
                store.link_run(study["id"], run_id)
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(ids[i::4],)) for i in range(4)]
    [t.start() for t in threads]; [t.join() for t in threads]
    assert not errors and store.get_study(study["id"])["run_count"] == 20
