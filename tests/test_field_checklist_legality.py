import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]
STATIC = ROOT / "src/treepolo_mlb_data/web_static"


def read(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


def test_every_field_checklist_uses_the_same_legality_provider():
    source = read("field-checklists.js")

    assert "window.treepoloLegalFieldOptions?.available" in source
    assert "function legalValues(control)" in source
    assert "const allowed = new Set(legal)" in source
    assert "const kept = before.filter(value => allowed.has(value))" in source
    assert "treepolo:field-legality-ready" in source
    assert "return null" in source  # dynamic presets wait until provider exists


def test_field_legality_ready_event_is_emitted_after_unified_controls_load():
    fast = read("fast-status.js")

    unified = fast.index('/field-controls-unified.js')
    ready = fast.index('treepolo:field-legality-ready')
    assert unified < ready


def test_field_checklist_javascript_parses():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is unavailable")
    subprocess.run([node, "--check", str(STATIC / "field-checklists.js")], check=True)
