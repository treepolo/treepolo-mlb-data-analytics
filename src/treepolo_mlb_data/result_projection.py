from __future__ import annotations

from typing import Any


DEFAULT_CLIENT_RESULT_LIMIT = 500
MAX_CLIENT_RESULT_LIMIT = 5000


def _resolved_limit(payload: dict[str, Any], default_limit: int | None) -> int | None:
    raw_limit = payload.get("result_limit")
    if raw_limit in (None, ""):
        raw_limit = default_limit
    if raw_limit in (None, ""):
        return None
    return max(1, min(int(raw_limit), MAX_CLIENT_RESULT_LIMIT))


def apply_result_limit(
    result: dict[str, Any],
    payload: dict[str, Any],
    *,
    default_limit: int | None = None,
) -> dict[str, Any]:
    """Project a full analysis result into the row budget sent to the UI.

    The computation result is not changed in place. ``row_count`` remains the
    producer's full match count while ``returned_row_count`` reports the rows
    retained in the client payload.  Callers that need legacy cached results to
    be bounded can provide ``default_limit`` when the original payload did not
    yet contain ``result_limit``.
    """

    limit = _resolved_limit(payload, default_limit)
    if limit is None:
        return result

    limited = dict(result)

    def trim(section: dict[str, Any]) -> dict[str, Any]:
        copy = dict(section)
        rows = copy.get("rows")
        if isinstance(rows, list):
            copy["returned_row_count"] = min(len(rows), limit)
            copy["rows"] = rows[:limit]
            copy["result_limit"] = limit
        return copy

    if isinstance(limited.get("sections"), list):
        limited["sections"] = [
            trim(section) if isinstance(section, dict) else section
            for section in limited["sections"]
        ]
    else:
        limited = trim(limited)
    return limited
