from __future__ import annotations

import csv
import html as html_lib
import io
import json
import re
from urllib.parse import urljoin

import pytest
import requests

pytestmark = pytest.mark.integration
BASE = "https://baseballsavant.mlb.com"
OHTANI = 660271
PLAYER_URL = f"{BASE}/savant-player/shohei-ohtani-{OHTANI}?playerType=pitcher"
SPIN_PAGE_URL = (
    f"{BASE}/leaderboard/spin-direction-pitches"
    "?year=2026&pitch_type=FF&playerName=Shohei%20Ohtani&min=0"
)
SPIN_API = "/savant/api/v1/spin-direction-by-pitcher"
CANDIDATE_SERVICES = (
    "/player-services/pitches-seasonal",
    "/player-services/statcast-pitches-breakdown",
    "/player-services/roll",
    "/player-services/gamelogs",
    "/player-services/range",
    "/player-services/histogram",
)
ORIENTATION_FIELDS = (
    "image_spin_x", "image_spin_y", "image_spin_z", "image_orientation_angle",
    "hawkeye_measured", "movement_inferred", "alan_active_spin_pct", "active_spin",
)


def compact(text: str, limit: int = 1800) -> str:
    value = re.sub(r"\s+", " ", text).strip()
    if len(value) <= limit:
        return value
    half = max(1, (limit - 5) // 2)
    return value[:half] + " ... " + value[-half:]


def context(text: str, needle: str, radius: int = 2200, limit: int = 4400) -> str | None:
    index = text.find(needle)
    if index < 0:
        return None
    return compact(text[max(0, index - radius): index + len(needle) + radius], limit)


def script_urls(page: str, base_url: str) -> list[str]:
    out: list[str] = []
    for raw in re.findall(r'''<script[^>]+src=[\"']([^\"']+)[\"']''', page, flags=re.I):
        url = urljoin(base_url, html_lib.unescape(raw))
        if url not in out:
            out.append(url)
    return out


def endpoint_literals(text: str) -> list[str]:
    patterns = (
        r'''[\"'`](/savant/api/v1/[^\"'`?\s${}]+)''',
        r'''[\"'`](/player-services/[^\"'`?\s${}]+)''',
        r'''[\"'`](/app/[^\"'`?\s${}]+)''',
    )
    out: list[str] = []
    for pattern in patterns:
        for value in re.findall(pattern, text):
            if value not in out:
                out.append(value)
    return sorted(out)


def response_shape(response: requests.Response, *, include_first: bool = True) -> dict:
    out = {
        "url": response.url,
        "status": response.status_code,
        "content_type": response.headers.get("content-type"),
        "bytes": len(response.content),
    }
    try:
        body = response.json()
    except Exception:
        out["text_head"] = compact(response.text[:7000], 3000)
        return out
    out["json_type"] = type(body).__name__
    rows = None
    if isinstance(body, list):
        rows = body
        out["row_container"] = "$root"
    elif isinstance(body, dict):
        out["top_keys"] = list(body)[:100]
        for key, value in body.items():
            if isinstance(value, list) and value and isinstance(value[0], dict):
                rows = value
                out["row_container"] = key
                break
    if rows:
        out["row_count"] = len(rows)
        out["first_row_keys"] = list(rows[0])[:160]
        if include_first:
            out["first_row"] = rows[0]
    return out


def load_scripts(session: requests.Session, page: str, page_url: str) -> list[tuple[str, str]]:
    loaded: list[tuple[str, str]] = []
    for url in script_urls(page, page_url):
        if "mlbstatic.com" not in url.lower() and "baseballsavant.mlb.com" not in url.lower():
            continue
        response = session.get(url, timeout=60)
        if response.ok and len(response.content) <= 12_000_000:
            loaded.append((url, response.text))
    return loaded


def service_contexts(scripts: list[tuple[str, str]]) -> dict:
    out: dict[str, list[dict]] = {}
    for service in CANDIDATE_SERVICES:
        hits = []
        for url, text in scripts:
            if service not in text:
                continue
            hits.append({
                "script": url,
                "context": context(text, service, radius=3500, limit=7000),
            })
        out[service] = hits
    return out


def orientation_summary(page: str) -> dict:
    return {
        "field_counts": {field: page.count(field) for field in ORIENTATION_FIELDS},
        "spinAxis_count": page.count("spinAxis"),
        "leaderboardData_count": page.count("leaderboardData"),
    }


def csv_shape(response: requests.Response) -> dict:
    out = {
        "url": response.url,
        "status": response.status_code,
        "content_type": response.headers.get("content-type"),
        "bytes": len(response.content),
    }
    text = response.text
    reader = csv.reader(io.StringIO(text))
    rows = []
    for index, row in enumerate(reader):
        rows.append(row)
        if index >= 2:
            break
    out["header"] = rows[0] if rows else []
    out["first_data_row"] = rows[1] if len(rows) > 1 else []
    out["line_count"] = text.count("\n")
    return out


def test_deep_spin_orientation_probe():
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (compatible; one-off-research/4.0)",
        "Referer": BASE + "/",
    })
    report: dict = {}

    player_response = session.get(PLAYER_URL, timeout=60)
    player_response.raise_for_status()
    player_html = player_response.text
    player_scripts = load_scripts(session, player_html, PLAYER_URL)

    spin_response = session.get(SPIN_PAGE_URL, timeout=60)
    spin_response.raise_for_status()
    spin_html = spin_response.text
    spin_scripts = load_scripts(session, spin_html, SPIN_PAGE_URL)

    report["confirmed_aggregate_surfaces"] = {
        "player_page": orientation_summary(player_html),
        "spin_page": orientation_summary(spin_html),
    }

    report["player_script_endpoints"] = [
        {"script": url, "endpoints": endpoint_literals(text)}
        for url, text in player_scripts
        if endpoint_literals(text)
    ]
    report["spin_script_endpoints"] = [
        {"script": url, "endpoints": endpoint_literals(text)}
        for url, text in spin_scripts
        if endpoint_literals(text)
    ]
    report["candidate_service_contexts"] = service_contexts(player_scripts)

    # Confirm that the leaderboard CSV surface is the same aggregate family.
    csv_response = session.get(SPIN_PAGE_URL + "&csv=true", timeout=60)
    report["spin_page_csv"] = csv_shape(csv_response)

    # Keep the exact known expand-row API call as a control.
    spin_api_response = session.get(
        BASE + SPIN_API,
        params={"pitcher": OHTANI, "year": 2026, "pov": "Pit"},
        timeout=60,
    )
    report["spin_direction_api_control"] = response_shape(spin_api_response, include_first=True)

    # Search source contexts around every orientation-related field in the player bundle.
    report["orientation_code_contexts"] = []
    for url, text in player_scripts:
        contexts = {}
        for field in ("image_spin_x", "image_orientation_angle", "hawkeye_measured", "spinAxisPoint"):
            hit = context(text, field, radius=2600, limit=5200)
            if hit:
                contexts[field] = hit
        if contexts:
            report["orientation_code_contexts"].append({"script": url, "contexts": contexts})

    rendered = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2)
    assert len(rendered) < 150_000, f"research report unexpectedly grew to {len(rendered)} bytes"
    pytest.fail("\n===== SAVANT RAW ORIENTATION UPSTREAM TRACE =====\n" + rendered)
