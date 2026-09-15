from __future__ import annotations

from pathlib import Path

from treepolo_mlb_data import webapp


STATIC = Path(webapp.STATIC_DIR)


def test_dataset_workspace_reads_runtime_meta_and_labels_the_ui():
    source = (STATIC / "dataset-workspace.js").read_text(encoding="utf-8")
    assert 'fetch("/api/meta"' in source
    assert "treepoloDatasetReady" in source
    assert "dataset-workspace-badge" in source
    assert "document.title = productName" in source
    assert "speed_unit" in source
    assert "distance_unit" in source


def test_cpbl_workspace_does_not_load_mlb_only_supplemental_controls():
    source = (STATIC / "fast-status.js").read_text(encoding="utf-8")
    assert 'dataset?.id === "mlb"' in source
    assert 'loadScriptOnce("/supplemental-data.js"' in source
    assert 'loadScriptOnce("/dataset-workspace.js"' in source
