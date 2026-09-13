from __future__ import annotations

from pathlib import Path

from treepolo_mlb_data import stage4d
from treepolo_mlb_data import stage4d_report_v2


ROOT = Path(__file__).resolve().parents[1]


def sample_prepared() -> dict:
    columns = [
        "candidate_k",
        "criterion",
        "score",
        "selection_standard_error",
        "valid",
        "selected",
        "cluster_sizes",
        "minimum_cluster_size",
        "adaptive_max_k",
        "rejection_reason",
    ]
    rows = [{
        "candidate_k": 1,
        "criterion": "BIC",
        "score": 2152.27,
        "selection_standard_error": None,
        "valid": True,
        "selected": True,
        "cluster_sizes": "189",
        "minimum_cluster_size": 6,
        "adaptive_max_k": 8,
        "rejection_reason": None,
    }]
    section = {
        "title": "自動群數診斷 Auto Cluster Diagnostics",
        "columns": columns,
        "rows": rows,
        "row_count": 1,
        "grain": {"keys": ["candidate_k"], "label": "auto cluster candidate"},
        "backend": "numerical",
    }
    stage4d_report_v2.install()
    return {
        "section": section,
        "field_metadata": stage4d.field_metadata(section),
        "sampling": {"mode": "automatic", "method": "random", "size": 5000, "seed": 42, "sampled": False, "source_rows": 1, "returned_rows": 1},
        "provenance": {
            "source_kind": "history",
            "history_id": 50,
            "data_revision": "legacy:example",
            "backend": "numerical",
            "mode": "clustering",
            "grain": section["grain"],
            "row_count": 1,
            "returned_row_count": 1,
            "section_title": section["title"],
            "complete_result": True,
        },
        "analysis_payload": {"mode": "clustering"},
    }


def sample_spec() -> dict:
    return {
        "version": "stage4d-v1",
        "type": "line",
        "preset": "builtin:auto_k",
        "mapping": {"x": "candidate_k", "y": "score", "series": "criterion", "label": "criterion"},
        "display": {"width": 1000, "height": 620, "legend": True, "show_n": True, "equal_axes": False},
        "sampling": {"mode": "automatic", "method": "random", "size": 5000, "seed": 42},
    }


def test_html_report_is_bilingual_and_contains_wide_tables() -> None:
    prepared = sample_prepared()
    body = stage4d_report_v2._html_report_v2("analysis-report", prepared, sample_spec(), "<svg xmlns='http://www.w3.org/2000/svg'></svg>").decode("utf-8")
    assert "分析報告 Analysis Report" in body
    assert "視覺化 Visualization" in body
    assert "結果 Result" in body
    assert "來源資訊 Provenance" in body
    assert "視覺化設定 Presentation Spec" in body
    assert "報告表格最多顯示" in body
    assert "選擇標準誤 Selection Standard Error" in body
    assert "最小群大小 Minimum Cluster Size" in body
    assert "自適應最大群數 Adaptive Max K" in body
    assert "table-layout:fixed" in body
    assert "overflow-wrap:anywhere" in body
    assert "word-break:break-word" in body
    assert "overflow-x:hidden" in body


def test_unknown_report_field_still_has_bilingual_fallback() -> None:
    prepared = sample_prepared()
    prepared["section"]["columns"].append("future_metric_xyz")
    prepared["section"]["rows"][0]["future_metric_xyz"] = 1.2
    prepared["field_metadata"] = stage4d.field_metadata(prepared["section"])
    body = stage4d_report_v2._html_report_v2("分析報告 Analysis Report", prepared, sample_spec(), "").decode("utf-8")
    assert "資料欄位 Data Field · future_metric_xyz" in body


def test_pdf_report_handles_wide_bilingual_result_table() -> None:
    body = stage4d_report_v2._pdf_report_v2("analysis-report", sample_prepared(), sample_spec())
    assert body.startswith(b"%PDF")
    assert len(body) > 1000


def test_cli_installs_report_v2_after_stage4d() -> None:
    cli = (ROOT / "src/treepolo_mlb_data/cli.py").read_text(encoding="utf-8")
    assert "from .stage4d_report_v2 import install as install_stage4d_report_v2" in cli
    assert "install_stage4d_report_v2()" in cli
    assert cli.index("install_stage4d(webapp)") < cli.index("install_stage4d_report_v2()") < cli.index("install_stage4d_frontend_patch(webapp)")
