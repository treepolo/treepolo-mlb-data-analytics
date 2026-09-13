from treepolo_mlb_data.webapp import STATIC_DIR


def source(name: str) -> str:
    return (STATIC_DIR / name).read_text(encoding="utf-8")


def test_basic_metric_layout_is_scoped_and_has_column_labels():
    js = source("ui-consistency-fixes.js")
    assert '"統計 Aggregate"' in js
    assert '"欄位 Field"' in js
    assert '"不重複 Distinct"' in js
    assert "#basic-metrics .metric-row" in js
    assert "#basic-panel .form-grid > div" in js
    assert "grid-template-columns: minmax(105px, .9fr) minmax(0, 1.4fr) minmax(105px, .9fr) 28px;" in js


def test_basic_metric_header_tracks_dynamic_metric_rows():
    js = source("ui-consistency-fixes.js")
    assert 'container.querySelector(":scope > .basic-metric-head")' in js
    assert 'head.hidden = !list.querySelector(".metric-row");' in js
    assert 'document.addEventListener("treepolo:analysis-options-changed"' in js
    assert "normalizeBasicMetrics();" in js
