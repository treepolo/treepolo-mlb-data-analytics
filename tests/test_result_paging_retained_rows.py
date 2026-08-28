from treepolo_mlb_data.webapp import STATIC_DIR


def test_shared_result_paging_caps_retained_rows_before_rendering():
    source = (STATIC_DIR / "result-paging.js").read_text(encoding="utf-8")
    assert "function normalizeRetainedRows" in source
    assert "rows.splice(limit)" in source
    assert "const normalized = normalizeRetainedRows(full);" in source
    assert "const paged = initialPage(normalized);" in source
    assert "schedule(normalized);" in source


def test_retained_row_cap_uses_declared_result_limit_and_safe_legacy_default():
    source = (STATIC_DIR / "result-paging.js").read_text(encoding="utf-8")
    assert "const LEGACY_RESULT_LIMIT = 500;" in source
    assert "section?.result_limit" in source
    assert "result?.result_limit" in source
    assert "Math.min(Math.max(1, Math.trunc(value)), 5000)" in source
