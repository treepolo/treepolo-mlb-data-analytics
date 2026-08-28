from treepolo_mlb_data.webapp import STATIC_DIR


UNORDERED_MULTI_FIELD_SELECTORS = {
    ".s4-groups",
    ".s4-partition",
    ".s4-fields",
    "#s4-cluster-features",
    "#s4-cluster-ids",
    "#s4-cluster-partitions",
    "#s4-reg-independent",
    "#s4-boot-units",
    "#cc-entities",
    "#cc-features",
    ".ta-entity-fields",
    ".ta-percentile-partition",
}


def test_unordered_dynamic_multifields_share_checkbox_component():
    source = (STATIC_DIR / "field-checklists.js").read_text(encoding="utf-8")
    for selector in UNORDERED_MULTI_FIELD_SELECTORS:
        assert f'"{selector}"' in source
    assert '".s4-order"' not in source
    assert 'checkbox.type = "checkbox"' in source
    assert "window.treepoloLegalFieldOptions?.available" in source
    assert 'input.dataset.unifiedFieldInput = "1"' in source
    assert 'control.value = values.join(",")' in source


def test_checklist_bootstrap_precedes_fast_status_legality_and_unified_layers():
    webapp = (STATIC_DIR.parent / "webapp.py").read_text(encoding="utf-8")
    fast = (STATIC_DIR / "fast-status.js").read_text(encoding="utf-8")
    assert webapp.index('/field-checklists.js') < webapp.index('/fast-status.js')
    assert fast.index('/field-option-legality-v3.js') < fast.index('/field-controls-unified.js')
    assert 'treepolo:field-legality-ready' in fast


def test_dense_rank_wording_is_globally_normalized_without_removing_rank():
    source = (STATIC_DIR / "ui-consistency-fixes.js").read_text(encoding="utf-8")
    workflow = (STATIC_DIR / "acceptance-fixes.js").read_text(encoding="utf-8")
    assert 'option[value="dense_rank"]' in source
    assert "保留並列（不跳號） Dense Rank" in source
    assert 'value="rank"' in workflow


def test_research_workflow_remove_metric_button_cannot_collapse_vertically():
    source = (STATIC_DIR / "ui-consistency-fixes.js").read_text(encoding="utf-8")
    assert ".s4-metric-row > button.remove-row" in source
    assert "grid-column: 1 / -1" in source
    assert "white-space: nowrap" in source
    assert "min-width: 170px" in source
