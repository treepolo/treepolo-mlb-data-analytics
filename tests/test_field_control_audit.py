from pathlib import Path


ROOT = Path(__file__).parents[1]
STATIC = ROOT / "src/treepolo_mlb_data/web_static"


def read(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


def test_classic_layer_restores_single_selects_without_touching_multiple_checklists():
    classic = read("field-controls-classic.js")
    checklist = read("field-checklists.js")

    assert 'select[data-field-select]:not([multiple])' in classic
    assert "select.hidden=false" in classic
    assert "select.removeAttribute('hidden')" in classic
    assert "xp-field-search-button" in classic
    assert "搜尋欄位 Search field" in classic
    assert "select[multiple][data-field-select]" in checklist
    assert "field-checklist-search" in checklist
    assert "field-checklist-summary" in checklist


def test_native_datalists_are_removed_and_custom_popup_is_below_control():
    classic = read("field-controls-classic.js")

    assert "removeNativeDatalist" in classic
    assert 'input[list^="ta-fields-"]' in classic
    assert 'input[list^="ta-pipeline-"]' in classic
    assert 'datalist[id^="ta-fields-"]' in classic
    assert 'datalist[id^="ta-pipeline-"]' in classic
    assert "top:calc(100% + 1px)" in classic
    assert "xp-popup-search" in classic


def test_pitch_type_value_domain_never_uses_schema_field_options():
    classic = read("field-controls-classic.js")

    assert "pitch_type: PITCH_TYPES" in classic
    for code in ("FF", "SI", "FC", "SL", "ST", "CU", "KC", "CH", "FS", "FO", "KN", "EP", "SC", "SV"):
        assert f'["{code}",' in classic
    assert "VALUE_DOMAINS[String(field||'').trim()]" in classic
    assert "#role-exclude,.ta-role-exclude" in classic
    assert "decorateValue(i,'pitch_type',true)" in classic
    assert "#cc-reference" in classic
    assert "decorateValue(i,'pitch_type',false)" in classic


def test_condition_value_families_are_semantically_decorated():
    classic = read("field-controls-classic.js")

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
        assert selector in classic
    for selector in (".condition-field", ".s4-filter-field", ".cc-filter-field", ".s4-metric-cond-field", ".ta-event-field"):
        assert selector in classic


def test_advanced_field_reference_registry_is_complete():
    classic = read("field-controls-classic.js")

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
        assert selector in classic
    assert "Prior-stage alias" in classic


def test_classic_layer_loads_after_dynamic_analysis_pages():
    fast = read("fast-status.js")

    acceptance = fast.index('/acceptance-fixes.js')
    cluster = fast.index('/cluster-comparison-page.js')
    classic = fast.index('/field-controls-classic.js')
    assert acceptance < cluster < classic
    assert "await loadScriptOnce" in fast
