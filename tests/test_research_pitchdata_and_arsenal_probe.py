from __future__ import annotations

import json
import re
from urllib.parse import urljoin, urlparse

import pytest
import requests

pytestmark = pytest.mark.integration
BASE = "https://baseballsavant.mlb.com"
OHTANI = 660271
PLAYER_URL = f"{BASE}/savant-player/shohei-ohtani-{OHTANI}?playerType=pitcher"
SPIN_URL = f"{BASE}/leaderboard/spin-direction-pitches?year=2026&pitch_type=FF&min=0"
BREAKDOWN_URL = (
    f"{BASE}/player-services/statcast-pitches-breakdown"
    f"?playerId={OHTANI}&position=1&hand=&pitchBreakdown=pitches&timeFrame=yearly"
    "&season=&pitchType=&count=&gameType=&updatePitches=true"
)
HIGH_VALUE_TERMS = (
    "hawkeye_measured",
    "movement_inferred",
    "image_orientation_angle",
    "image_spin_x",
    "image_spin_y",
    "image_spin_z",
    "spin-direction-pitches",
    "spin-axis-by-pitcher",
    "statcast-pitches-breakdown",
    "pitches-seasonal",
    "play_id",
    "playId",
    "game_pk",
    "gamePk",
    "pitch_number",
    "spinAxis",
    "spin_axis",
    "orientation",
    "seam",
)
ENDPOINT_RE = re.compile(
    r"[\"'](\/+(?:player-services|savant\/api|leaderboard|api|app)\/[^\"'\\\s<>]{1,220})[\"']"
)
SCRIPT_RE = re.compile(r"<script[^>]+src=[\"']([^\"']+)[\"']", re.I)


def compact(text: str, limit: int = 900) -> str:
    return re.sub(r"\s+", " ", text).strip()[:limit]


def extract_script_urls(page_url: str, html: str) -> list[str]:
    urls = []
    seen = set()
    for src in SCRIPT_RE.findall(html):
        url = urljoin(page_url, src)
        if url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def extract_endpoints(text: str) -> list[str]:
    return sorted(set(ENDPOINT_RE.findall(text)))[:120]


def term_evidence(text: str, max_snippets: int = 28) -> dict:
    lower = text.lower()
    counts = {term: lower.count(term.lower()) for term in HIGH_VALUE_TERMS}
    snippets = []
    seen = set()
    for term in HIGH_VALUE_TERMS:
        needle = term.lower()
        start = 0
        while len(snippets) < max_snippets:
            index = lower.find(needle, start)
            if index < 0:
                break
            snippet = compact(text[max(0, index - 340): index + len(term) + 620])
            if snippet and snippet not in seen:
                seen.add(snippet)
                snippets.append({"term": term, "snippet": snippet})
            start = index + max(1, len(needle))
        if len(snippets) >= max_snippets:
            break
    return {"counts": counts, "snippets": snippets}


def probe_text(session: requests.Session, url: str, kind: str) -> dict:
    response = session.get(url, timeout=60)
    text = response.text
    evidence = term_evidence(text)
    return {
        "kind": kind,
        "url": url,
        "status": response.status_code,
        "content_type": response.headers.get("content-type"),
        "bytes": len(response.content),
        "endpoints": extract_endpoints(text),
        "term_counts": evidence["counts"],
        "term_snippets": evidence["snippets"],
    }


def probe_bundle(session: requests.Session, url: str) -> dict:
    response = session.get(url, timeout=60)
    content_type = response.headers.get("content-type") or ""
    text = response.text if ("javascript" in content_type or "text" in content_type or urlparse(url).path.endswith(".js")) else ""
    evidence = term_evidence(text, max_snippets=18) if text else {"counts": {}, "snippets": []}
    hit_count = sum(evidence["counts"].values())
    endpoints = extract_endpoints(text) if text else []
    return {
        "url": url,
        "status": response.status_code,
        "content_type": content_type,
        "bytes": len(response.content),
        "hit_count": hit_count,
        "endpoints": endpoints,
        "term_counts": {key: value for key, value in evidence["counts"].items() if value},
        "term_snippets": evidence["snippets"] if hit_count else [],
    }


def test_deep_spin_orientation_probe():
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 (compatible; one-off-research/17.0)"})

    player = session.get(PLAYER_URL, timeout=60)
    player.raise_for_status()
    spin = session.get(SPIN_URL, timeout=60)
    spin.raise_for_status()

    script_urls = []
    seen = set()
    for page_url, html in ((PLAYER_URL, player.text), (SPIN_URL, spin.text)):
        for url in extract_script_urls(page_url, html):
            if url not in seen:
                seen.add(url)
                script_urls.append(url)

    bundle_results = [probe_bundle(session, url) for url in script_urls[:50]]
    interesting_bundles = [item for item in bundle_results if item["hit_count"] or item["endpoints"]]

    report = {
        "pages": [
            probe_text(session, PLAYER_URL, "player_page_source"),
            probe_text(session, SPIN_URL, "spin_direction_page_source"),
            probe_text(session, BREAKDOWN_URL, "statcast_pitches_breakdown_fragment"),
        ],
        "script_url_count": len(script_urls),
        "script_urls": script_urls[:50],
        "interesting_bundles": interesting_bundles,
    }
    rendered = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2)
    assert len(rendered) < 90_000, f"source/bundle report unexpectedly grew to {len(rendered)} bytes"
    pytest.fail("\n===== SAVANT SOURCE + BUNDLE ENDPOINT REPORT =====\n" + rendered)
