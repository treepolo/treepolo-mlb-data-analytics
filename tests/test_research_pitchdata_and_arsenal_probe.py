from __future__ import annotations

import html as html_lib
import json
import re
from urllib.parse import urljoin

import pytest
import requests

pytestmark = pytest.mark.integration
BASE = "https://baseballsavant.mlb.com"
OHTANI = 660271
FIELDS = (
    "image_spin_x",
    "image_spin_y",
    "image_spin_z",
    "image_orientation_angle",
    "hawkeye_measured",
    "movement_inferred",
    "alan_active_spin_pct",
    "active_spin",
)
PATH_TERMS = (
    "spin", "pitch", "hawk", "orientation", "seam", "leaderboard",
    "player-service", "statcast", "csv", "download",
)
SPIN_DIRECTION_API = "/savant/api/v1/spin-direction-by-pitcher"


def compact(text: str, limit: int = 1400) -> str:
    value = re.sub(r"\s+", " ", text).strip()
    if len(value) <= limit:
        return value
    half = max(1, (limit - 5) // 2)
    return value[:half] + " ... " + value[-half:]


def context(text: str, needle: str, radius: int = 900, limit: int = 1800) -> str | None:
    index = text.lower().find(needle.lower())
    if index < 0:
        return None
    return compact(text[max(0, index - radius): index + len(needle) + radius], limit)


def asset_urls(page: str, base_url: str) -> list[str]:
    values: list[str] = []
    for raw in re.findall(r'''(?:src|href)=[\"']([^\"']+)[\"']''', page, flags=re.I):
        raw = html_lib.unescape(raw)
        if raw.startswith("javascript:") or raw.startswith("#"):
            continue
        url = urljoin(base_url, raw)
        if url not in values:
            values.append(url)
    return values


def script_urls(page: str, base_url: str) -> list[str]:
    values: list[str] = []
    for raw in re.findall(r'''<script[^>]+src=[\"']([^\"']+)[\"']''', page, flags=re.I):
        url = urljoin(base_url, html_lib.unescape(raw))
        if url not in values:
            values.append(url)
    return values


def path_literals(text: str, *, limit: int = 80) -> list[str]:
    found: list[str] = []
    for raw in re.findall(r'''[\"'`]((?:/|https?://)[^\"'`\s]{2,260})[\"'`]''', text):
        value = html_lib.unescape(raw)
        low = value.lower()
        if any(term in low for term in PATH_TERMS) and value not in found:
            found.append(value)
        if len(found) >= limit:
            break
    return found


def nearby_paths(text: str, needle: str, radius: int = 9000) -> list[str]:
    index = text.lower().find(needle.lower())
    if index < 0:
        return []
    return path_literals(text[max(0, index - radius): index + len(needle) + radius], limit=50)


def nearest_array_owner(text: str, needle: str) -> dict | None:
    index = text.lower().find(needle.lower())
    if index < 0:
        return None
    start = max(0, index - 60000)
    prefix = text[start:index]
    patterns = (
        r'''([A-Za-z_$][\w$]*)\s*:\s*\[\s*\{''',
        r'''[\"']([^\"']+)[\"']\s*:\s*\[\s*\{''',
        r'''(?:var|let|const)\s+([A-Za-z_$][\w$]*)\s*=\s*\[\s*\{''',
    )
    best = None
    for pattern in patterns:
        for match in re.finditer(pattern, prefix):
            absolute = start + match.start()
            if best is None or absolute > best[0]:
                best = (absolute, match.group(1))
    if best is None:
        return None
    absolute, owner = best
    return {
        "owner": owner,
        "distance_to_field": index - absolute,
        "declaration_context": compact(text[max(0, absolute - 500): absolute + 500], 1000),
    }


def fetch_like_snippets(text: str, *, limit: int = 30) -> list[str]:
    snippets: list[str] = []
    patterns = (
        r'''fetch\s*\(''',
        r'''axios\s*\.\s*(?:get|post|request)\s*\(''',
        r'''\$\s*\.\s*(?:ajax|get|getJSON|post)\s*\(''',
        r'''XMLHttpRequest''',
    )
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.I):
            snippet = compact(text[max(0, match.start() - 220): match.start() + 700], 900)
            low = snippet.lower()
            if any(term in low for term in PATH_TERMS) and snippet not in snippets:
                snippets.append(snippet)
            if len(snippets) >= limit:
                return snippets
    return snippets


def summarize_page(page: str, page_url: str) -> dict:
    return {
        "url": page_url,
        "bytes": len(page.encode("utf-8")),
        "field_counts": {field: page.count(field) for field in FIELDS},
        "spinAxis_count": page.count("spinAxis"),
        "spin_axis_owner": nearest_array_owner(page, "image_spin_x"),
        "spin_axis_context": context(page, "spinAxis", radius=1100, limit=2200),
        "download_csv_context": context(page, "Download CSV", radius=800, limit=1600),
        "script_urls": script_urls(page, page_url),
        "interesting_paths": path_literals(page, limit=70),
    }


def summarize_script(session: requests.Session, url: str) -> dict:
    response = session.get(url, timeout=60)
    item = {"url": url, "status": response.status_code, "bytes": len(response.content)}
    if not response.ok or len(response.content) > 12_000_000:
        return item
    text = response.text
    item.update({
        "field_counts": {field: text.count(field) for field in FIELDS},
        "spinAxis_count": text.count("spinAxis"),
        "interesting_paths": path_literals(text, limit=80),
        "near_image_spin_paths": nearby_paths(text, "image_spin_x"),
        "near_orientation_paths": nearby_paths(text, "image_orientation_angle"),
        "fetch_like": fetch_like_snippets(text),
        "spin_direction_api_context": context(text, SPIN_DIRECTION_API, radius=3000, limit=6000),
        "btn_csv_context": context(text, "btnCSV", radius=2200, limit=4400),
        "leaderboard_data_context": context(text, "leaderboardData", radius=1800, limit=3600),
        "renderer_context": (
            context(text, "spinAxisPoint", radius=1600, limit=3200)
            or context(text, "image_spin_x", radius=1600, limit=3200)
        ),
    })
    return item


def response_shape(response: requests.Response) -> dict:
    item = {
        "url": response.url,
        "status": response.status_code,
        "content_type": response.headers.get("content-type"),
        "bytes": len(response.content),
    }
    try:
        body = response.json()
    except Exception:
        item["text_head"] = compact(response.text[:5000], 2500)
        return item
    item["json_type"] = type(body).__name__
    if isinstance(body, dict):
        item["top_keys"] = list(body)[:80]
        rows = None
        for key, value in body.items():
            if isinstance(value, list) and value and isinstance(value[0], dict):
                rows = value
                item["row_container"] = key
                break
        if rows is None and body and all(not isinstance(v, (dict, list)) for v in body.values()):
            rows = [body]
    elif isinstance(body, list):
        rows = body
        item["row_container"] = "$root"
    else:
        rows = None
    if rows:
        item["row_count"] = len(rows)
        item["first_row_keys"] = list(rows[0])[:120]
        item["first_row"] = rows[0]
    return item


def test_deep_spin_orientation_probe():
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (compatible; one-off-research/3.0)",
        "Referer": BASE + "/",
    })
    report: dict = {}

    pitch_url = f"{BASE}/app/pitch-data/{OHTANI}"
    first = session.get(pitch_url, timeout=60)
    first.raise_for_status()
    last_modified = first.headers.get("last-modified")
    conditional = session.get(
        pitch_url,
        headers={"If-Modified-Since": last_modified} if last_modified else {},
        timeout=60,
    )
    report["pitch_data_refresh"] = {
        "url": pitch_url,
        "status": first.status_code,
        "bytes": len(first.content),
        "last_modified": last_modified,
        "cache_control": first.headers.get("cache-control"),
        "conditional_status": conditional.status_code,
        "conditional_bytes": len(conditional.content),
    }

    player_url = f"{BASE}/savant-player/shohei-ohtani-{OHTANI}?playerType=pitcher"
    player_response = session.get(player_url, timeout=60)
    player_response.raise_for_status()
    player_html = player_response.text
    report["player_page"] = summarize_page(player_html, player_url)
    report["player_scripts"] = [
        summarize_script(session, url)
        for url in report["player_page"]["script_urls"]
        if "mlbstatic.com" in url.lower() or "baseballsavant.mlb.com" in url.lower()
    ]

    leaderboard_url = (
        f"{BASE}/leaderboard/spin-direction-pitches"
        "?year=2026&pitch_type=FF&playerName=Shohei%20Ohtani&min=0"
    )
    leaderboard_response = session.get(leaderboard_url, timeout=60)
    leaderboard_response.raise_for_status()
    leaderboard_html = leaderboard_response.text
    report["spin_direction_page"] = summarize_page(leaderboard_html, leaderboard_url)
    report["spin_direction_scripts"] = [
        summarize_script(session, url)
        for url in report["spin_direction_page"]["script_urls"]
        if "mlbstatic.com" in url.lower() or "baseballsavant.mlb.com" in url.lower()
    ]

    report["other_pages"] = []
    for url in (
        f"{BASE}/leaderboard/spin-direction-comparison?year=2026&team=&min=0",
        f"{BASE}/leaderboard/active-spin?year=2026&team=&min=0",
    ):
        response = session.get(url, timeout=60)
        item = {"url": url, "status": response.status_code, "bytes": len(response.content)}
        if response.ok:
            summary = summarize_page(response.text, url)
            item.update({
                "field_counts": summary["field_counts"],
                "spinAxis_count": summary["spinAxis_count"],
                "spin_axis_owner": summary["spin_axis_owner"],
                "download_csv_context": summary["download_csv_context"],
                "interesting_paths": summary["interesting_paths"],
                "script_urls": summary["script_urls"],
            })
        report["other_pages"].append(item)

    # Deliberately try conservative query shapes that are consistent with the leaderboard's visible filters.
    api_url = BASE + SPIN_DIRECTION_API
    report["spin_direction_api_trials"] = []
    trials = (
        {"player_id": OHTANI, "year": 2026, "pitch_type": "FF"},
        {"playerId": OHTANI, "year": 2026, "pitch_type": "FF"},
        {"pitcher": OHTANI, "year": 2026, "pitch_type": "FF"},
        {"player_id": OHTANI, "season": 2026, "pitch_type": "FF"},
    )
    for params in trials:
        response = session.get(api_url, params=params, timeout=60)
        item = {"params": params}
        item.update(response_shape(response))
        report["spin_direction_api_trials"].append(item)

    rendered = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2)
    assert len(rendered) < 160_000, f"compact research report unexpectedly grew to {len(rendered)} bytes"
    pytest.fail("\n===== COMPACT SPIN ORIENTATION RESEARCH REPORT =====\n" + rendered)
