from __future__ import annotations

import json
import re
from collections import Counter

import pytest
import requests

pytestmark = pytest.mark.integration
BASE = "https://baseballsavant.mlb.com"
OHTANI = 660271
YEAR = 2026
PLAYER_URL = f"{BASE}/savant-player/shohei-ohtani-{OHTANI}?playerType=pitcher"
PITCHES_URL = f"{BASE}/player-services/pitches-seasonal?playerId={OHTANI}&season={YEAR}"
SPIN_BY_PITCHER_URL = f"{BASE}/savant/api/v1/spin-direction-by-pitcher?pitcher={OHTANI}&year={YEAR}&pov=Pit"
PLAYER_JS_URL = "https://builds.mlbstatic.com/baseballsavant.mlb.com/v1/sections/player-update/builds/365eecaecb2cdd235bf4378010b37fef2f181f45/scripts/build/index.js"
TARGET_FIELDS = (
    "play_id", "playId", "pid", "pitch_id", "game_pk", "gamePk", "game_date",
    "pitch_number", "api_pitch_type", "pitch_type", "release_speed", "release_spin_rate",
    "spin_axis", "spinAxis", "rn_clock", "infer_n_pitches", "meas_n_pitches",
    "hawkeye_measured", "movement_inferred", "image_orientation_angle",
    "image_spin_x", "image_spin_y", "image_spin_z",
)
SPIN_FIELDS = (
    "hawkeye_measured", "movement_inferred", "image_orientation_angle",
    "image_spin_x", "image_spin_y", "image_spin_z", "spin_axis", "spinAxis",
)
ID_FIELDS = ("play_id", "playId", "pid", "pitch_id", "game_pk", "gamePk", "pitch_number")


def compact(value, limit: int = 1500) -> str:
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return re.sub(r"\s+", " ", value).strip()[:limit]


def response_meta(response: requests.Response) -> dict:
    return {
        "status": response.status_code,
        "content_type": response.headers.get("content-type"),
        "bytes": len(response.content),
        "url": response.url,
    }


def json_shape(value, depth: int = 0):
    if depth >= 4:
        return type(value).__name__
    if isinstance(value, dict):
        keys = sorted(value)
        return {key: json_shape(value[key], depth + 1) for key in keys[:40]}
    if isinstance(value, list):
        return {
            "type": "list",
            "length": len(value),
            "sample": json_shape(value[0], depth + 1) if value else None,
        }
    return type(value).__name__


def flatten_dict_rows(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from flatten_dict_rows(child)
    elif isinstance(value, list):
        for child in value:
            yield from flatten_dict_rows(child)


def target_counts(value) -> dict:
    counter = Counter()
    for row in flatten_dict_rows(value):
        for field in TARGET_FIELDS:
            if field in row:
                counter[field] += 1
    return dict(sorted(counter.items()))


def interesting_rows(value, limit: int = 5) -> list[dict]:
    rows = []
    seen = set()
    for row in flatten_dict_rows(value):
        hits = [field for field in TARGET_FIELDS if field in row]
        if not hits:
            continue
        key = tuple(sorted(row.keys()))
        if key in seen:
            continue
        seen.add(key)
        rows.append({
            "keys": sorted(row.keys()),
            "targets": {field: row.get(field) for field in TARGET_FIELDS if field in row},
        })
        if len(rows) >= limit:
            break
    return rows


def probe_json(session: requests.Session, url: str) -> dict:
    response = session.get(url, timeout=60)
    result = response_meta(response)
    try:
        payload = response.json()
    except Exception as exc:
        result["json_error"] = repr(exc)
        result["text_prefix"] = compact(response.text, 1800)
        return result
    result.update({
        "shape": json_shape(payload),
        "target_field_object_counts": target_counts(payload),
        "interesting_rows": interesting_rows(payload),
    })
    return result


def pitch_rows(payload) -> list[dict]:
    pitches = payload.get("pitches") if isinstance(payload, dict) else None
    rows = []
    if isinstance(pitches, dict):
        for group_name, group in pitches.items():
            if isinstance(group, list):
                for row in group:
                    if isinstance(row, dict):
                        copy = dict(row)
                        copy["__group"] = group_name
                        rows.append(copy)
    elif isinstance(pitches, list):
        rows = [row for row in pitches if isinstance(row, dict)]
    return rows


def probe_pitches_seasonal(session: requests.Session) -> dict:
    response = session.get(PITCHES_URL, timeout=60)
    result = response_meta(response)
    payload = response.json()
    rows = pitch_rows(payload)
    key_counts = Counter()
    for row in rows:
        key_counts.update(row.keys())
    both_id_and_spin = []
    spin_rows = []
    for row in rows:
        has_id = any(field in row and row.get(field) not in (None, "") for field in ID_FIELDS)
        has_spin = any(field in row and row.get(field) not in (None, "") for field in SPIN_FIELDS)
        if has_spin and len(spin_rows) < 5:
            spin_rows.append({key: row.get(key) for key in sorted(set(ID_FIELDS + SPIN_FIELDS + ("api_pitch_type", "pitch_type", "release_speed"))) if key in row})
        if has_id and has_spin and len(both_id_and_spin) < 5:
            both_id_and_spin.append({key: row.get(key) for key in sorted(set(ID_FIELDS + SPIN_FIELDS + ("api_pitch_type", "pitch_type", "release_speed"))) if key in row})
    return {
        **result,
        "top_level_keys": sorted(payload.keys()) if isinstance(payload, dict) else [],
        "pitch_row_count": len(rows),
        "pitch_row_union_keys": sorted(key_counts.keys()),
        "target_key_counts": {field: key_counts[field] for field in TARGET_FIELDS if key_counts[field]},
        "spin_row_count": sum(any(field in row and row.get(field) not in (None, "") for field in SPIN_FIELDS) for row in rows),
        "id_and_spin_row_count": sum(
            any(field in row and row.get(field) not in (None, "") for field in ID_FIELDS)
            and any(field in row and row.get(field) not in (None, "") for field in SPIN_FIELDS)
            for row in rows
        ),
        "spin_row_samples": spin_rows,
        "id_and_spin_samples": both_id_and_spin,
        "first_pitch_samples": [
            {key: row.get(key) for key in sorted(row.keys()) if key in TARGET_FIELDS or key in ("__group", "description", "events", "showVideo")}
            for row in rows[:3]
        ],
    }


def callsite_snippets(text: str, token: str, max_hits: int = 12) -> list[str]:
    snippets = []
    start = 0
    while len(snippets) < max_hits:
        index = text.find(token, start)
        if index < 0:
            break
        snippets.append(compact(text[max(0, index - 1200): index + 2200], 3300))
        start = index + len(token)
    return snippets


def page_cooccurrence(session: requests.Session) -> dict:
    response = session.get(PLAYER_URL, timeout=60)
    text = response.text
    lower = text.lower()
    windows = []
    both = 0
    start = 0
    while True:
        index = lower.find("image_spin_x", start)
        if index < 0:
            break
        chunk = text[max(0, index - 1800): index + 2200]
        has_id = any(field.lower() in chunk.lower() for field in ID_FIELDS)
        if has_id:
            both += 1
        if len(windows) < 8:
            windows.append({
                "has_pitch_identifier_nearby": has_id,
                "snippet": compact(chunk, 2600),
            })
        start = index + 12
    return {
        **response_meta(response),
        "image_spin_x_occurrences": lower.count("image_spin_x"),
        "play_id_occurrences": lower.count("play_id"),
        "spin_windows_with_any_pitch_identifier": both,
        "spin_windows": windows,
    }


def test_deep_spin_orientation_probe():
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 (compatible; one-off-research/19.0)"})

    js_response = session.get(PLAYER_JS_URL, timeout=60)
    js_response.raise_for_status()
    js_text = js_response.text

    report = {
        "spin_direction_by_pitcher": probe_json(session, SPIN_BY_PITCHER_URL),
        "pitches_seasonal": probe_pitches_seasonal(session),
        "player_page_spin_id_cooccurrence": page_cooccurrence(session),
        "player_js_trace": {
            **response_meta(js_response),
            "Wce_callsite_count": js_text.count("Wce("),
            "Wce_callsites": callsite_snippets(js_text, "Wce(", 10),
            "image_spin_x_callsites": callsite_snippets(js_text, "image_spin_x", 5),
            "pitches_seasonal_callsites": callsite_snippets(js_text, "pitches-seasonal", 4),
        },
    }
    rendered = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2)
    assert len(rendered) < 70_000, f"focused per-pitch spin report unexpectedly grew to {len(rendered)} bytes"
    pytest.fail("\n===== FOCUSED PER-PITCH SPIN SOURCE REPORT =====\n" + rendered)
