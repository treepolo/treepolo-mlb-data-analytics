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
PLAYER_URL = f"{BASE}/savant-player/shohei-ohtani-{OHTANI}?playerType=pitcher"
SPIN_URL = f"{BASE}/leaderboard/spin-direction-pitches?year=2026&pitch_type=FF&min=0"
ROUTE_PREFIXES = ("/savant/api/", "/player-services/", "/app/", "/api/")
ROUTE_TERMS = (
    "pitch", "spin", "hawk", "orient", "seam", "track", "video", "play",
    "gameday", "statcast", "player", "evp", "movement",
)
FIELD_TERMS = (
    "image_spin_x", "image_spin_y", "image_spin_z", "image_orientation_angle",
    "hawkeye_measured", "movement_inferred", "spinAxis", "play_id", "pid",
)


def script_urls(page: str, base_url: str) -> list[str]:
    out: list[str] = []
    for raw in re.findall(r'''<script[^>]+src=[\"']([^\"']+)[\"']''', page, flags=re.I):
        url = urljoin(base_url, html_lib.unescape(raw))
        if url not in out:
            out.append(url)
    return out


def load_page_scripts(session: requests.Session, page_url: str) -> tuple[requests.Response, list[tuple[str, str]]]:
    page = session.get(page_url, timeout=60)
    page.raise_for_status()
    scripts: list[tuple[str, str]] = []
    for url in script_urls(page.text, page.url):
        if "mlbstatic.com" not in url.lower() and "baseballsavant.mlb.com" not in url.lower():
            continue
        response = session.get(url, timeout=90)
        if response.ok and len(response.content) <= 12_000_000:
            scripts.append((url, response.text))
    return page, scripts


def quoted_literals(text: str) -> list[str]:
    out = []
    for _, value in re.findall(r'''([\"'`])((?:(?!\1).){2,420})\1''', text, flags=re.S):
        value = html_lib.unescape(value).strip()
        if value not in out:
            out.append(value)
    return out


def relevant_routes(text: str) -> list[str]:
    out = []
    for value in quoted_literals(text):
        low = value.lower()
        if not (value.startswith("/") or value.startswith("http")):
            continue
        if value.startswith(ROUTE_PREFIXES) or any(term in low for term in ROUTE_TERMS):
            if value not in out:
                out.append(value)
    return sorted(out)


def field_counts(text: str) -> dict[str, int]:
    return {term: text.lower().count(term.lower()) for term in FIELD_TERMS}


def first_video_pid(session: requests.Session) -> str:
    response = session.get(
        BASE + "/player-services/pitches-seasonal",
        params={"playerId": OHTANI, "season": 2026},
        timeout=60,
    )
    response.raise_for_status()
    for rows in (response.json().get("pitches") or {}).values():
        for row in rows or []:
            if row.get("pid") and row.get("showVideo"):
                return str(row["pid"])
    raise AssertionError("no 2026 Ohtani video pitch found")


def inspect_page(session: requests.Session, name: str, page_url: str, route_sources: dict[str, set[str]]) -> dict:
    page, scripts = load_page_scripts(session, page_url)
    for route in relevant_routes(page.text):
        route_sources.setdefault(route, set()).add(f"{name}:inline")

    interesting_scripts = []
    for url, text in scripts:
        routes = relevant_routes(text)
        counts = field_counts(text)
        for route in routes:
            route_sources.setdefault(route, set()).add(url)
        if any(counts.values()):
            interesting_scripts.append({
                "script": url,
                "field_counts": {key: value for key, value in counts.items() if value},
                "routes": routes,
            })

    return {
        "url": page.url,
        "status": page.status_code,
        "bytes": len(page.content),
        "inline_field_counts": {key: value for key, value in field_counts(page.text).items() if value},
        "script_count": len(scripts),
        "field_hit_scripts": interesting_scripts,
    }


def test_deep_spin_orientation_probe():
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 (compatible; one-off-research/11.0)"})
    pid = first_video_pid(session)
    route_sources: dict[str, set[str]] = {}

    pages = {
        "player_page": inspect_page(session, "player_page", PLAYER_URL, route_sources),
        "spin_direction_page": inspect_page(session, "spin_direction_page", SPIN_URL, route_sources),
        "sporty_video_page": inspect_page(session, "sporty_video_page", f"{BASE}/sporty-videos?playId={pid}", route_sources),
    }

    route_union = [
        {"route": route, "sources": sorted(sources)}
        for route, sources in sorted(route_sources.items())
    ]
    raw_candidates = [
        item for item in route_union
        if any(term in item["route"].lower() for term in ("hawk", "orient", "seam", "track", "spin", "pitch"))
    ]
    report = {
        "pages": pages,
        "route_count": len(route_union),
        "raw_candidate_routes": raw_candidates,
        "route_union": route_union,
    }
    rendered = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2)
    assert len(rendered) < 130_000, f"compact endpoint inventory unexpectedly grew to {len(rendered)} bytes"
    pytest.fail("\n===== COMPACT SAVANT FRONTEND ENDPOINT INVENTORY =====\n" + rendered)
