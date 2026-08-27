from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]
STATIC = ROOT / "src/treepolo_mlb_data/web_static"


def read(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


def test_legality_layer_tracks_pipeline_shape_and_prior_stage_outputs():
    source = read("field-option-legality-v2.js")

    # Aggregate and Project are shape-changing stages: fields that no longer exist
    # must not remain in later popups. Other stages add typed aliases.
    assert 'kind === "aggregate"' in source
    assert 'const out = new Map()' in source
    assert 'kind === "project"' in source
    assert 'kind === "derive"' in source
    assert 'kind === "rolling"' in source
    assert 'kind === "offset"' in source
    assert 'kind === "trend"' in source
    assert 'kind === "rank"' in source
    assert 'kind === "arsenal_signature"' in source
    assert 'kind === "pitch_role_select"' in source
    assert 'kind === "pitch_role_annotate"' in source
    assert 'kind === "empirical_percentile"' in source
    assert 'kind === "event_pattern_cohorts"' in source
    assert "beforeStage" in source
    assert "afterPreparation" in source


def test_numeric_and_type_compatible_controls_are_narrowed():
    source = read("field-option-legality-v2.js")

    for selector in (
        ".s4-metric-field",
        ".s4-left,.s4-right-field",
        ".s4-value-field",
        ".ta-metric-cond-value-field",
        ".ta-value-field",
        ".ta-percentile-field",
        "#s4-cluster-features",
        "#s4-reg-dependent",
        "#s4-reg-independent",
        "#s4-boot-value",
        "#cc-features",
        "#cc-selection-field",
        "#cc-evaluation-field",
        ".metric-field",
        "#role-value-field",
        "#percentile-value",
        "#temporal-value",
        "#cross-value",
    ):
        assert selector in source
    assert "numericOnly" in source
    assert "compatible(map, source)" in source
    assert "NUMERIC_SQL" in source
    assert 'fetch("/api/meta"' in source


def test_pitch_field_lists_are_pitch_fields_only():
    source = read("field-option-legality-v2.js")

    assert 'control.matches(".ta-pitch-field")' in source
    assert 'new Set(["pitch_type", "pitch_name"])' in source
    assert 'new Set(["pitch_type"])' in source


def test_advanced_popup_is_owned_by_legal_option_provider():
    source = read("field-option-legality-v2.js")

    assert "renderOwnedPopup" in source
    assert "legalDescriptors(input)" in source
    assert "沒有合法項目 No legal matches" in source
    assert "window.treepoloLegalFieldOptions" in source
    assert ".xp-edit-shell > .xp-popup" in source


def test_native_single_selects_are_rebuilt_from_legal_descriptors():
    source = read("field-option-legality-v2.js")

    assert "rebuildSelect" in source
    assert ".metric-field,#role-value-field,#percentile-value,#temporal-value,#cross-value,.cc-field" in source
    assert "actual.length === allowed.length" in source
    assert "descriptors.forEach" in source


def test_semantic_value_lists_remain_domain_scoped_not_field_scoped():
    classic = read("field-controls-classic.js")

    assert "function domain(field)" in classic
    assert "VALUE_DOMAINS[String(field||'').trim()]" in classic
    assert "pitch_type: PITCH_TYPES" in classic
    assert "valueField(input)" in classic
    assert "#role-exclude,.ta-role-exclude" in classic
    assert "#cc-reference" in classic


def test_legality_layer_loads_after_classic_controls():
    fast = read("fast-status.js")

    acceptance = fast.index('/acceptance-fixes.js')
    cluster = fast.index('/cluster-comparison-page.js')
    classic = fast.index('/field-controls-classic.js')
    legality = fast.index('/field-option-legality-v2.js')
    assert acceptance < cluster < classic < legality


def test_legality_javascript_syntax():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is not installed")
    subprocess.run(
        [node, "--check", str(STATIC / "field-option-legality-v2.js")],
        check=True,
        capture_output=True,
        text=True,
    )
