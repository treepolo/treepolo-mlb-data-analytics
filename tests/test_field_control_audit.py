import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]
STATIC = ROOT / "src/treepolo_mlb_data/web_static"


def read(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


def test_unified_field_control_javascript_parses():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is unavailable")
    subprocess.run([node, "--check", str(STATIC / "field-controls-unified.js")], check=True)
    subprocess.run([node, "--check", str(STATIC / "field-controls-native-arrow.js")], check=True)
    subprocess.run([node, "--check", str(STATIC / "fast-status.js")], check=True)


def test_large_single_field_selectors_are_one_editable_combo_not_select_plus_search_button():
    unified = read("field-controls-unified.js")

    assert '"select[data-field-select]:not([multiple])"' in unified
    assert '".s4-filter-field"' in unified
    assert '".cc-field"' in unified
    assert '".result-sort-field"' in unified
    assert "xp-field-entry" in unified
    assert "xp-field-popup" in unified
    assert "輸入或搜尋欄位 Type or search field" in unified
    assert "xp-popup-search" not in unified
    assert "搜尋欄位 Search field" not in unified


def test_editable_field_controls_use_real_visible_native_select_arrow_donors():
    unified = read("field-controls-unified.js")
    arrows_js = read("field-controls-native-arrow.js")
    arrows_css = read("field-controls-native-arrow.css")

    assert 'content:"▼"' not in unified
    assert 'document.createElement("select")' in arrows_js
    assert 'donor.className = "xp-native-select-arrow"' in arrows_js
    assert 'donor.setAttribute("aria-hidden", "true")' in arrows_js
    assert "pointer-events: none" in arrows_css
    assert "clip-path: inset(0 0 0 calc(100% - 24px))" in arrows_css
    assert "color: transparent" not in arrows_css
    assert ".xp-field-shell::after" not in arrows_css
    assert ".xp-field-input-shell::after" not in arrows_css
    assert ".xp-semantic-shell.xp-has-domain::after" not in arrows_css
    assert "rotate(" not in arrows_css
    assert 'content: "▼"' not in arrows_css


def test_exact_typed_single_field_commits_without_enter_or_click():
    unified = read("field-controls-unified.js")

    assert "function exactOption" in unified
    assert "const match = exactOption(options, input.value)" in unified
    assert "commit(match)" in unified
    assert 'event.key === "Enter"' not in unified


def test_multiple_field_checklists_remain_the_existing_searchable_checklist_component():
    checklist = read("field-checklists.js")

    assert "select[multiple][data-field-select]" in checklist
    assert "field-checklist-search" in checklist
    assert "field-checklist-summary" in checklist


def test_browser_datalists_are_removed_and_popup_is_below_the_same_control():
    unified = read("field-controls-unified.js")

    assert "removeNativeDatalist" in unified
    assert 'input[list^="ta-fields-"]' in unified
    assert 'input[list^="ta-pipeline-"]' in unified
    assert 'datalist[id^="ta-fields-"]' in unified
    assert 'datalist[id^="ta-pipeline-"]' in unified
    assert "top:calc(100% + 1px)" in unified
    assert "xp-field-popup" in unified


def test_every_field_name_control_uses_the_same_legality_provider():
    unified = read("field-controls-unified.js")

    assert "window.treepoloLegalFieldOptions?.available" in unified
    assert "function legalFieldOptions(control)" in unified
    assert "legalFieldOptions(select)" in unified
    assert "legalFieldOptions(input)" in unified
    assert 'control?.matches?.(".result-sort-field")' in unified
    assert "nativeOutputOptions(control)" in unified


def test_pitch_type_value_domain_never_uses_schema_field_options():
    unified = read("field-controls-unified.js")

    assert "pitch_type: PITCH_TYPES" in unified
    for code in ("FF", "SI", "FC", "SL", "ST", "CU", "KC", "CH", "FS", "FO", "KN", "EP", "SC", "SV"):
        assert f'["{code}",' in unified
    assert "function domain(field)" in unified
    assert "#role-exclude,.ta-role-exclude" in unified
    assert 'decorateSemanticValue(input, "pitch_type", true)' in unified
    assert "#cc-reference" in unified


def test_condition_value_families_are_semantically_decorated():
    unified = read("field-controls-unified.js")

    for selector in (
        ".condition-value",
        ".s4-filter-value",
        ".cc-filter-value",
        ".s4-metric-cond-value",
        ".s4-value",
        ".ta-event-value",
        "#s4-boot-a",
        "#s4-boot-b",
        "#s4-boot-success",
    ):
        assert selector in unified
    for selector in (".condition-field", ".s4-filter-field", ".cc-filter-field", ".s4-metric-cond-field", ".ta-event-field"):
        assert selector in unified


def test_advanced_field_reference_registry_is_complete_and_covers_dynamic_stages():
    unified = read("field-controls-unified.js")

    selectors = (
        ".s4-groups", ".s4-metric-field", ".s4-metric-cond-field", ".s4-left", ".s4-right-field",
        ".s4-field", ".s4-value-field", ".s4-partition", ".s4-order", ".s4-fields",
        "#s4-cluster-features", "#s4-cluster-ids", "#s4-cluster-partitions",
        "#s4-reg-dependent", "#s4-reg-independent", "#s4-boot-value", "#s4-boot-units", "#s4-boot-group",
        "#cc-entities", "#cc-features", ".ta-entity-fields", ".ta-pitch-field", ".ta-value-field",
        ".ta-percentile-field", ".ta-percentile-partition", ".ta-event-field", ".ta-metric-cond-value-field",
        ".result-sort-field",
    )
    for selector in selectors:
        assert selector in unified
    assert "new MutationObserver" in unified
    assert "scan(document)" in unified


def test_alias_creation_inputs_are_not_registered_as_field_choosers():
    unified = read("field-controls-unified.js")
    registry = unified.split("const FIELD_INPUT_RULES = [", 1)[1].split("];", 1)[0]

    for selector in (".s4-alias", ".s4-metric-alias", ".ta-custom-alias", ".ta-cohort-alias"):
        assert selector not in registry


def test_unified_layer_loads_after_dynamic_pages_and_legality_provider():
    fast = read("fast-status.js")

    acceptance = fast.index('/acceptance-fixes.js')
    cluster = fast.index('/cluster-comparison-page.js')
    legality = fast.index('/field-option-legality-v3.js')
    unified = fast.index('/field-controls-unified.js')
    arrows_css = fast.index('/field-controls-native-arrow.css')
    arrows_js = fast.index('/field-controls-native-arrow.js')
    assert acceptance < cluster < legality < unified < arrows_css < arrows_js
    assert '/field-controls-classic.js' not in fast
