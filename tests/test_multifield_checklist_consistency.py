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


def test_unordered_dynamic_multifields_share_csv_checklist_component():
    checklist = source("field-checklists.js")
    for selector in UNORDERED_MULTI_FIELD_SELECTORS:
        assert f'"{selector}"' in checklist
    assert '".s4-order"' not in checklist
    assert 'checkbox.type = "checkbox"' in checklist
    assert "const provider = window.treepoloLegalFieldOptions" in checklist
    assert 'control.dataset.multiField = "1"' in checklist
    assert "model()?.write" in checklist


def test_unordered_multifields_have_one_renderer_owner():
    checklist = source("field-checklists.js")
    unified = source("field-controls-unified.js")
    registry = unified.split("const FIELD_INPUT_RULES = [", 1)[1].split("];", 1)[0]

    assert "function claimControl(control)" in checklist
    assert 'control.dataset.fieldChecklistOwned = "1"' in checklist
    for selector in UNORDERED_MULTI_FIELD_SELECTORS:
        assert selector not in registry
    assert '.s4-order' in registry


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
    assert "function renderSignature" in checklist
    assert "state.signature === signature" in checklist
    assert "syncState(state);" in checklist


def test_checklist_selection_sync_is_idempotent_and_emits_one_commit_event():
    checklist = source("field-checklists.js")
    model = source("multi-field-model.js")

    assert "state.selectionSignature === selectionSignature" in checklist
    assert "state.summary.replaceChildren(fragment)" in checklist
    assert "model().write(control, values, { emit:true })" in checklist
    assert 'control.dispatchEvent(new Event("change", { bubbles:true }))' in model
    assert 'control.dispatchEvent(new Event("input", { bubbles:true }))' not in model


def test_checklist_observer_only_handles_new_controls_locally():
    checklist = source("field-checklists.js")

    assert "function handleAddedControls(mutations)" in checklist
    assert "new MutationObserver(handleAddedControls)" in checklist
    assert "mutation.addedNodes" in checklist
    assert "controls.forEach(control" in checklist
    assert "characterData:true" not in checklist
    assert "attributes:true" not in checklist


def test_checklist_bootstrap_and_legality_dependency_are_explicit():
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


def test_tie_label_observer_is_scoped_to_stage_lists_not_document_body():
    consistency = source("ui-consistency-fixes.js")
    assert '.s4-stage-list,.s4-input-stage-list' in consistency
    assert "observeStageLists" in consistency
    assert ").observe(document.body" not in consistency


def test_research_workflow_remove_metric_button_cannot_collapse_vertically():
    consistency = source("ui-consistency-fixes.js")
    assert ".s4-metric-row .remove-row" in consistency
    assert "grid-column: 1 / -1 !important" in consistency
    assert "display: inline-block !important" in consistency
    assert "width: max-content !important" in consistency
    assert "white-space: nowrap !important" in consistency
    assert "writing-mode: horizontal-tb !important" in consistency
    assert "word-break: normal !important" in consistency
    assert "min-width: 170px !important" in consistency
