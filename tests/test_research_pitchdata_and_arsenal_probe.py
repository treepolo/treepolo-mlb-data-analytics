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
    # Preserve template literals too; ${...} is useful evidence about route construction.
    for _, value in re.findall(r'''([\"'`])((?:(?!\1).){2,420})\1''', text, flags=re.S):
        value = html_lib.unescape(value).strip()
        if value not in out:
            out.append(value)
    return out


def relevant_routes(text: str) -> list[str]:
    out = []
    for value in quoted_literals(text):
        low = value.lower()
        pathish = value.startswith("/") or value.startswith("http")
        if not pathish:
            continue
        if value.startswith(ROUTE_PREFIXES) or any(term in low for term in ROUTE_TERMS):
            if value not in out:
                out.append(value)
    return sorted(out)


def explicit_api_routes(text: str) -> list[str]:
    return sorted({
        value for value in quoted_literals(text)
        if value.startswith(ROUTE_PREFIXES)
    })


def source_hints(text: str) -> list[str]:
    return sorted(set(re.findall(r"/usr/local/app/[^`\"'\s]{2,260}", text)))[:200]


def contexts(text: str, needle: str, radius: int = 900, max_hits: int = 3) -> list[str]:
    low = text.lower()
    target = needle.lower()
    start = 0
    out = []
    while len(out) < max_hits:
        index = low.find(target, start)
        if index < 0:
            break
        fragment = re.sub(r"\s+", " ", text[max(0, index - radius): index + len(needle) + radius]).strip()
        out.append(fragment[:2200])
        start = index + len(target)
    return out


def script_inventory(url: str, text: str) -> dict | None:
    routes = relevant_routes(text)
    api = explicit_api_routes(text)
    counts = {term: text.lower().count(term.lower()) for term in FIELD_TERMS}
    if not routes and not any(counts.values()):
        return None
    field_contexts = {
        term: contexts(text, term)
        for term, count in counts.items()
        if count and term.lower() in {
            "image_spin_x", "image_orientation_angle", "hawkeye_measured", "spinaxis", "play_id"
        }
    }
    return {
        "script": url,
        "bytes": len(text.encode("utf-8", errors="ignore")),
        "explicit_api_routes": api,
        "relevant_path_literals": routes,
        "field_counts": counts,
        "field_contexts": field_contexts,
        "source_hints": source_hints(text),
    }


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


def page_inventory(session: requests.Session, page_url: str) -> dict:
    page, scripts = load_page_scripts(session, page_url)
    inventories = []
    for url, text in scripts:
        item = script_inventory(url, text)
        if item:
            inventories.append(item)
    return {
        "url": page.url,
        "status": page.status_code,
        "bytes": len(page.content),
        "inline_explicit_api_routes": explicit_api_routes(page.text),
        "inline_relevant_path_literals": relevant_routes(page.text),
        "script_count": len(scripts),
        "interesting_scripts": inventories,
    }


def test_deep_spin_orientation_probe():
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 (compatible; one-off-research/10.0)"})

    pid = first_video_pid(session)
    report = {
        "player_page": page_inventory(session, PLAYER_URL),
        "spin_direction_page": page_inventory(session, SPIN_URL),
        "sporty_video_page": page_inventory(session, f"{BASE}/sporty-videos?playId={pid}"),
    }

    # Flat union makes it obvious whether a raw-looking route exists anywhere in the loaded frontend.
    route_sources: dict[str, list[str]] = {}
    for page_name, page in report.items():
        for route in page["inline_explicit_api_routes"] + page["inline_relevant_path_literals"]:
            route_sources.setdefault(route, []).append(f"{page_name}:inline")
        for script in page["interesting_scripts"]:
            for route in script["explicit_api_routes"] + script["relevant_path_literals"]:
                route_sources.setdefault(route, []).append(script["script"])
    report["route_union"] = [
        {"route": route, "sources": sorted(set(sources))}
        for route, sources in sorted(route_sources.items())
    ]

    rendered = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2)
    assert len(rendered) < 220_000, f"endpoint inventory unexpectedly grew to {len(rendered)} bytes"
    pytest.fail("\n===== SAVANT FRONTEND ENDPOINT INVENTORY =====\n" + rendered)
