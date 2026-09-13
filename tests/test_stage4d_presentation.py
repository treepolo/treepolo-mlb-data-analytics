from __future__ import annotations

import gzip
import json
import zipfile
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest

from treepolo_mlb_data.analysis_state import AnalysisStateStore
from treepolo_mlb_data.stage4d import (
    AUTO_SAMPLE_ROWS,
    BUILTIN_PRESETS,
    MAX_FULL_VISUALIZATION_ROWS,
    Stage4DService,
    field_metadata,
    normalize_spec,
    sample_rows,
)
from treepolo_mlb_data.web_analysis import RequestError


class FakeAnalysis:
    def __init__(self, result: dict):
        self.result = result
        self.payloads: list[dict] = []

    def analyze(self, payload: dict, progress=None) -> dict:
        self.payloads.append(json.loads(json.dumps(payload)))
        return json.loads(json.dumps(self.result))


class FakeApp:
    def __init__(self, root: Path, result: dict):
        self.config = SimpleNamespace(
            root=root,
            database_path=root / "data" / "statcast.sqlite3",
        )
        self.config.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.analysis_state = AnalysisStateStore(root / "data" / "analysis_state.sqlite3")
        self.analysis = FakeAnalysis(result)

    def close(self) -> None:
        self.analysis_state.close()


@pytest.fixture
def sample_result() -> dict:
    rows = [
        {"game_year": 2024, "pitch_type": "FF", "release_speed": 95.1, "pfx_x": -0.4, "pfx_z": 1.2, "sample_size": 20},
        {"game_year": 2025, "pitch_type": "FF", "release_speed": 96.2, "pfx_x": -0.5, "pfx_z": 1.3, "sample_size": 24},
        {"game_year": 2026, "pitch_type": "SL", "release_speed": 87.8, "pfx_x": 0.2, "pfx_z": 0.3, "sample_size": 18},
    ]
    return {
        "columns": list(rows[0]),
        "rows": rows,
        "row_count": len(rows),
        "grain": {"label": "grouped"},
        "backend": "duckdb",
    }


def test_presentation_metadata_has_roles_and_units(sample_result):
    metadata = {item["name"]: item for item in field_metadata(sample_result)}
    assert metadata["game_year"]["role"] == "temporal_dimension"
    assert metadata["pitch_type"]["role"] == "category"
    assert metadata["release_speed"]["unit"] == "mph"
    assert metadata["release_speed"]["is_numeric"] is True
    assert metadata["sample_size"]["role"] == "sample_size"


def test_sampling_is_explicit_and_reproducible():
    rows = [{"pitch_uid": str(index), "x": index} for index in range(10_000)]
    first, first_info = sample_rows(rows, {"mode": "automatic", "method": "random", "seed": 19})
    second, second_info = sample_rows(rows, {"mode": "automatic", "method": "random", "seed": 19})
    assert len(first) == AUTO_SAMPLE_ROWS
    assert first == second
    assert first_info == second_info
    assert first_info["sampled"] is True
    assert first_info["source_rows"] == 10_000
    assert first_info["returned_rows"] == AUTO_SAMPLE_ROWS

    nth, nth_info = sample_rows(rows, {"mode": "manual", "method": "every_nth", "size": 1000, "seed": 42})
    assert len(nth) == 1000
    assert nth_info["method"] == "every_nth"


def test_full_visualization_refuses_unsafe_row_count():
    rows = [{"x": index} for index in range(MAX_FULL_VISUALIZATION_ROWS + 1)]
    with pytest.raises(RequestError, match="use Automatic or Manual Sampling"):
        sample_rows(rows, {"mode": "full"})


def test_full_result_rerun_removes_ui_result_limit(tmp_path, sample_result):
    app = FakeApp(tmp_path, sample_result)
    try:
        service = Stage4DService(app)
        projected = dict(sample_result)
        projected["rows"] = projected["rows"][:1]
        projected["returned_row_count"] = 1
        source = {"kind": "analysis_payload", "payload": {"mode": "basic", "result_limit": 1, "filters": []}}
        prepared = service.prepare_data({
            "source": source,
            "client_result": projected,
            "allow_rerun": True,
            "sampling": {"mode": "full"},
        })
        assert len(prepared["section"]["rows"]) == 3
        assert app.analysis.payloads
        assert "result_limit" not in app.analysis.payloads[-1]
        assert prepared["provenance"]["rerun"] is True
    finally:
        app.close()


def test_live_and_frozen_visualization_persistence(tmp_path, sample_result):
    app = FakeApp(tmp_path, sample_result)
    try:
        service = Stage4DService(app)
        source = {"kind": "analysis_payload", "payload": {"mode": "basic", "filters": []}}
        spec = {"type": "scatter", "mapping": {"x": "pfx_x", "y": "pfx_z", "series": "pitch_type"}, "sampling": {"mode": "full"}, "display": {}}
        live = service.save_visualization({"name": "Live movement", "save_mode": "live", "source": source, "section": 0, "spec": spec})
        assert live["save_mode"] == "live"
        assert live["frozen_path"] is None

        frozen = service.save_visualization({"name": "Frozen movement", "save_mode": "frozen", "source": source, "section": 0, "spec": spec})
        assert frozen["save_mode"] == "frozen"
        snapshot = Path(frozen["frozen_path"])
        assert snapshot.is_file()
        with gzip.open(snapshot, "rt", encoding="utf-8") as handle:
            data = json.load(handle)
        assert len(data["section"]["rows"]) == 3
        loaded = service.store.get_visualization(frozen["id"], include_frozen=True)
        assert loaded["frozen_data"]["section"]["rows"] == data["section"]["rows"]
        assert service.store.delete_visualization(frozen["id"]) is True
        assert not snapshot.exists()
    finally:
        app.close()


def test_four_data_exports_and_reports(tmp_path, sample_result):
    app = FakeApp(tmp_path, sample_result)
    try:
        service = Stage4DService(app)
        source = {"kind": "analysis_payload", "payload": {"mode": "basic", "filters": []}}
        for format_name in ("csv", "json", "xlsx", "parquet"):
            body, content_type, filename = service.export({"source": source, "section": 0, "format": format_name, "name": "test export"})
            assert body
            assert filename.endswith("." + format_name)
            assert content_type
            if format_name == "csv":
                assert body.startswith("\ufeff".encode("utf-8"))
                assert b"release_speed" in body
            elif format_name == "json":
                decoded = json.loads(body.decode("utf-8"))
                assert decoded["metadata"]["row_count"] == 3
                assert len(decoded["section"]["rows"]) == 3
            elif format_name == "xlsx":
                assert body.startswith(b"PK")
                with zipfile.ZipFile(BytesIO(body)) as archive:
                    names = set(archive.namelist())
                    assert "xl/worksheets/sheet1.xml" in names
                    assert "xl/worksheets/sheet2.xml" in names
            elif format_name == "parquet":
                assert body[:4] == b"PAR1"
                assert body[-4:] == b"PAR1"

        report_spec = {"type": "scatter", "mapping": {"x": "pfx_x", "y": "pfx_z", "series": "pitch_type"}, "sampling": {"mode": "full"}, "display": {"title": "Movement"}}
        html_body, html_type, html_name = service.report({"source": source, "section": 0, "format": "html", "name": "Movement report", "spec": report_spec, "chart_svg": '<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script><circle cx="1" cy="1" r="1"/></svg>'})
        text = html_body.decode("utf-8")
        assert html_type.startswith("text/html")
        assert html_name.endswith(".html")
        assert "Movement report" in text
        assert "<script>" not in text
        assert "<circle" in text

        pdf_body, pdf_type, pdf_name = service.report({"source": source, "section": 0, "format": "pdf", "name": "Movement report", "spec": report_spec})
        assert pdf_type == "application/pdf"
        assert pdf_name.endswith(".pdf")
        assert pdf_body.startswith(b"%PDF")
    finally:
        app.close()


def test_builtin_presets_and_spec_contract_are_future_compatible():
    ids = {item["id"] for item in BUILTIN_PRESETS}
    assert {"pitch_movement", "pitch_location", "release_point", "auto_k", "regression_coefficients", "confidence_interval"}.issubset(ids)
    spec = normalize_spec({"type": "line", "mapping": {"x": "candidate_k", "y": "score"}, "sampling": {"mode": "automatic"}, "display": {"title": "Auto K"}})
    assert spec["version"] == "stage4d-v1"
    assert spec["type"] == "line"
    # One VisualizationSpec describes one chart today; it contains no global singleton
    # flag that would prevent future dashboard/collection entities from composing many specs.
    assert "dashboard_only" not in spec
    with pytest.raises(RequestError):
        normalize_spec({"type": "made_up_chart"})


def test_project_baseball_asset_policy_only(tmp_path, sample_result):
    app = FakeApp(tmp_path, sample_result)
    try:
        status = Stage4DService(app).asset_status()
        assert status["policy"] == "project-research-asset-only"
        assert status["external_search_allowed"] is False
        assert status["manifest"].endswith("research_assets/3d_baseball/upstream_manifest.json")
        assert status["fetch_helper"].endswith("research_assets/3d_baseball/fetch_upstream.py")
    finally:
        app.close()
