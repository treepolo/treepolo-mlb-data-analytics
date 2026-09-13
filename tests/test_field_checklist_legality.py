import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]
STATIC = ROOT / "src/treepolo_mlb_data/web_static"


def read(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


def test_every_field_checklist_uses_the_shared_legality_provider():
    source = read("field-checklists.js")

    assert "const provider = window.treepoloLegalFieldOptions" in source
    assert 'typeof provider?.descriptors === "function"' in source
    assert 'typeof provider?.available === "function"' in source
    assert "treepolo:field-legality-ready" in source
    assert "treepoloFieldChecklistsApi" in source


def test_legality_is_foundational_and_committed_after_catalog_is_ready():
    fast = read("fast-status.js")

    legality = fast.index('/field-option-legality-v3.js')
    unified = fast.index('/field-controls-unified.js')
    assert legality < unified
    assert "await waitForFieldCatalog();" in fast
    assert "provider.refresh?.();" in fast
    assert "treepoloFieldChecklistsApi?.refreshRoot?.(document)" in fast
    assert 'new CustomEvent("treepolo:field-legality-ready"' in fast


def test_field_checklist_javascript_parses():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is unavailable")
    subprocess.run([node, "--check", str(STATIC / "field-checklists.js")], check=True)
