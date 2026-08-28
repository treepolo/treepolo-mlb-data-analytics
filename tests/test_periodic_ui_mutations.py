from treepolo_mlb_data.webapp import STATIC_DIR


def test_clock_reuses_one_text_node_instead_of_replacing_children_each_second():
    source = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    start = source.index("function updateClock()")
    end = source.index("async function init()", start)
    clock = source[start:end]

    assert "text.nodeValue = value" in clock
    assert "host.replaceChildren(document.createTextNode(value))" in clock
    assert '.textContent = new Intl.DateTimeFormat' not in clock


def test_fast_status_does_not_rewrite_unchanged_notice_text_each_poll():
    source = (STATIC_DIR / "fast-status.js").read_text(encoding="utf-8")

    assert "const message = status.summary_state" in source
    assert "if (notice.textContent !== message) notice.textContent = message;" in source
