from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_cli_installs_stage4d_before_serving_ui():
    cli = read("src/treepolo_mlb_data/cli.py")
    assert "from .stage4d import install as install_stage4d" in cli
    assert "from .stage4d_saved_v2 import install as install_stage4d_saved_v2" in cli
    assert "from .stage4d_frontend_patch import install as install_stage4d_frontend_patch" in cli
    assert "install_stage4d(webapp)" in cli
    assert "install_stage4d_saved_v2()" in cli
    assert "install_stage4d_frontend_patch(webapp)" in cli
    assert cli.index("install_stage4d(webapp)") < cli.index("install_stage4d_saved_v2()") < cli.index("install_stage4d_frontend_patch(webapp)")


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
    assert "snapshot_hash" in patch
    assert "legacy" not in patch.lower() or "snapshot_hash" in patch
    assert "pendingSavedVisualization.section_index" in fixups


def test_section_or_preset_change_resets_stale_geometry_before_apply():
    patch = read("src/treepolo_mlb_data/stage4d_frontend_patch.py")
    reset = read("src/treepolo_mlb_data/web_static/stage4d-preset-state-reset.js")
    assert "stage4d-preset-state-reset.js" in patch
    assert "resetPresetGeometry" in reset
    assert 'target.matches("#viz-section")' in reset
    assert 'target.matches("#viz-preset")' in reset
    assert '$("#viz-equal-axes")' in reset
    for selector in ("#viz-ref-x", "#viz-ref-y", "#viz-x-min", "#viz-x-max", "#viz-y-min", "#viz-y-max"):
        assert f'"{selector}"' in reset
    assert '$("#viz-stacked")' in reset
    assert '$("#viz-bar-orientation")' in reset
    assert "}, true);" in reset


def test_large_browser_minimum_font_restores_original_density_and_type_scale():
    patch = read("src/treepolo_mlb_data/stage4d_frontend_patch.py")
    compat = read("src/treepolo_mlb_data/web_static/font-minimum-compat.js")
    assert "font-minimum-compat.js" in patch
    assert "measureEffectiveMinimumFontPx" in compat
    assert "DESIGN_MIN_FONT_PX = 10" in compat
    assert "ENABLE_THRESHOLD_PX = 14" in compat
    assert "DESIGN_MIN_FONT_PX / effectiveFontPx" in compat
    assert "treepolo-minimum-font-compat" in compat
    assert "--treepolo-font-compat-scale" in compat
    for px in (10, 11, 12, 13, 16):
        assert f"--treepolo-cf-font-{px}" in compat
    assert "--treepolo-cf-nav-width" in compat
    assert "--treepolo-cf-control-height" in compat
    assert "--treepolo-cf-viz-left-min" in compat
    assert "stage4d-grid" in compat
    assert "window.addEventListener(\"resize\"" in compat


def test_stage4d_two_column_controls_are_contained_in_their_grid_cells():
    patch = read("src/treepolo_mlb_data/stage4d_frontend_patch.py")
    containment = read("src/treepolo_mlb_data/web_static/stage4d-layout-containment.js")
    assert "stage4d-layout-containment.js" in patch
    assert "grid-template-columns: repeat(2, minmax(0, 1fr))" in containment
    assert "min-width: 0" in containment
    assert "width: 100%" in containment
    assert "max-width: 100%" in containment
    assert ".stage4d-map-grid > label > select" in containment
    assert ".stage4d-display-grid > label > select" in containment


def test_stage4d_repeated_save_from_analysis_creates_new_visualization_and_shows_progress():
    patch = read("src/treepolo_mlb_data/stage4d_frontend_patch.py")
    lifecycle = read("src/treepolo_mlb_data/web_static/stage4d-save-lifecycle.js")
    assert "stage4d-save-lifecycle.js" in patch
    assert 'document.querySelector("#viz-source-kind")?.value === "visualization"' in lifecycle
    assert "isVisualizationSave" in lifecycle
    assert "!isExplicitSavedVisualizationEdit()" in lifecycle
    assert "const nextUrl = collectionUrl(url)" in lifecycle
    assert "儲存中 Saving…" in lifecycle
    assert "button.disabled = true" in lifecycle
    assert "button.disabled = false" in lifecycle


def test_saved_visualization_spec_is_restored_after_data_load():
    patch = read("src/treepolo_mlb_data/stage4d_frontend_patch.py")
    restore = read("src/treepolo_mlb_data/web_static/stage4d-saved-restore.js")
    assert "stage4d-saved-restore.js" in patch
    assert "pendingSaved" in restore
    assert "setTimeout(() => restoreSpec(item), 0)" in restore
    assert 'setSelect("#viz-type"' in restore
    for key in ("x", "y", "series", "label", "lower", "upper"):
        assert f'`#viz-${{key}}`' in restore
    assert '$("#viz-render")' in restore
    assert "missing fields" in restore
    assert "Legacy Frozen visualization loaded" in restore


def test_frozen_snapshot_v2_is_content_addressed_and_multi_section():
    backend = read("src/treepolo_mlb_data/stage4d_saved_v2.py")
    assert 'SNAPSHOT_VERSION = "stage4d-frozen-result-v2"' in backend
    assert "hashlib.sha256" in backend
    assert "visualization_snapshots" in backend
    assert "snapshot_hash TEXT" in backend
    assert 'body = {"version": SNAPSHOT_VERSION, "result": frozen_data["result"]}' in backend
    assert "release_snapshot" in backend
    assert 'return None, frozen["result"]' in backend
    assert "legacy_frozen" in backend


def test_stage4d_y_axis_spacing_uses_renderer_margin_not_text_shifting():
    patch = read("src/treepolo_mlb_data/stage4d_frontend_patch.py")
    axis_layout = read("src/treepolo_mlb_data/web_static/stage4d-axis-layout.js")
    assert "stage4d-axis-layout.js" in patch
    assert "treepoloStage4DLeftMargin" in axis_layout
    assert "measureText" in axis_layout
    assert "maxTickWidth" in axis_layout
    assert "new_margin" in patch
    assert "treepoloStage4DLeftMargin(yField,yValues,display)" in patch
    assert "MutationObserver" not in axis_layout
    assert "getBoundingClientRect" not in axis_layout
    assert "neededShift" not in axis_layout


def test_baseball_presets_do_not_add_an_external_asset_source():
    backend = read("src/treepolo_mlb_data/stage4d.py")
    manifest = read("research_assets/3d_baseball/upstream_manifest.json")
    assert '"policy": "project-research-asset-only"' in backend
    assert '"external_search_allowed": False' in backend
    assert "research_assets" in backend and "3d_baseball" in backend
    assert '"source_repository": "Vac1911/spinrate-visual"' in manifest
