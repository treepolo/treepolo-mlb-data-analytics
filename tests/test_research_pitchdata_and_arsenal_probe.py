from __future__ import annotations

import csv
import io
import json
import re

import pytest
import requests

pytestmark = pytest.mark.integration
BASE = "https://baseballsavant.mlb.com"
OHTANI = 660271

CANDIDATE_PATHS = (
    "/player-services/spin-axis",
    "/player-services/spin-axis-pitcher",
    "/player-services/spin-direction",
    "/player-services/spin-direction-pitches",
    "/player-services/spin",
    "/savant/api/v1/spin-axis",
    "/savant/api/v1/spin-axis-by-pitcher",
    "/savant/api/v1/spin-direction-pitches",
)


def compact(text: str, limit: int = 2200) -> str:
    value = re.sub(r"\s+", " ", str(text)).strip()
    if len(value) <= limit:
        return value
    half = max(1, (limit - 5) // 2)
    return value[:half] + " ... " + value[-half:]


def response_probe(response: requests.Response) -> dict:
    out = {
        "url": response.url,
        "status": response.status_code,
        "content_type": response.headers.get("content-type"),
        "bytes": len(response.content),
    }
    if not response.ok:
        out["text_head"] = compact(response.text[:2500], 1200)
        return out
    try:
        body = response.json()
    except Exception:
        out["text_head"] = compact(response.text[:5000], 2200)
        return out
    out["json_type"] = type(body).__name__
    if isinstance(body, dict):
        out["top_keys"] = list(body)[:100]
        for key, value in body.items():
            if isinstance(value, list) and value:
                out["row_container"] = key
                out["row_count"] = len(value)
                out["first_row"] = value[0]
                break
    elif isinstance(body, list):
        out["row_count"] = len(body)
        out["first_row"] = body[0] if body else None
    return out


def csv_probe(response: requests.Response) -> dict:
    out = {
        "url": response.url,
        "status": response.status_code,
        "content_type": response.headers.get("content-type"),
        "bytes": len(response.content),
    }
    text = response.content.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    rows = list(reader)
    headers = list(reader.fieldnames or [])
    out["headers"] = headers
    out["row_count"] = len(rows)
    id_like = [
        field for field in headers
        if any(token in field.lower() for token in ("play", "game_pk", "at_bat", "pitch_number", "date", "uid"))
    ]
    out["per_pitch_identifier_fields"] = id_like
    ohtani_rows = [
        row for row in rows
        if str(row.get("player_id") or row.get("pitcher") or "").strip() == str(OHTANI)
        or "ohtani" in str(row.get("last_name, first_name") or row.get("name") or "").lower()
    ]
    out["ohtani_row_count"] = len(ohtani_rows)
    out["ohtani_examples"] = ohtani_rows[:10]
    out["first_row"] = rows[0] if rows else None
    return out


def test_deep_spin_orientation_probe():
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (compatible; one-off-research/8.0)",
        "Referer": BASE + "/leaderboard/spin-direction-pitches",
    })
    report = {}

    # Verify the export itself, rather than relying on a third-party label saying "per-pitch".
    csv_response = session.get(
        BASE + "/leaderboard/spin-direction-pitches",
        params={
            "year": 2026,
            "team": "",
            "min": 0,
            "pitch_type": "",
            "pov": "Pit",
            "csv": "true",
        },
        timeout=90,
    )
    csv_response.raise_for_status()
    report["spin_direction_csv"] = csv_probe(csv_response)

    # A second, narrowly filtered request checks whether filtering changes grain.
    ff_response = session.get(
        BASE + "/leaderboard/spin-direction-pitches",
        params={
            "year": 2026,
            "team": "",
            "min": 0,
            "pitch_type": "FF",
            "pov": "Pit",
            "csv": "true",
        },
        timeout=90,
    )
    ff_response.raise_for_status()
    report["spin_direction_ff_csv"] = csv_probe(ff_response)

    # The UI receives spinAxis server-rendered. Probe conservative route names derived from that serverVals key.
    trials = []
    parameter_sets = (
        {"playerId": OHTANI, "season": 2026},
        {"pitcher": OHTANI, "year": 2026, "pov": "Pit"},
    )
    for path in CANDIDATE_PATHS:
        for params in parameter_sets:
            response = session.get(BASE + path, params=params, timeout=45)
            item = {"path": path, "params": params}
            item.update(response_probe(response))
            trials.append(item)
    report["candidate_spin_services"] = trials

    rendered = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2)
    assert len(rendered) < 140_000, f"research report unexpectedly grew to {len(rendered)} bytes"
    pytest.fail("\n===== SAVANT SPIN CSV GRAIN AND SERVICE PROBE =====\n" + rendered)
