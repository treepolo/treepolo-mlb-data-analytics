import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]
STATIC = ROOT / "src/treepolo_mlb_data/web_static"


def read(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


def test_field_legality_javascript_parses():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is unavailable")
    subprocess.run([node, "--check", str(STATIC / "field-legality.js")], check=True)


def test_field_legality_is_schema_and_pipeline_driven_not_column_allowlisted():
    source = read("field-legality.js")

    assert 'fetch("/api/meta"' in source
    assert "fieldTypes" in source
    assert "baseCatalog" in source
    assert "pipelineCatalog" in source
    assert "processStage" in source
    assert "aggregateType" in source
    assert "requirementFor" in source
    assert "legalOptions" in source

    # The legality resolver must not enumerate known Statcast measurement columns.
    # They arrive from /api/meta and are filtered by type/capability at runtime.
    for hardcoded_measurement in (
        "release_speed", "release_spin_rate", "pfx_x", "pfx_z", "launch_speed", "launch_angle"
    ):
        assert hardcoded_measurement not in source


def test_numeric_and_compatible_controls_are_filtered_by_runtime_types():
    source = read("field-legality.js")

    assert "NUMERIC_TYPES" in source
    assert 'kind: "numeric"' in source
    assert 'kind: "compatible"' in source
    assert "sameFamily" in source
    assert "aggregateNeedsNumeric" in source
    assert "#s4-cluster-features" in source
    assert "#s4-reg-dependent" in source
    assert ".s4-left" in source
    assert ".s4-value-field" in source
    assert ".ta-metric-cond-value-field" in source


def test_pipeline_catalog_tracks_actual_prior_stage_outputs():
    source = read("field-legality.js")

    # Aggregate/project change the available schema; alias-producing stages extend it.
    assert 'kind === "aggregate"' in source
    assert 'kind === "project"' in source
    for kind in (
        "derive", "rolling", "offset", "trend", "rank", "arsenal_signature",
        "pitch_role_select", "pitch_role_annotate", "empirical_percentile", "event_pattern_cohorts",
    ):
        assert f'kind === "{kind}"' in source
    assert "Prior-stage alias" in source


def test_native_and_advanced_field_lists_both_use_legality_layer():
    source = read("field-legality.js")

    assert "NATIVE_FIELD_SELECTOR" in source
    assert "refreshNativeSelect" in source
    assert "FIELD_INPUT_RULES" in source
    assert "legalizeEditPopup" in source
    assert "搜尋合法欄位 Search legal fields" in source
    assert "沒有合法符合項目 No legal matches" in source


def test_legality_layer_loads_after_classic_controls():
    fast = read("fast-status.js")

    classic = fast.index('/field-controls-classic.js')
    legality = fast.index('/field-legality.js')
    assert classic < legality
    assert 'await loadScriptOnce("/field-legality.js", "fieldLegality")' in fast
