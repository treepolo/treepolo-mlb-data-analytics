from __future__ import annotations

import math
from typing import Any

from ..schema import IDENTIFIER_COLUMNS
from .numerical import NumericalTable


# Public Statcast spin_axis is a direction on a circle, not a linear scalar.
# Numerical models therefore encode it on the unit circle so 359° and 1° are
# close to one another instead of appearing 358 units apart.
CIRCULAR_DEGREE_FIELDS = frozenset({"spin_axis"})


def reject_identifier_features(fields: tuple[str, ...], *, label: str = "model features") -> None:
    invalid = sorted({field for field in fields if field in IDENTIFIER_COLUMNS})
    if invalid:
        raise ValueError(
            f"{label} cannot use identifier fields as continuous numerical features: {', '.join(invalid)}"
        )


def _finite_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def encode_circular_features(
    table: NumericalTable,
    features: tuple[str, ...],
) -> tuple[NumericalTable, tuple[str, ...], dict[str, tuple[str, str]]]:
    """Expand circular degree features into sin/cos coordinates.

    The original field remains in the table for display/identity purposes while
    the numerical model receives two unit-circle coordinates. Non-circular
    features pass through unchanged.
    """
    reject_identifier_features(features)

    encoded_features: list[str] = []
    encodings: dict[str, tuple[str, str]] = {}
    rows = [dict(row) for row in table.rows]
    columns = list(table.columns)

    for field in features:
        if field not in CIRCULAR_DEGREE_FIELDS:
            encoded_features.append(field)
            continue

        sin_field = f"{field}_sin"
        cos_field = f"{field}_cos"
        if sin_field not in columns:
            columns.append(sin_field)
        if cos_field not in columns:
            columns.append(cos_field)

        for row in rows:
            number = _finite_float(row.get(field))
            if number is None:
                row[sin_field] = None
                row[cos_field] = None
                continue
            theta = math.radians(number % 360.0)
            row[sin_field] = math.sin(theta)
            row[cos_field] = math.cos(theta)

        encoded_features.extend((sin_field, cos_field))
        encodings[field] = (sin_field, cos_field)

    return (
        NumericalTable(tuple(columns), tuple(rows), table.grain),
        tuple(encoded_features),
        encodings,
    )


def complete_partition_counts(
    table: NumericalTable,
    *,
    features: tuple[str, ...],
    partition_fields: tuple[str, ...],
) -> dict[tuple[Any, ...], int]:
    """Count rows with complete finite feature vectors in each partition."""
    counts: dict[tuple[Any, ...], int] = {}
    for row in table.rows:
        key = tuple(row.get(field) for field in partition_fields)
        if any(value is None for value in key):
            continue
        complete = True
        for field in features:
            if _finite_float(row.get(field)) is None:
                complete = False
                break
        if complete:
            counts[key] = counts.get(key, 0) + 1
        else:
            counts.setdefault(key, 0)
    return counts
