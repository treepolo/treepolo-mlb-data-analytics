from __future__ import annotations

import csv
import io
import json
import math
import re
from collections import defaultdict

import pytest
import requests

pytestmark = pytest.mark.integration
BASE = "https://baseballsavant.mlb.com"
OHTANI = 660271
YEAR = 2026
PLAYER_URL = f"{BASE}/savant-player/shohei-ohtani-{OHTANI}?playerType=pitcher"
STATCAST_CSV_URL = (
    f"{BASE}/statcast_search/csv?all=true&hfPT=&hfAB=&hfBBT=&hfPR=&hfZ=&stadium="
    "&hfBBL=&hfNewZones=&hfGT=R%7C=&hfSea=&hfSit=&player_type=pitcher"
    "&hfOuts=&opponent=&pitcher_throws=&batter_stands=&hfSA="
    f"&game_date_gt={YEAR}-01-01&game_date_lt={YEAR}-12-31&pitchers_lookup%5B%5D={OHTANI}"
    "&team=&position=&hfRO=&home_road=&hfFlag=&metric_1=&hfInn=&min_pitches=0"
    "&min_results=0&group_by=name&sort_col=pitches&player_event_sort=h_launch_speed"
    "&sort_order=desc&min_abs=0&type=details&"
)


def parse_float(value):
    try:
        return None if value in (None, "") else float(value)
    except (TypeError, ValueError):
        return None


def circular_mean(values: list[float]) -> float | None:
    if not values:
        return None
    x = sum(math.cos(math.radians(v)) for v in values)
    y = sum(math.sin(math.radians(v)) for v in values)
    return math.degrees(math.atan2(y, x)) % 360


def circular_diff(a: float | None, b: float | None) -> float | None:
    if a is None or b is None:
        return None
    return abs((a - b + 180) % 360 - 180)


def load_pitch_axes(session: requests.Session) -> tuple[int, dict[str, list[float]]]:
    response = session.get(STATCAST_CSV_URL, timeout=120)
    response.raise_for_status()
    reader = csv.DictReader(io.StringIO(response.content.decode("utf-8-sig")))
    rows = list(reader)
    by_pitch: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        pitch_type = row.get("pitch_type") or ""
        axis = parse_float(row.get("spin_axis"))
        if pitch_type and axis is not None:
            by_pitch[pitch_type].append(axis)
    return len(rows), dict(by_pitch)


def extract_balanced_array(text: str, start: int) -> str:
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                return text[start:index + 1]
    raise AssertionError("unterminated spinAxis array")


def load_embedded_spin_axis(session: requests.Session) -> list[dict]:
    response = session.get(PLAYER_URL, timeout=60)
    response.raise_for_status()
    text = response.text
    patterns = (
        r'"spinAxis"\s*:\s*\[',
        r'\bspinAxis\s*:\s*\[',
        r'\bspinAxis\s*=\s*\[',
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        start = text.find("[", match.start())
        raw = extract_balanced_array(text, start)
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, list) and payload:
            return [row for row in payload if isinstance(row, dict)]
    raise AssertionError("could not parse embedded serverVals.spinAxis")


def select_aggregate(rows: list[dict], pitch_type: str) -> dict | None:
    candidates = [
        row for row in rows
        if row.get("api_pitch_type") == pitch_type
        and int(row.get("season") or YEAR) == YEAR
    ]
    return candidates[0] if candidates else None


def test_deep_spin_orientation_probe():
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 (compatible; one-off-research/23.0)"})
    row_count, by_pitch = load_pitch_axes(session)
    spin_rows = load_embedded_spin_axis(session)
    comparisons = []
    for pitch_type, values in sorted(by_pitch.items()):
        aggregate = select_aggregate(spin_rows, pitch_type)
        mean_axis = circular_mean(values)
        reverse_axis = None if mean_axis is None else (360 - mean_axis) % 360
        measured = parse_float(aggregate.get("hawkeye_measured")) if aggregate else None
        inferred = parse_float(aggregate.get("movement_inferred")) if aggregate else None
        comparisons.append({
            "pitch_type": pitch_type,
            "csv_n": len(values),
            "embedded_found": aggregate is not None,
            "embedded_n_pitches": aggregate.get("n_pitches") if aggregate else None,
            "csv_circular_mean_spin_axis": mean_axis,
            "reverse_360_minus_spin_axis": reverse_axis,
            "hawkeye_measured": measured,
            "movement_inferred": inferred,
            "hawkeye_clock": aggregate.get("hawkeye_measured_clock_label") if aggregate else None,
            "inferred_clock": aggregate.get("movement_inferred_clock_label") if aggregate else None,
            "direct_difference_deg": circular_diff(mean_axis, measured),
            "reverse_difference_deg": circular_diff(reverse_axis, measured),
            "reverse_difference_vs_inferred_deg": circular_diff(reverse_axis, inferred),
        })
    valid = [row for row in comparisons if row["reverse_difference_deg"] is not None]
    report = {
        "regular_season_csv_rows": row_count,
        "embedded_spin_axis_row_count": len(spin_rows),
        "embedded_2026_pitch_types": sorted({row.get("api_pitch_type") for row in spin_rows if int(row.get("season") or 0) == YEAR and row.get("api_pitch_type")}),
        "comparisons": comparisons,
        "mean_reverse_difference_deg": sum(row["reverse_difference_deg"] for row in valid) / len(valid) if valid else None,
        "max_reverse_difference_deg": max((row["reverse_difference_deg"] for row in valid), default=None),
    }
    pytest.fail("\n===== EMBEDDED SPINAXIS VS PER-PITCH STATCAST REPORT =====\n" + json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
