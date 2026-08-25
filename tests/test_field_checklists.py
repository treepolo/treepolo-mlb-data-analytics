import re

from treepolo_mlb_data.webapp import STATIC_DIR


EXPECTED_MULTIPLE_FIELD_SELECTS = {
    "basic-group",
    "arsenal-entities",
    "role-entities",
    "temporal-entities",
    "percentile-entities",
    "cross-unit",
    "cross-baseline",
    "change-entities",
}


def _multiple_field_select_ids(html: str) -> set[str]:
    ids: set[str] = set()
    for attrs in re.findall(r"<select\s+([^>]*\bmultiple\b[^>]*)>", html):
        if "data-field-select" not in attrs:
            continue
        match = re.search(r'id="([^"]+)"', attrs)
        assert match is not None
        ids.add(match.group(1))
    return ids


def test_all_current_field_multiselects_are_accounted_for():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    assert _multiple_field_select_ids(html) == EXPECTED_MULTIPLE_FIELD_SELECTS


def test_remaining_field_multiselects_render_as_checklists():
    enhancer = (STATIC_DIR / "field-checklists.js").read_text(encoding="utf-8")
    app = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    bootstrap = (STATIC_DIR / "fast-status.js").read_text(encoding="utf-8")

    assert "renderBasicGroupChecklist" in app
    assert 'select[multiple][data-field-select]' in enhancer
    assert 'select.id === "basic-group"' in enhancer
    assert 'checkbox.type = "checkbox"' in enhancer
    assert "option.selected = checkbox.checked" in enhancer
    assert 'host.className = "field-checklist"' in enhancer
    assert '/field-checklists.js' in bootstrap
