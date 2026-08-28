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


def test_all_static_field_multiselects_are_accounted_for():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    assert _multiple_field_select_ids(html) == EXPECTED_MULTIPLE_FIELD_SELECTS


def test_static_and_dynamic_field_multiselects_use_one_generic_checklist_renderer():
    enhancer = (STATIC_DIR / "field-checklists.js").read_text(encoding="utf-8")
    app = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    webapp = (STATIC_DIR.parent / "webapp.py").read_text(encoding="utf-8")

    assert "select[multiple][data-field-select]" in enhancer
    assert '".s4-groups"' in enhancer
    assert '".ta-entity-fields"' in enhancer
    assert '"#cc-features"' in enhancer
    assert '".s4-order"' not in enhancer
    assert 'checkbox.type = "checkbox"' in enhancer
    assert 'host.className = "field-checklist"' in enhancer
    assert 'control.value = values.join(",")' in enhancer
    assert "renderBasicGroupChecklist" not in app
    assert "/field-checklists.js" in webapp
