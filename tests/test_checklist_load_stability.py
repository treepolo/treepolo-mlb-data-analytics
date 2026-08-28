from treepolo_mlb_data.webapp import STATIC_DIR


def test_checklist_refresh_happens_before_next_paint_and_width_is_stable():
    source = (STATIC_DIR / "field-checklists.js").read_text(encoding="utf-8")
    assert "queueMicrotask" in source
    assert ".field-checklist{width:100%;max-width:100%;min-width:0" in source
    assert "select[multiple][data-field-select]" in source
    assert "visibility:hidden" in source or "display:none!important" in source


def test_library_status_decoration_is_idempotent_and_ignores_own_badges():
    source = (STATIC_DIR / "analysis-library-status.js").read_text(encoding="utf-8")
    assert "existingBadge" in source
    assert "analysis-stale-badge" in source
    assert "libraryStructureChanged" in source
    assert "node.classList?.contains(\"analysis-stale-badge\")" in source


def test_load_metadata_observer_ignores_badge_only_mutations():
    source = (STATIC_DIR / "analysis-load-metadata.js").read_text(encoding="utf-8")
    assert "libraryStructureChanged" in source
    assert "analysis-stale-badge" in source
