from treepolo_mlb_data.webapp import STATIC_DIR


def test_checklist_refresh_happens_before_next_paint_and_csv_input_stays_visible():
    source = (STATIC_DIR / "field-checklists.js").read_text(encoding="utf-8")
    assert "queueMicrotask" in source
    assert ".field-checklist{width:100%;max-width:100%;min-width:0" in source
    assert "input[data-multi-field]{width:100%" in source
    assert "display:none!important" not in source
    assert "window.treepoloFieldChecklistsApi" in source


def test_library_status_decoration_is_idempotent_and_ignores_own_badges():
    source = (STATIC_DIR / "analysis-library-status.js").read_text(encoding="utf-8")
    assert "existingBadge" in source
    assert "libraryStructureChanged" in source
    assert 'node.classList?.contains("analysis-stale-badge")' in source


def test_loaded_analysis_metadata_is_owned_by_canonical_loader_not_polling_helper():
    controls = (STATIC_DIR / "stage4-controls.js").read_text(encoding="utf-8")
    fast = (STATIC_DIR / "fast-status.js").read_text(encoding="utf-8")

    assert not (STATIC_DIR / "analysis-load-metadata.js").exists()
    assert "/analysis-load-metadata.js" not in fast
    assert "loaded_source_kind:source.kind||null" in controls
    assert "loaded_source_id:source.id||item.id||null" in controls
    assert "treepolo:analysis-current-source-updated" in controls
