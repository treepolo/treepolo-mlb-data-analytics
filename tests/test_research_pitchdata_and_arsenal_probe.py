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
SPIN_API_URL = f"{BASE}/savant/api/v1/spin-direction-pitches?pitcher={OHTANI}&year={YEAR}&pov=Pit"


def circular_mean_degrees(values: list[float]) -> float | None:
    if not values:
        return None
    x = sum(math.cos(math.radians(value)) for value in values) / len(values)
    y = sum(math.sin(math.radians(value)) for value in values) / len(values)
    return math.degrees(math.atan2(y, x)) % 360


def circular_difference(a: float | None, b: float | None) -> float | None:
    if a is None or b is None:
        return None
    return abs((a - b + 180) % 360 - 180)


def parse_float(value):
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def csv_probe(session: requests.Session) -> dict:
    response = session.get(STATCAST_CSV_URL, timeout=120)
    response.raise_for_status()
    text = response.content.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    rows = list(reader)
    by_pitch = defaultdict(list)
    missing_by_pitch = defaultdict(int)
    total_by_pitch = defaultdict(int)
    sample_rows = []
    for row in rows:
        pitch_type = row.get("pitch_type") or ""
        axis = parse_float(row.get("spin_axis"))
        if pitch_type:
            total_by_pitch[pitch_type] += 1
            if axis is None:
                missing_by_pitch[pitch_type] += 1
            else:
                by_pitch[pitch_type].append(axis)
        if len(sample_rows) < 6 and pitch_type:
            sample_rows.append({
                "game_date": row.get("game_date"),
                "game_type": row.get("game_type"),
                "game_pk": row.get("game_pk"),
                "at_bat_number": row.get("at_bat_number"),
                "pitch_number": row.get("pitch_number"),
                "pitch_type": pitch_type,
                "release_speed": row.get("release_speed"),
                "release_spin_rate": row.get("release_spin_rate"),
                "spin_axis": row.get("spin_axis"),
            })
    return {
        "status": response.status_code,
        "content_type": response.headers.get("content-type"),
        "bytes": len(response.content),
        "row_count": len(rows),
        "columns_prefix": (reader.fieldnames or [])[:12],
        "game_type_counts": dict(sorted({
            game_type: sum(1 for row in rows if row.get("game_type") == game_type)
            for game_type in {row.get("game_type") for row in rows if row.get("game_type")}
        }.items())),
        "pitch_types": {
            pitch_type: {
                "total_n": total_by_pitch[pitch_type],
                "spin_axis_n": len(values),
                "missing_spin_axis_n": missing_by_pitch[pitch_type],
                "circular_mean_spin_axis": circular_mean_degrees(values),
                "arithmetic_mean_spin_axis": sum(values) / len(values) if values else None,
                "min": min(values) if values else None,
                "max": max(values) if values else None,
            }
            for pitch_type, values in sorted(by_pitch.items())
        },
        "sample_rows": sample_rows,
    }


def spin_aggregate_probe(session: requests.Session) -> dict:
    response = session.get(SPIN_API_URL, timeout=60)
    response.raise_for_status()
    payload = response.json()
    rows = payload if isinstance(payload, list) else payload.get("data", payload.get("rows", [])) if isinstance(payload, dict) else []
    selected = []
    for row in rows:
        if not isinstance(row, dict) or int(row.get("season") or YEAR) != YEAR:
            continue
        selected.append({
            "api_pitch_type": row.get("api_pitch_type"),
            "n_pitches": row.get("n_pitches"),
            "hawkeye_measured": parse_float(row.get("hawkeye_measured")),
            "movement_inferred": parse_float(row.get("movement_inferred")),
            "hawkeye_measured_clock_label": row.get("hawkeye_measured_clock_label"),
            "movement_inferred_clock_label": row.get("movement_inferred_clock_label"),
        })
    return {
        "status": response.status_code,
        "content_type": response.headers.get("content-type"),
        "bytes": len(response.content),
        "rows": selected,
    }


def comparison(csv_data: dict, spin_data: dict) -> list[dict]:
    by_type = {row["api_pitch_type"]: row for row in spin_data["rows"] if row.get("api_pitch_type")}
    out = []
    for pitch_type, metrics in csv_data["pitch_types"].items():
        aggregate = by_type.get(pitch_type)
        if not aggregate:
            continue
        mean_axis = metrics["circular_mean_spin_axis"]
        out.append({
            "pitch_type": pitch_type,
            "csv_total_n": metrics["total_n"],
            "csv_spin_axis_n": metrics["spin_axis_n"],
            "csv_missing_spin_axis_n": metrics["missing_spin_axis_n"],
            "leaderboard_n_pitches": aggregate.get("n_pitches"),
            "csv_circular_mean_spin_axis": mean_axis,
            "csv_arithmetic_mean_spin_axis": metrics["arithmetic_mean_spin_axis"],
            "leaderboard_hawkeye_measured": aggregate.get("hawkeye_measured"),
            "difference_deg": circular_difference(mean_axis, aggregate.get("hawkeye_measured")),
            "leaderboard_movement_inferred": aggregate.get("movement_inferred"),
            "difference_vs_inferred_deg": circular_difference(mean_axis, aggregate.get("movement_inferred")),
            "measured_clock": aggregate.get("hawkeye_measured_clock_label"),
            "inferred_clock": aggregate.get("movement_inferred_clock_label"),
        })
    return out


def test_deep_spin_orientation_probe():
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 (compatible; one-off-research/21.0)"})
    csv_data = csv_probe(session)
    spin_data = spin_aggregate_probe(session)
    report = {
        "statcast_csv": csv_data,
        "spin_direction_aggregate": spin_data,
        "per_pitch_spin_axis_vs_hawkeye_measured": comparison(csv_data, spin_data),
    }
    pytest.fail("\n===== REGULAR-SEASON SPIN_AXIS VS HAWKEYE_MEASURED REPORT =====\n" + json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
