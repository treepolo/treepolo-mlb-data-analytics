import sqlite3

from treepolo_mlb_data.fast_status import prepare_fast_status, read_fast_status
from treepolo_mlb_data.webapp import STATIC_DIR


def test_fast_status_exposes_current_data_revision(tmp_path):
    path = tmp_path / "statcast.sqlite3"
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL);
        INSERT INTO settings VALUES ('data_revision', 'rev-current', 'now');
    """)
    conn.commit()
    conn.close()

    prepare_fast_status(path)
    status = read_fast_status(path)
    assert status["data_revision"] == "rev-current"


def test_analysis_library_marks_historical_results_and_is_loaded():
    helper = (STATIC_DIR / "analysis-library-status.js").read_text(encoding="utf-8")
    compare_page = (STATIC_DIR / "cluster-comparison-page.js").read_text(encoding="utf-8")
    assert "/api/data/status" in helper
    assert "舊資料版本 Historical Data" in helper
    assert "重新執行會使用目前資料" in helper
    assert "/analysis-library-status.js" in compare_page


def test_analysis_library_adds_one_edit_action_and_routes_it_to_shared_dialog():
    helper = (STATIC_DIR / "analysis-library-status.js").read_text(encoding="utf-8")

    assert "function decorateSavedEditButtons()" in helper
    assert "analysis-library-edit" in helper
    assert "編輯 Edit" in helper
    assert "編輯名稱與備註 Edit name and notes" in helper
    assert 'document.dispatchEvent(new CustomEvent("treepolo:analysis-edit-request"' in helper
    assert 'document.addEventListener("treepolo:analysis-library-refresh-request", scheduleRefresh)' in helper
