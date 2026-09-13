from treepolo_mlb_data.webapp import STATIC_DIR


def source(name: str) -> str:
    return (STATIC_DIR / name).read_text(encoding="utf-8")


def test_shared_paging_loads_before_legacy_acceptance_layer():
    bootstrap = source("fast-status.js")
    paging_at = bootstrap.index('loadScriptOnce("/result-paging.js"')
    acceptance_at = bootstrap.index('loadScriptOnce("/acceptance-fixes.js"')
    assert paging_at < acceptance_at


def test_shared_paging_owns_fresh_analysis_response_paging():
    paging = source("result-paging.js")
    assert "window.treepoloResultPaging" in paging
    assert "window.__taFetchLimiterInstalled = true" in paging
    assert 'url.includes("/api/analyze")' in paging
    assert "const normalized = normalizeRetainedRows(full)" in paging
    assert "const paged = initialPage(normalized)" in paging
    assert "schedule(normalized)" in paging


def test_cached_load_uses_same_paging_presenter_and_keeps_canonical_result_state():
    controls = source("stage4-controls.js")
    assert "window.treepoloResultPaging" in controls
    assert "paging.present(result,renderStoredResultPage)" in controls
    assert "lastResult=item.result||null" in controls
    assert "window.treepoloLastAnalysis={" in controls
    assert "payload:lastPayload,result:lastResult" in controls
    assert "loaded_source_kind:source.kind||null" in controls
    assert "renderStoredResultPage(item.result)" not in controls


def test_shared_pager_limits_initial_dom_to_200_rows():
    paging = source("result-paging.js")
    assert "const PAGE_SIZE = 200" in paging
    assert "section.rows.slice(0, PAGE_SIZE)" in paging
    assert "rows.slice(start, start + PAGE_SIZE)" in paging
