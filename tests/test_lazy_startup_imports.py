from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_webapp_import_does_not_eagerly_load_heavy_native_analysis_dependencies():
    code = r'''
import sys
import treepolo_mlb_data.webapp

heavy_roots = ("numpy", "scipy", "sklearn", "duckdb")
loaded = sorted(
    name for name in sys.modules
    if any(name == root or name.startswith(root + ".") for root in heavy_roots)
)
if loaded:
    raise SystemExit("eager heavy imports during webapp startup: " + ", ".join(loaded[:40]))
'''
    subprocess.run([sys.executable, "-c", code], cwd=ROOT, check=True)
