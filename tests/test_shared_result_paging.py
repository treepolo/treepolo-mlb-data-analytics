from treepolo_mlb_data.webapp import STATIC_DIR


def test_result_paging_handles_fresh_and_stored_results_through_one_pipeline():
    source = (STATIC_DIR / "result-paging.js").read_text(encoding="utf-8")
    assert 'url.includes("/api/analyze")' in source
    assert "isStoredDetail" in source
    assert "analysis/(?:history|saved)" in source.replace("\\/", "/")
    assert "prepareForRender(full)" in source
    assert "prepareForRender(item.result)" in source
    assert "window.__taFetchLimiterInstalled = true" in source


def test_stored_paging_requires_a_real_library_load_action():
    source = (STATIC_DIR / "result-paging.js").read_text(encoding="utf-8")
    assert "loadIntentUntil" in source
    assert "#analysis-history-list button,#saved-analysis-list button" in source
    assert 'text.includes("載入")' in source
    assert "performance.now() <= loadIntentUntil" in source


def test_workspace_library_requests_use_current_fetch_pipeline():
    source = (STATIC_DIR / "stage4-controls.js").read_text(encoding="utf-8")
    assert "const response = await window.fetch(path" in source
    assert "const response = await nativeFetch(path" not in source


def test_shared_paging_loads_before_legacy_acceptance_fallback():
    source = (STATIC_DIR / "fast-status.js").read_text(encoding="utf-8")
    paging = source.index('loadScriptOnce("/result-paging.js"')
    acceptance = source.index('loadScriptOnce("/acceptance-fixes.js"')
    assert paging < acceptance


def test_library_load_metadata_is_integrated_without_polling_overlay():
    bootstrap = (STATIC_DIR / "fast-status.js").read_text(encoding="utf-8")
    controls = (STATIC_DIR / "stage4-controls.js").read_text(encoding="utf-8")
    assert 'loadScriptOnce("/analysis-load-metadata.js"' not in bootstrap
    assert "loaded_source_kind" in controls
    assert "cache_key:item.cache_key" in controls
