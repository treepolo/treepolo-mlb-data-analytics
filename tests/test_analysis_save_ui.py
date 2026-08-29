from treepolo_mlb_data.webapp import STATIC_DIR


def source(name: str) -> str:
    return (STATIC_DIR / name).read_text(encoding="utf-8")


def test_save_dialog_reuses_one_saved_analysis_endpoint_and_resolves_history_metadata():
    save_ui = source("analysis-save-ui.js")

    assert save_ui.count('"/api/analysis/saved"') == 1
    assert "async function saveSource" in save_ui
    assert "async function resolveSource" in save_ui
    assert "`/api/analysis/history/${historyId}`" in save_ui
    assert "cache_key: item.cache_key || null" in save_ui
    assert "data_revision: item.data_revision || null" in save_ui
    assert "const resolved = await resolveSource(source)" in save_ui
    assert "analysis_payload: resolved.payload" in save_ui
    assert "cache_key: resolved.cache_key || null" in save_ui


def test_save_dialog_is_single_purpose_and_does_not_decorate_workspace_dom():
    save_ui = source("analysis-save-ui.js")

    assert "MutationObserver" not in save_ui
    assert "analysis-history-list" not in save_ui
    assert "result-save-toolbar" not in save_ui
    assert "window.treepoloLastAnalysis" not in save_ui
    assert "removeLegacySavePanel" not in save_ui
    assert 'document.addEventListener("treepolo:analysis-save-request"' in save_ui
    assert "window.treepoloAnalysisSaveUiApi = { open: openSaveDialog }" in save_ui


def test_workspace_owns_result_save_toolbar_and_history_save_as_actions():
    controls = source("stage4-controls.js")

    assert "function ensureResultSaveToolbar()" in controls
    assert "result-save-toolbar" in controls
    assert "儲存此分析 Save Analysis" in controls
    assert "function historySource(item)" in controls
    assert "另存分析 Save As…" in controls
    assert "requestSave(source)" in controls
    assert "儲存目前分析 Save Current Analysis" not in controls
    assert "analysis-save-current" not in controls


def test_analysis_state_updates_save_toolbar_for_fresh_and_loaded_results_without_dom_observers():
    controls = source("stage4-controls.js")

    assert "function publishAnalysisState()" in controls
    assert "publishAnalysisState();" in controls
    assert "window.treepoloLastAnalysis = { payload:lastPayload, result:lastResult }" in controls
    assert "loaded_source_kind:source.kind||null" in controls
    assert "treepolo:analysis-state-changed" in controls
    assert "treepolo:analysis-current-source-updated" in controls
    assert "renderMissingStoredResult" in controls


def test_save_ui_is_not_a_late_fast_status_enhancement():
    controls = source("stage4-controls.js")
    fast_status = source("fast-status.js")

    assert "function ensureSaveUiLoaded()" in controls
    assert 'script.src = "/analysis-save-ui.js"' in controls
    assert '/analysis-save-ui.js' not in fast_status
    assert "treepolo:analysis-library-refresh-request" in controls


def test_save_as_uses_xp_dialog_with_deferred_name_and_notes_inputs():
    save_ui = source("analysis-save-ui.js")

    assert "xp-save-dialog-layer" in save_ui
    assert "xp-save-dialog-title" in save_ui
    assert "xp-save-name" in save_ui
    assert "xp-save-notes" in save_ui
    assert "另存分析 Save Analysis As" in save_ui
    assert "取消 Cancel" in save_ui
    assert "儲存 Save" in save_ui
