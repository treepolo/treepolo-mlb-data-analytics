from treepolo_mlb_data.webapp import STATIC_DIR


def source(name: str) -> str:
    return (STATIC_DIR / name).read_text(encoding="utf-8")


def test_native_date_inputs_accept_full_iso_date_paste_globally():
    js = source("ui-consistency-fixes.js")
    assert "const ISO_DATE_RE = /^\\d{4}-\\d{2}-\\d{2}$/;" in js
    assert "document.addEventListener(\"paste\"" in js
    assert "input[type=\"date\"]" in js
    assert "event.clipboardData?.getData(\"text\")" in js
    assert "input.value = text;" in js
    assert 'new Event("input", { bubbles:true })' in js
    assert 'new Event("change", { bubbles:true })' in js


def test_date_paste_keeps_native_date_control_and_rejects_invalid_dates():
    html = source("index.html")
    js = source("ui-consistency-fixes.js")
    assert 'type="date"' in html
    assert "function isValidIsoDate(text)" in js
    assert "date.toISOString().slice(0, 10) === text" in js
    assert "if (!isValidIsoDate(text)) return;" in js
