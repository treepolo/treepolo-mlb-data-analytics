from treepolo_mlb_data.webapp import STATIC_DIR


def source(name: str) -> str:
    return (STATIC_DIR / name).read_text(encoding="utf-8")


def test_panel_activation_has_one_owner_and_emits_a_formal_event():
    panels = source("panel-activation.js")
    assert "window.treepoloPanels" in panels
    assert "function activate(panelId" in panels
    assert 'new CustomEvent("treepolo:panel-activated"' in panels
    assert 'item.classList.toggle("active-panel", item.id === panelId)' in panels


def test_checklists_subscribe_to_panel_activation_without_observer_bridge():
    checklist = source("field-checklists.js")
    fast = source("fast-status.js")

    assert 'document.addEventListener("treepolo:panel-activated"' in checklist
    assert "refreshRoot(panel)" in checklist
    assert not (STATIC_DIR / "field-checklist-panel-activation.js").exists()
    assert "/field-checklist-panel-activation.js" not in fast
