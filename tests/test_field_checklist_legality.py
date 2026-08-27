import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]
STATIC = ROOT / "src/treepolo_mlb_data/web_static"


def read(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


def test_multiple_field_checklists_use_the_same_legality_provider():
    source = read("field-checklists.js")

    assert "window.treepoloLegalFieldOptions?.available" in source
    assert "function legalValues(select)" in source
    assert "allowed && !allowed.has(option.value)" in source
    assert "treepolo:field-legality-ready" in source


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
