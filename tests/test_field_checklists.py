import re

from treepolo_mlb_data.webapp import STATIC_DIR


EXPECTED_STATIC_MULTI_FIELDS = {
    "basic-group",
    "arsenal-entities",
    "role-entities",
    "temporal-entities",
    "percentile-entities",
    "cross-unit",
    "cross-baseline",
    "change-entities",
}


def _csv_multi_field_ids(html: str) -> set[str]:
    ids: set[str] = set()
    for attrs in re.findall(r"<input\s+([^>]*\bdata-multi-field\b[^>]*)>", html):
        match = re.search(r'id="([^"]+)"', attrs)
        if match:
            ids.add(match.group(1))
    return ids


def test_all_static_unordered_multifields_use_editable_csv_canonical_inputs():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    assert _csv_multi_field_ids(html) == EXPECTED_STATIC_MULTI_FIELDS
    assert "<select multiple" not in html


def test_static_and_dynamic_multifields_use_one_generic_checklist_renderer():
    checklist = (STATIC_DIR / "field-checklists.js").read_text(encoding="utf-8")
    model = (STATIC_DIR / "multi-field-model.js").read_text(encoding="utf-8")
    app = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    webapp = (STATIC_DIR.parent / "webapp.py").read_text(encoding="utf-8")

    assert "input[data-multi-field]" in checklist
    assert '".s4-groups"' in checklist
    assert '".ta-entity-fields"' in checklist
    assert '"#cc-features"' in checklist
    assert '".s4-order"' not in checklist
    assert 'checkbox.type = "checkbox"' in checklist
    assert 'host.className = "field-checklist"' in checklist
    assert "treepoloMultiField" in checklist
    assert "control.value = next" in model
    assert "renderBasicGroupChecklist" not in app
    assert "/field-checklists.js" in webapp
