import sqlite3
from pathlib import Path

from treepolo_mlb_data.web_analysis import AnalysisFacade
from treepolo_mlb_data.webapp import STATIC_DIR


def test_stage4_ui_loads_cluster_comparison_and_partition_controls():
    controls = (STATIC_DIR / "stage4-controls.js").read_text(encoding="utf-8")
    compare = (STATIC_DIR / "cluster-comparison-page.js").read_text(encoding="utf-8")
    assert "/cluster-comparison-page.js" in controls
    assert "cluster_compare" in controls
    assert "s4-cluster-partitions" in controls
    assert "多階段分群比較 Multi-stage Cluster Comparison" in compare
    assert 'mode: MODE' in compare and 'const MODE = "cluster_compare"' in compare
    assert "每個個體獨立建模" in compare


def test_metric_validation_observer_cannot_self_trigger_on_option_label_changes():
    controls = (STATIC_DIR / "stage4-controls.js").read_text(encoding="utf-8")
    # The observer only needs to notice metric rows being added or removed. Watching
    # the entire subtree lets syncMetricRow() mutate an option label and recursively
    # schedule itself forever, freezing the browser main thread at startup.
    assert 'observe(host,{childList:true});' in controls
    assert 'observe(host,{childList:true,subtree:true});' not in controls
    assert 'empty.textContent!==emptyLabel' in controls


def test_meta_advertises_stage4_modes(tmp_path: Path):
    path = tmp_path / "db.sqlite"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE pitches (pitch_uid TEXT PRIMARY KEY, pitcher INTEGER, pitch_type TEXT, release_speed REAL)")
    conn.commit(); conn.close()
    capabilities = set(AnalysisFacade(path).meta()["capabilities"])
    assert {"workflow", "clustering", "regression", "bootstrap", "cluster_compare"} <= capabilities
