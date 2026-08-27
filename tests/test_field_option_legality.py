from __future__ import annotations

import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest

from treepolo_mlb_data.schema import field_capabilities
from treepolo_mlb_data.web_analysis import AnalysisFacade


ROOT = Path(__file__).parents[1]
STATIC = ROOT / "src/treepolo_mlb_data/web_static"


def read(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


def test_schema_capabilities_are_derived_centrally():
    numeric = set(field_capabilities("new_numeric_metric", "REAL"))
    dated = set(field_capabilities("custom_game_date", "TEXT"))
    pitch_class = set(field_capabilities("pitch_name", "TEXT"))
    canonical = set(field_capabilities("pitch_type", "TEXT"))

    assert {"numeric", "trend_orderable"} <= numeric
    assert {"temporal", "trend_orderable"} <= dated
    assert "pitch_classification" in pitch_class
    assert {"pitch_classification", "canonical_pitch_type"} <= canonical


def test_meta_exposes_capabilities_for_runtime_schema(tmp_path):
    db = tmp_path / "meta.sqlite"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE pitches (pitch_uid TEXT PRIMARY KEY, pitch_type TEXT, game_date TEXT, custom_metric REAL)")
    conn.commit()
    conn.close()

    fields = {item["name"]: item for item in AnalysisFacade(db).meta()["fields"]}
    assert "numeric" in fields["custom_metric"]["capabilities"]
    assert "temporal" in fields["game_date"]["capabilities"]
    assert "canonical_pitch_type" in fields["pitch_type"]["capabilities"]


def test_legality_layer_tracks_pipeline_shape_and_prior_stage_outputs():
    source = read("field-option-legality-v3.js")

    assert 'kind === "aggregate"' in source
    assert 'const out=new Map()' in source
    assert 'kind === "project"' in source
    for kind in (
        "derive", "rolling", "offset", "trend", "rank", "arsenal_signature",
        "pitch_role_select", "pitch_role_annotate", "empirical_percentile", "event_pattern_cohorts",
    ):
        assert f'kind === "{kind}"' in source
    assert "beforeStage" in source
    assert "afterPreparation" in source


def test_field_lists_use_capabilities_not_frontend_field_allowlists():
    source = read("field-option-legality-v3.js")

    assert 'withCapability(all,"numeric")' in source
    assert 'withCapability(all,"trend_orderable")' in source
    assert '"pitch_classification":"canonical_pitch_type"' in source
    assert "canonical_pitch_type" in source
    assert "pitch_classification" in source
    assert 'new Set(["pitch_type"' not in source
    assert 'new Set(["pitch_type", "pitch_name"' not in source
    assert "ORDERABLE_TEXT_FIELDS" not in source
    assert 'fetch("/api/meta"' in source


def test_filter_fields_are_not_accidentally_restricted_to_numeric():
    source = read("field-option-legality-v3.js")

    assert 'control.matches(".cc-filter-field,.s4-filter-field,.condition-field")' in source
    assert '.cc-field"))return numeric()' not in source
    assert "#cc-selection-field,#cc-evaluation-field" in source


def test_numeric_and_type_compatible_controls_are_narrowed():
    source = read("field-option-legality-v3.js")

    for selector in (
        ".s4-metric-field", ".s4-left,.s4-right-field", ".s4-value-field",
        ".ta-metric-cond-value-field", ".ta-value-field", ".ta-percentile-field",
        "#s4-cluster-features", "#s4-reg-dependent", "#s4-reg-independent",
        "#s4-boot-value", "#cc-features", "#cc-selection-field", "#cc-evaluation-field",
        ".metric-field", "#role-value-field", "#percentile-value", "#temporal-value", "#cross-value",
    ):
        assert selector in source
    assert "compatible(map,source)" in source


def test_every_ui_field_control_consumes_the_legality_provider():
    legality = read("field-option-legality-v3.js")
    unified = read("field-controls-unified.js")

    assert "window.treepoloLegalFieldOptions={available:control=>legalDescriptors(control).map(item=>item.value),refresh}" in legality
    assert "window.treepoloLegalFieldOptions?.available" in unified
    assert "function legalFieldOptions(control)" in unified
    assert "legalFieldOptions(select)" in unified
    assert "legalFieldOptions(input)" in unified


def test_native_single_selects_that_need_capability_narrowing_are_rebuilt_from_legal_descriptors():
    source = read("field-option-legality-v3.js")

    assert "rebuildSelect" in source
    assert "#cc-selection-field,#cc-evaluation-field" in source
    assert "descriptors.forEach" in source


def test_semantic_value_lists_remain_domain_scoped_not_field_scoped():
    unified = read("field-controls-unified.js")

    assert "function domain(field)" in unified
    assert "pitch_type: PITCH_TYPES" in unified
    assert "valueField(input)" in unified
    assert "#role-exclude,.ta-role-exclude" in unified
    assert "#cc-reference" in unified


def test_legality_provider_loads_before_unified_ui_controls():
    fast = read("fast-status.js")

    acceptance = fast.index('/acceptance-fixes.js')
    cluster = fast.index('/cluster-comparison-page.js')
    legality = fast.index('/field-option-legality-v3.js')
    unified = fast.index('/field-controls-unified.js')
    assert acceptance < cluster < legality < unified
    assert '/field-option-legality-v2.js' not in fast
    assert '/field-controls-classic.js' not in fast


def test_legality_and_unified_control_javascript_syntax():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is not installed")
    for filename in ("field-option-legality-v3.js", "field-controls-unified.js"):
        subprocess.run(
            [node, "--check", str(STATIC / filename)],
            check=True,
            capture_output=True,
            text=True,
        )
