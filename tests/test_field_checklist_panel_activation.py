from treepolo_mlb_data.webapp import STATIC_DIR


def source(name: str) -> str:
    return (STATIC_DIR / name).read_text(encoding="utf-8")


def test_checklists_refresh_when_any_panel_becomes_active():
    bridge = source("field-checklist-panel-activation.js")
    assert 'attributeFilter:["class"]' in bridge
    assert 'panel.classList.contains("active-panel")' in bridge
    assert "treepoloFieldChecklistsApi?.refresh?.()" in bridge
    assert 'observe(main, { childList:true })' in bridge


def test_panel_activation_bridge_is_loaded_after_field_controls():
    bootstrap = source("fast-status.js")
    unified_at = bootstrap.index('loadScriptOnce("/field-controls-unified.js"')
    bridge_at = bootstrap.index('loadScriptOnce("/field-checklist-panel-activation.js"')
    assert unified_at < bridge_at
