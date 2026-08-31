from __future__ import annotations

import csv
import io
import json
import math
from collections import defaultdict

import pytest
import requests

pytestmark = pytest.mark.integration
BASE = "https://baseballsavant.mlb.com"
OHTANI = 660271
YEAR = 2026
STATCAST_CSV_URL = (
    f"{BASE}/statcast_search/csv?all=true&hfPT=&hfAB=&hfBBT=&hfPR=&hfZ=&stadium="
    "&hfBBL=&hfNewZones=&hfGT=R%7C=&hfSea=&hfSit=&player_type=pitcher"
    "&hfOuts=&opponent=&pitcher_throws=&batter_stands=&hfSA="
    f"&game_date_gt={YEAR}-01-01&game_date_lt={YEAR}-12-31&pitchers_lookup%5B%5D={OHTANI}"
    "&team=&position=&hfRO=&home_road=&hfFlag=&metric_1=&hfInn=&min_pitches=0"
    "&min_results=0&group_by=name&sort_col=pitches&player_event_sort=h_launch_speed"
    "&sort_order=desc&min_abs=0&type=details&"
)
SPIN_API = f"{BASE}/savant/api/v1/spin-direction-pitches"


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


def load_savant_aggregate(session: requests.Session, pitch_type: str) -> dict:
    response = session.get(
        SPIN_API,
        params={
            "pitcher": OHTANI,
            "year": YEAR,
            "pitch_type": pitch_type,
            "pov": "Pit",
        },
        timeout=60,
    )
    response.raise_for_status()
    payload = response.json()
    rows = payload if isinstance(payload, list) else payload.get("data", payload.get("rows", []))
    candidates = [
        row for row in rows
        if isinstance(row, dict)
        and row.get("api_pitch_type") == pitch_type
        and int(row.get("season") or YEAR) == YEAR
    ]
    if not candidates:
        return {"pitch_type": pitch_type, "found": False, "response_rows": len(rows)}
    row = candidates[0]
    return {
        "pitch_type": pitch_type,
        "found": True,
        "n_pitches": row.get("n_pitches"),
        "hawkeye_measured": parse_float(row.get("hawkeye_measured")),
        "movement_inferred": parse_float(row.get("movement_inferred")),
        "hawkeye_clock": row.get("hawkeye_measured_clock_label"),
        "inferred_clock": row.get("movement_inferred_clock_label"),
    }


def test_deep_spin_orientation_probe():
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 (compatible; one-off-research/22.0)"})
    row_count, by_pitch = load_pitch_axes(session)
    comparisons = []
    for pitch_type, values in sorted(by_pitch.items()):
        aggregate = load_savant_aggregate(session, pitch_type)
        mean_axis = circular_mean(values)
        reverse_axis = None if mean_axis is None else (360 - mean_axis) % 360
        comparisons.append({
            **aggregate,
            "csv_n": len(values),
            "csv_circular_mean_spin_axis": mean_axis,
            "reverse_360_minus_spin_axis": reverse_axis,
            "direct_difference_deg": circular_diff(mean_axis, aggregate.get("hawkeye_measured")),
            "reverse_difference_deg": circular_diff(reverse_axis, aggregate.get("hawkeye_measured")),
            "reverse_difference_vs_inferred_deg": circular_diff(reverse_axis, aggregate.get("movement_inferred")),
            "csv_min": min(values),
            "csv_max": max(values),
        })
    report = {
        "regular_season_csv_rows": row_count,
        "pitch_types": sorted(by_pitch),
        "comparisons": comparisons,
        "mean_reverse_difference_deg": (
            sum(row["reverse_difference_deg"] for row in comparisons if row.get("reverse_difference_deg") is not None)
            / sum(row.get("reverse_difference_deg") is not None for row in comparisons)
        ),
    }
    pytest.fail("\n===== ALL-PITCH-TYPE SPIN AXIS COORDINATE REPORT =====\n" + json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
