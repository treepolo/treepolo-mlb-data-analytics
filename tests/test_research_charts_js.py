from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_chart_module_node_tests_pass():
    done = subprocess.run(["node", str(ROOT / "tests" / "research-charts.test.cjs")], capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "research-charts: ok" in done.stdout
