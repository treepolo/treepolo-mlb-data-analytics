import io
import json
import threading
import urllib.error
import urllib.request
import zipfile
from http.server import ThreadingHTTPServer

import pytest

from research_fixtures import make_research_db
from treepolo_mlb_data.config import AppConfig
from treepolo_mlb_data.webapp import AppServices, _Handler

PAYLOAD = {"mode": "basic", "group_by": ["pitch_type"], "metrics": [{"function": "count"}], "limit": 50}


class Server:
    def __init__(self, tmp_path, name):
        root = tmp_path / name
        config = AppConfig(data_dir=str(root), analysis_backend="sqlite")
        make_research_db(config.database_path)
        self.services = AppServices(config)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.httpd.services = self.services
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def call(self, method, path, body=None, raw=None, expect=200):
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        headers = {} if raw is not None else {"Content-Type": "application/json"}
        request = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                status, payload, head = response.status, response.read(), response.headers
        except urllib.error.HTTPError as error:
            status, payload, head = error.code, error.read(), error.headers
        assert status == expect, (status, payload[:300])
        if head.get_content_type() == "application/json":
            return json.loads(payload), head
        return payload, head

    def close(self):
        self.httpd.shutdown(); self.httpd.server_close()
        self.services.analysis_state.close(); self.services.research.close()


@pytest.fixture()
def servers(tmp_path):
    a, b = Server(tmp_path, "a"), Server(tmp_path, "b")
    yield a, b
    a.close(); b.close()


def test_methods_and_full_run_flow(servers):
    a, _ = servers
    methods, _ = a.call("GET", "/api/research/methods")
    assert "analysis_payload" in [m["kind"] for m in methods["methods"]]

    study, _ = a.call("POST", "/api/research/studies", {"name": "demo", "purpose": "p"})
    study_id = study["item"]["id"]
    first, _ = a.call("POST", "/api/research/run", {"kind": "analysis_payload", "config": {"payload": PAYLOAD}, "study_id": study_id})
    second, _ = a.call("POST", "/api/research/run", {"kind": "analysis_payload", "config": {"payload": PAYLOAD}, "study_name": "other"})
    assert first["reused"] is False and second["reused"] is True and first["run"]["id"] == second["run"]["id"]
    run_id = first["run"]["id"]

    listing, _ = a.call("GET", f"/api/research/runs?study_id={study_id}&kind=analysis_payload&status=success&limit=10")
    assert [r["id"] for r in listing["runs"]] == [run_id]
    detail, _ = a.call("GET", f"/api/research/runs/{run_id}")
    assert detail["item"]["status"] == "success" and detail["item"]["local_data_match"] is None
    page, _ = a.call("GET", f"/api/research/runs/{run_id}/result?section=0&offset=0&limit=1")
    assert page["total"] == 2 and len(page["rows"]) == 1 and page["section"]["title"] == "basic"

    changed, _ = a.call("POST", f"/api/research/runs/{run_id}/studies", {"study_id": study_id, "action": "remove"})
    assert changed["changed"] is True
    studies, _ = a.call("GET", "/api/research/studies")
    assert {s["name"]: s["run_count"] for s in studies["studies"]}["demo"] == 0
    updated, _ = a.call("POST", f"/api/research/studies/{study_id}", {"insight_markdown": "# found"})
    assert updated["item"]["insight_markdown"] == "# found"


def test_export_import_between_servers(servers):
    a, b = servers
    study, _ = a.call("POST", "/api/research/studies", {"name": "share", "insight_markdown": "# why"})
    a.call("POST", "/api/research/run", {"kind": "analysis_payload", "config": {"payload": PAYLOAD}, "study_id": study["item"]["id"]})
    body, head = a.call("POST", "/api/research/export", {"study_ids": [study["item"]["id"]], "notes": "n"})
    assert head.get_content_type() == "application/zip"
    assert "attachment; filename=" in head["Content-Disposition"] and head["Content-Disposition"].endswith('.treepolo-research.zip"')
    assert "manifest.json" in zipfile.ZipFile(io.BytesIO(body)).namelist()
    report, _ = b.call("POST", "/api/research/import", raw=body)
    assert report["runs_added"] == 1 and report["studies_added"] == 1
    runs, _ = b.call("GET", "/api/research/runs")
    assert runs["runs"][0]["origin"] == "imported"
    rerun, _ = b.call("POST", f"/api/research/runs/{runs['runs'][0]['id']}/rerun", {})
    assert rerun["reused"] is True or rerun["reproduced"] is True  # same fixture data -> same key -> same result


def test_deletes(servers):
    a, _ = servers
    out, _ = a.call("POST", "/api/research/run", {"kind": "analysis_payload", "config": {"payload": PAYLOAD}})
    run_id = out["run"]["id"]
    deleted, _ = a.call("DELETE", f"/api/research/runs/{run_id}")
    assert deleted["deleted"] is True
    a.call("DELETE", f"/api/research/runs/{run_id}", expect=404)
    study_id = a.call("POST", "/api/research/studies", {"name": "tmp"})[0]["item"]["id"]
    assert a.call("DELETE", f"/api/research/studies/{study_id}?delete_runs=1")[0]["deleted"] is True
    a.call("DELETE", f"/api/research/studies/{study_id}", expect=404)


def test_errors_are_json_with_the_right_status(servers):
    a, _ = servers
    a.call("GET", "/api/research/nope", expect=404)
    a.call("POST", "/api/research/nope", {}, expect=404)
    a.call("GET", "/api/research/runs/9999", expect=404)
    bad, _ = a.call("POST", "/api/research/run", {"kind": "analysis_payload", "config": {}}, expect=400)
    assert "payload" in bad["error"]
    a.call("POST", "/api/research/run", {"kind": "nope", "config": {}}, expect=400)
    a.call("POST", "/api/research/export", {}, expect=400)
    a.call("GET", "/api/research/runs?limit=abc", expect=400)
    a.call("GET", "/api/research/runs?limit=5000", expect=400)
    a.call("POST", "/api/research/import", raw=b"", expect=400)
    a.call("POST", "/api/research/import", raw=b"definitely not a zip", expect=400)
    a.call("POST", "/api/research/runs/1/studies", {"study_id": 1, "action": "add"}, expect=400)


def test_existing_endpoints_still_work(servers):
    a, _ = servers
    meta, _ = a.call("GET", "/api/meta")
    assert meta["ready"] is True
    result, _ = a.call("POST", "/api/analyze", dict(PAYLOAD))
    assert result["row_count"] == 2
