from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_analysis_history_table_uses_persistent_history_ids():
    script = read("src/treepolo_mlb_data/web_static/analysis-library-status.js")
    assert "function decorateHistoryIds()" in script
    assert 'document.getElementById("analysis-history-list")' in script
    assert 'th.textContent = "#"' in script
    assert 'item?.id != null ? `#${item.id}` : "—"' in script
    assert "Analysis History ID" in script
    assert 'decorateRows("analysis-history-list", historyItems, 5)' in script


def test_analysis_history_id_decoration_is_idempotent():
    script = read("src/treepolo_mlb_data/web_static/analysis-library-status.js")
    assert '!headerRow.querySelector(".analysis-history-id")' in script
    assert 'row.querySelector(":scope > .analysis-history-id")' in script
    assert 'node.classList?.contains("analysis-history-id")' in script
