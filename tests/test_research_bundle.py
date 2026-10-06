import io
import json
import zipfile

import pytest

from research_fixtures import make_research_db
from treepolo_mlb_data.config import AppConfig
from treepolo_mlb_data.research.bundle import export_bundle, import_bundle
from treepolo_mlb_data.research.config_schema import ConfigError, ConfigField
from treepolo_mlb_data.research.methods import ArtifactData, ResearchMethod, ResearchResult, register_method
from treepolo_mlb_data.research.service import ResearchService
from treepolo_mlb_data.research.store import ResearchStore
from treepolo_mlb_data.web_analysis import AnalysisFacade


class BundleDemo(ResearchMethod):
    kind = "bundle_demo_for_test"
    requires_scope = True
    fields = (ConfigField("n", "int", default=1),)

    def run(self, ctx, config):
        rows = [{"v": i * config["n"]} for i in range(3)]
        section = {"title": "S", "columns": ["v"], "rows": rows, "grain": {"keys": ["v"], "label": "x"}, "row_count": 3, "backend": "t"}
        return ResearchResult((section,), {"n": config["n"]}, (ArtifactData("detail", "d", ("v",), tuple(rows)),))


register_method(BundleDemo())
SCOPE = {"game_years": [2024]}


def make_service(tmp_path, name):
    root = tmp_path / name
    db = make_research_db(root / "statcast.sqlite3")
    config = AppConfig(data_dir=str(root), analysis_backend="sqlite")
    return ResearchService(config, AnalysisFacade(db, backend="sqlite"))


@pytest.fixture()
def pair(tmp_path):
    a, b = make_service(tmp_path, "a"), make_service(tmp_path, "b")
    yield a, b
    a.close(); b.close()


def seeded(service):
    study = service.create_study("Decay", purpose="why", insight_markdown="# findings")
    r1 = service.run("bundle_demo_for_test", {"scope": SCOPE}, study_id=study["id"])["run"]
    r2 = service.run("bundle_demo_for_test", {"scope": SCOPE, "n": 2}, study_id=study["id"])["run"]
    return study, r1, r2


def test_roundtrip_preserves_runs_results_and_insights(pair):
    a, b = pair
    study, r1, r2 = seeded(a)
    data = export_bundle(a.store, study_ids=[study["id"]], notes="hello")
    report = import_bundle(b.store, data)
    assert (report["studies_added"], report["runs_added"], report["runs_skipped_duplicate"]) == (1, 2, 0)
    imported = {run["run_key"]: run for run in b.list_runs()}
    assert set(imported) == {r1["run_key"], r2["run_key"]}
    for original in (r1, r2):
        copy = imported[original["run_key"]]
        assert copy["origin"] == "imported" and copy["imported_bundle_uid"] == report["bundle_uid"]
        for key in ("config", "scope", "data_fingerprint", "result_hash", "summary", "code_version", "kind", "method_version"):
            assert copy[key] == original[key]
        assert b.store.load_result(copy["id"]) == a.store.load_result(original["id"])
        assert b.store.load_artifact(copy["id"], "detail") == a.store.load_artifact(original["id"], "detail")
    [study_b] = b.list_studies()
    assert study_b["origin"] == "imported" and study_b["insight_markdown"] == "# findings" and study_b["purpose"] == "why"
    assert study_b["run_count"] == 2 and study_b["study_uid"] == study["study_uid"]


def test_importing_twice_skips_everything_the_second_time(pair):
    a, b = pair
    study, _, _ = seeded(a)
    data = export_bundle(a.store, study_ids=[study["id"]])
    import_bundle(b.store, data)
    again = import_bundle(b.store, data)
    assert (again["studies_added"], again["studies_merged"], again["runs_added"], again["runs_skipped_duplicate"]) == (0, 1, 0, 2)
    assert len(b.list_runs()) == 2 and len(b.list_studies()) == 1


def test_imported_run_with_matching_data_is_reused_not_recomputed(pair):
    a, b = pair
    study, r1, _ = seeded(a)
    import_bundle(b.store, export_bundle(a.store, study_ids=[study["id"]]))
    out = b.run("bundle_demo_for_test", {"scope": SCOPE})
    # b has identical data in scope? fixtures are identical, so the fingerprints match and the imported run is reused
    assert out["reused"] is True and out["run"]["origin"] == "imported"
    assert b.run_detail(out["run"]["id"])["local_data_match"] is True


def test_rerun_of_imported_run_with_different_data_links_parent(pair):
    import sqlite3
    a, b = pair
    study, r1, _ = seeded(a)
    conn = sqlite3.connect(b.config.database_path)
    conn.execute("UPDATE settings SET value='rev-x'")
    conn.execute("UPDATE pitches SET _ingested_at='2035-01-01T00:00:00+00:00' WHERE game_year=2024")
    conn.commit(); conn.close()
    import_bundle(b.store, export_bundle(a.store, study_ids=[study["id"]]))
    imported = b.store.find_run_by_key(r1["run_key"])
    assert b.run_detail(imported["id"])["local_data_match"] is False
    rerun = b.rerun(imported["id"])
    assert rerun["run"]["origin"] == "local" and rerun["run"]["parent_run_id"] == imported["id"]
    assert rerun["run"]["run_key"] != r1["run_key"]


def test_export_selection_options(pair):
    a, _ = pair
    study, r1, r2 = seeded(a)
    only = zipfile.ZipFile(io.BytesIO(export_bundle(a.store, run_ids=[r1["id"]])))
    names = set(only.namelist())
    assert f"runs/{r1['run_key']}.json" in names and f"runs/{r2['run_key']}.json" not in names
    assert not any(n.startswith("studies/") for n in names)
    slim = zipfile.ZipFile(io.BytesIO(export_bundle(a.store, run_ids=[r1["id"]], include_artifacts=False)))
    assert json.loads(slim.read(f"runs/{r1['run_key']}.json"))["artifacts"] == []
    assert len([n for n in slim.namelist() if n.startswith("blobs/")]) == 1
    for kwargs in ({}, {"study_ids": [999]}, {"run_ids": [999]}):
        with pytest.raises(ConfigError):
            export_bundle(a.store, **kwargs)


def test_failed_runs_cannot_be_exported_and_are_skipped_in_studies(pair):
    a, _ = pair
    study, r1, _ = seeded(a)
    a.store.fail_run(r1["id"], "x")
    with pytest.raises(ConfigError, match="did not finish"):
        export_bundle(a.store, run_ids=[r1["id"]])
    names = zipfile.ZipFile(io.BytesIO(export_bundle(a.store, study_ids=[study["id"]]))).namelist()
    assert len([n for n in names if n.startswith("runs/")]) == 1


def rebuild(data, mutate):
    source = zipfile.ZipFile(io.BytesIO(data))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as target:
        for info in source.infolist():
            name, body = mutate(info.filename, source.read(info.filename))
            if name is not None:
                target.writestr(name, body)
    return out.getvalue()


def test_corrupt_or_malicious_bundles_are_rejected_without_changes(pair):
    a, b = pair
    study, r1, _ = seeded(a)
    data = export_bundle(a.store, study_ids=[study["id"]])
    import gzip

    def tamper_blob(name, body):
        if name.startswith("blobs/"):
            return name, gzip.compress(b'{"tampered":true}')
        return name, body

    def evil_name(name, body):
        return ("../evil.json" if name == "manifest.json" else name), body

    def tamper_config(name, body):
        if name.startswith("runs/"):
            payload = json.loads(body); payload["config"]["n"] = 7
            return name, json.dumps(payload).encode()
        return name, body

    def drop_blob(name, body):
        return (None, b"") if name.startswith("blobs/") else (name, body)

    def bad_format(name, body):
        if name == "manifest.json":
            payload = json.loads(body); payload["format"] = "other"
            return name, json.dumps(payload).encode()
        return name, body

    for mutate, text in ((tamper_blob, "corrupted"), (evil_name, "Unexpected bundle member"), (tamper_config, "does not match"),
                         (drop_blob, "missing blob"), (bad_format, "Unsupported bundle format")):
        with pytest.raises(ConfigError, match=text):
            import_bundle(b.store, rebuild(data, mutate))
        assert b.list_runs() == [] and b.list_studies() == []
    with pytest.raises(ConfigError, match="Not a valid research bundle"):
        import_bundle(b.store, b"not a zip")


def test_import_is_atomic_when_a_database_error_happens(pair, monkeypatch):
    a, b = pair
    study, _, _ = seeded(a)
    data = export_bundle(a.store, study_ids=[study["id"]])
    import treepolo_mlb_data.research.store as store_module
    calls = {"n": 0}
    real_now = store_module._now

    def flaky_now():
        calls["n"] += 1
        if calls["n"] >= 3:
            raise RuntimeError("disk exploded")
        return real_now()

    monkeypatch.setattr(store_module, "_now", flaky_now)
    with pytest.raises(RuntimeError):
        import_bundle(b.store, data)
    monkeypatch.setattr(store_module, "_now", real_now)
    assert b.list_runs() == [] and b.list_studies() == []
    assert import_bundle(b.store, data)["runs_added"] == 2  # a clean retry works


def test_imported_study_may_share_a_name_with_a_local_study(pair):
    a, b = pair
    study, _, _ = seeded(a)
    data = export_bundle(a.store, study_ids=[study["id"]])
    b.create_study("Decay")  # local study with the same name does not conflict (different origin)
    import_bundle(b.store, data)
    assert sorted(s["name"] for s in b.list_studies()) == ["Decay", "Decay"]
