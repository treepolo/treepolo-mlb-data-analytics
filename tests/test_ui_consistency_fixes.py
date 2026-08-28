import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]
STATIC = ROOT / "src/treepolo_mlb_data/web_static"


def test_ui_consistency_javascript_parses():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is unavailable")
    subprocess.run([node, "--check", str(STATIC / "ui-consistency-fixes.js")], check=True)


def test_fast_status_loads_ui_consistency_layer():
    source = (STATIC / "fast-status.js").read_text(encoding="utf-8")
    assert '/ui-consistency-fixes.js' in source
