"""Known-answer reports: tests marked ``report`` write their numbers to ``docs/research/known_answer_report.json`` and never fail on a number.

They show whether a tool finds the answer in a synthetic world whose answer is known. CI does not run them (``-m "not integration and not report"``); run them by hand with
``python -m pytest -q -p no:cacheprovider -m report`` (PowerShell: set ``$env:PYTHONPATH='src'`` first).
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

REPORT = Path(__file__).resolve().parents[1] / "docs" / "research" / "known_answer_report.json"


def _clean(value: Any) -> Any:
    if hasattr(value, "tolist"):
        value = value.tolist()
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    if isinstance(value, float):
        return round(value, 6) if math.isfinite(value) else None
    return value


def record(name: str, **numbers: Any) -> None:
    """Replace the entry ``name`` of the report with ``numbers`` (the other entries stay)."""

    data = json.loads(REPORT.read_text(encoding="utf-8")) if REPORT.exists() else {}
    data[name] = _clean(numbers)
    REPORT.write_text(json.dumps(data, indent=1, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
