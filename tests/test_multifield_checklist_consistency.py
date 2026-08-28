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


def source(name: str) -> str:
    return (STATIC_DIR / name).read_text(encoding="utf-8")


def test_unordered_dynamic_multifields_share_checkbox_component():
    checklist = source("field-checklists.js")
    for selector in UNORDERED_MULTI_FIELD_SELECTORS:
        assert f'"{selector}"' in checklist
    assert '".s4-order"' not in checklist
    assert 'checkbox.type = "checkbox"' in checklist
    assert "window.treepoloLegalFieldOptions?.available" in checklist
    assert 'control.dataset.unifiedFieldInput = "1"' in checklist
    assert 'control.value = values.join(",")' in checklist


def test_dynamic_checklists_cannot_be_reowned_by_editable_combo_layer():
    checklist = source("field-checklists.js")
    unified = source("field-controls-unified.js")

    assert "function claimControls" in checklist
    assert "claimControls(document);" in checklist
    assert 'control.dataset.unifiedFieldInput = "1"' in checklist
    assert 'if (!input || input.dataset.unifiedFieldInput === "1") return;' in unified


def test_checklist_rows_are_not_labels_and_ignore_stage_form_label_layout():
    checklist = source("field-checklists.js")

    assert 'const row = document.createElement("div")' in checklist
    assert 'row.className = "field-check-item"' in checklist
    assert '.field-checklist .field-check-item{display:flex;flex-direction:row' in checklist
    assert 'document.createElement("label")' not in checklist


def test_checklists_are_lazy_and_skip_dom_rebuild_when_shape_is_unchanged():
    checklist = source("field-checklists.js")

    assert "function activeForRender(control)" in checklist
    assert 'panel.classList.contains("active-panel")' in checklist
    assert "if (activeForRender(control)) renderChecklist(control);" in checklist
    assert "function renderSignature" in checklist
    assert "state.signature === signature" in checklist
    assert "syncState(state);" in checklist


def test_checklist_observer_only_reacts_to_new_controls_not_its_own_row_dom():
    checklist = source("field-checklists.js")

    assert "function addedControls(mutation)" in checklist
    assert "mutations.some(addedControls)" in checklist
    assert "addedNodes" in checklist
    assert "characterData:true" not in checklist
    assert "attributes:true" not in checklist


def test_checklist_bootstrap_precedes_fast_status_legality_and_unified_layers():
    webapp = (STATIC_DIR.parent / "webapp.py").read_text(encoding="utf-8")
    fast = source("fast-status.js")
    assert webapp.index('/field-checklists.js') < webapp.index('/fast-status.js')
    assert fast.index('/field-option-legality-v3.js') < fast.index('/field-controls-unified.js')
    assert 'treepolo:field-legality-ready' in fast


def test_dense_rank_wording_is_globally_normalized_without_removing_rank():
    consistency = source("ui-consistency-fixes.js")
    workflow = source("acceptance-fixes.js")
    assert 'option[value="dense_rank"]' in consistency
    assert "保留並列（不跳號） Dense Rank" in consistency
    assert 'value="rank"' in workflow


def test_research_workflow_remove_metric_button_cannot_collapse_vertically():
    consistency = source("ui-consistency-fixes.js")
    assert ".s4-metric-row > button.remove-row" in consistency
    assert "grid-column: 1 / -1" in consistency
    assert "white-space: nowrap" in consistency
    assert "min-width: 170px" in consistency
