from treepolo_mlb_data.webapp import STATIC_DIR


def test_unified_analysis_save_ui_reuses_one_saved_analysis_endpoint():
    save_ui = (STATIC_DIR / "analysis-save-ui.js").read_text(encoding="utf-8")

    assert save_ui.count('"/api/analysis/saved"') == 1
    assert "async function saveSource" in save_ui
    assert "window.treepoloLastAnalysis" in save_ui
    assert "historySource(item)" in save_ui
    assert "openSaveDialog(source)" in save_ui


def test_current_result_save_resolves_canonical_history_cache_metadata():
    save_ui = (STATIC_DIR / "analysis-save-ui.js").read_text(encoding="utf-8")

    assert "async function resolveSource" in save_ui
    assert "current.result?.history_id" in save_ui
    assert "history_id: item.id || null" in save_ui
    assert "`/api/analysis/history/${historyId}`" in save_ui
    assert "cache_key: item.cache_key || null" in save_ui
    assert "data_revision: item.data_revision || null" in save_ui
    assert "const resolved = await resolveSource(source)" in save_ui
    assert "analysis_payload: resolved.payload" in save_ui
    assert "cache_key: resolved.cache_key || null" in save_ui


def test_library_load_preserves_result_metadata_without_polling_overlay():
    controls = (STATIC_DIR / "stage4-controls.js").read_text(encoding="utf-8")
    loader = (STATIC_DIR / "fast-status.js").read_text(encoding="utf-8")

    assert 'loadItem(body.item,{kind:"saved",id})' in controls
    assert 'loadItem(body.item,{kind:"history",id})' in controls
    assert "window.treepoloLastAnalysis={" in controls
    assert 'history_id:source.kind==="history"' in controls
    assert "cache_key:item.cache_key||null" in controls
    assert "data_revision:item.data_revision||null" in controls
    assert "loaded_source_kind:source.kind||null" in controls
    assert '/analysis-load-metadata.js' not in loader


def test_save_entry_points_are_result_toolbar_and_history_rows():
    save_ui = (STATIC_DIR / "analysis-save-ui.js").read_text(encoding="utf-8")
    loader = (STATIC_DIR / "fast-status.js").read_text(encoding="utf-8")

    assert "result-save-toolbar" in save_ui
    assert 'title.insertAdjacentElement("afterend", toolbar)' in save_ui
    assert "analysis-history-save-as" in save_ui
    assert "另存分析 Save As…" in save_ui
    assert "removeLegacySavePanel" in save_ui
    assert "儲存目前分析" in save_ui
    assert "/analysis-save-ui.js" in loader


def test_save_as_uses_xp_dialog_with_deferred_name_and_notes_inputs():
    save_ui = (STATIC_DIR / "analysis-save-ui.js").read_text(encoding="utf-8")

    assert "xp-save-dialog-layer" in save_ui
    assert "xp-save-dialog-title" in save_ui
    assert "xp-save-name" in save_ui
    assert "xp-save-notes" in save_ui
    assert "另存分析 Save Analysis As" in save_ui
    assert "取消 Cancel" in save_ui
    assert "儲存 Save" in save_ui
