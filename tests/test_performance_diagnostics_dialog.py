from treepolo_mlb_data.webapp import STATIC_DIR


def test_performance_diagnostics_dialog_has_single_visibility_owner():
    source = (STATIC_DIR / "performance-diagnostics.js").read_text(encoding="utf-8")
    assert "function setPanelVisible(layer, visible)" in source
    assert 'layer.style.display = visible ? "grid" : "none";' in source
    assert 'layer.hidden = true' not in source
    assert 'layer.hidden = false' not in source
    assert 'data-perf-close' in source
    assert 'setPanelVisible(layer, false)' in source
