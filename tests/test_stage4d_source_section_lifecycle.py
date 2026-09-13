from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_visualization_source_change_invalidates_old_result_section():
    script = (ROOT / "src" / "treepolo_mlb_data" / "web_static" / "stage4d-latest-request.js").read_text(encoding="utf-8")

    assert "resetResultSectionForNewSource" in script
    assert '#viz-source-kind,#viz-source-item' in script
    assert 'option.value = "0"' in script
    assert 'section.value = "0"' in script
    assert 'section.dataset.sourcePending = "true"' in script
    assert 'section.disabled = true' in script
    assert '#viz-load' in script
    assert 'option[data-source-pending="true"]' in script
    assert 'section.disabled = false' in script
