from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_cli_installs_stage4d_before_serving_ui():
    cli = read("src/treepolo_mlb_data/cli.py")
    assert "from .stage4d import install as install_stage4d" in cli
    assert "from .stage4d_frontend_patch import install as install_stage4d_frontend_patch" in cli
    assert "install_stage4d(webapp)" in cli
    assert "install_stage4d_frontend_patch(webapp)" in cli


def test_visualization_workspace_wiring_and_output_navigation():
    script = read("src/treepolo_mlb_data/web_static/stage4d-visualization.js")
    assert "輸出 Output" in script
    assert "視覺化 Visualization" in script
    assert "分析庫 Analysis Library" in script
    assert "分析紀錄 Analysis History" in script
    assert "visualization-panel" in script
    assert "analysis-history-panel" in script
    assert "送至視覺化 Open in Visualization" in script
    assert "匯出 Export" in script
    assert "/api/visualization/data" in script
    assert "/api/export" in script
    assert "/api/report" in script


def test_stage4d_navigation_keeps_data_first_and_output_last():
    fixups = read("src/treepolo_mlb_data/web_static/stage4d-visualization-fixes.js")
    assert "function restoreNavigationOrder()" in fixups
    assert 'textContent.includes("資料 Data")' in fixups
    assert '$("#stage4d-output-nav",nav)' in fixups
    assert "nav.prepend(dataGroup)" in fixups
    assert "nav.append(output)" in fixups


def test_stage4d_first_version_is_single_chart_without_permanent_lock_in():
    spec = read("docs/STAGE4D_SPEC.md")
    assert "第一版 Visualization 採 **單圖模式**" in spec
    assert "不得設計成「永遠只能有一張圖」" in spec
    assert "multi-chart / dashboard" in spec


def test_sampling_and_export_choices_are_visible_in_ui():
    script = read("src/treepolo_mlb_data/web_static/stage4d-visualization.js")
    for value in ("完整資料 Full Data", "自動抽樣 Automatic Sampling", "手動抽樣 Manual Sampling"):
        assert value in script
    for value in ("csv", "json", "xlsx", "parquet"):
        assert f"<option>{value}</option>" in script
    for value in ("html", "pdf"):
        assert f"<option>{value}</option>" in script
    assert "Sampled:" in script


def test_horizontal_stacked_bar_and_saved_section_fixups_are_loaded():
    patch = read("src/treepolo_mlb_data/stage4d_frontend_patch.py")
    fixups = read("src/treepolo_mlb_data/web_static/stage4d-visualization-fixes.js")
    assert "stage4d-visualization-fixes.js" in patch
    assert "drawHorizontal" in fixups
    assert "drawVertical" in fixups
    assert "stacked" in fixups
    assert 'pendingSavedVisualization.save_mode==="live"' in fixups
    assert "pendingSavedVisualization.section_index" in fixups


def test_baseball_presets_do_not_add_an_external_asset_source():
    backend = read("src/treepolo_mlb_data/stage4d.py")
    manifest = read("research_assets/3d_baseball/upstream_manifest.json")
    assert '"policy": "project-research-asset-only"' in backend
    assert '"external_search_allowed": False' in backend
    assert "research_assets" in backend and "3d_baseball" in backend
    assert '"source_repository": "Vac1911/spinrate-visual"' in manifest
