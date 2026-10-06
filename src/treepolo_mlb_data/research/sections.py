from __future__ import annotations

import math
from typing import Any, Sequence


def clean(value: Any, digits: int = 10) -> Any:
    """Round floats so a rerun compares equal despite floating-point summation order; NaN and infinity become None."""

    if isinstance(value, float):
        return round(value, digits) if math.isfinite(value) else None
    return value


def make_section(
    title: str, columns: Sequence[str], rows: list[dict[str, Any]], keys: Sequence[str], backend: str, digits: int = 10,
) -> dict[str, Any]:
    """One result section in the shape ResearchService stores (see data_profile): only ``columns`` are kept, floats are cleaned."""

    kept = [{c: clean(row.get(c), digits) for c in columns} for row in rows]
    return {"title": title, "columns": list(columns), "rows": kept, "row_count": len(kept),
            "grain": {"keys": list(keys), "label": title}, "backend": backend}
