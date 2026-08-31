from __future__ import annotations

import json
import re
from pathlib import PurePosixPath
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
CALL_TERMS = ("fetch(", "$.ajax", "$.get", "axios", "d3.json", "XMLHttpRequest", "/player-services/", "/savant/api/")
ENDPOINT_RE = re.compile(
    r"[\"'](\/+(?:player-services|savant\/api|leaderboard|api|app)\/[^\"'\\\s<>]{1,220})[\"']"
)
SCRIPT_RE = re.compile(r"<script[^>]+src=[\"']([^\"']+)[\"']", re.I)


def compact(text: str, limit: int = 1100) -> str:
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
    return sorted(set(ENDPOINT_RE.findall(text)))[:160]


def counts(text: str, terms=HIGH_VALUE_TERMS) -> dict:
    lower = text.lower()
    return {term: lower.count(term.lower()) for term in terms if lower.count(term.lower())}


def snippets_for_terms(text: str, terms, max_total: int = 24, max_per_term: int = 3) -> list[dict]:
    lower = text.lower()
    out = []
    seen = set()
    for term in terms:
        needle = term.lower()
        start = 0
        found = 0
        while len(out) < max_total and found < max_per_term:
            index = lower.find(needle, start)
            if index < 0:
                break
            snippet = compact(text[max(0, index - 430): index + len(term) + 760])
            if snippet and snippet not in seen:
                seen.add(snippet)
                out.append({"term": term, "snippet": snippet})
                found += 1
            start = index + max(1, len(needle))
        if len(out) >= max_total:
            break
    return out


def fetch_text(session: requests.Session, url: str) -> tuple[requests.Response, str]:
    response = session.get(url, timeout=60)
    return response, response.text


def bundle_summary(session: requests.Session, url: str) -> dict:
    response, text = fetch_text(session, url)
    term_counts = counts(text)
    endpoints = extract_endpoints(text)
    return {
        "name": PurePosixPath(urlparse(url).path).name,
        "url": url,
        "status": response.status_code,
        "bytes": len(response.content),
        "term_counts": term_counts,
        "endpoint_count": len(endpoints),
        "score": sum(term_counts.values()) + len(endpoints) * 3,
    }


def deep_bundle_probe(session: requests.Session, url: str) -> dict:
    response, text = fetch_text(session, url)
    return {
        "name": PurePosixPath(urlparse(url).path).name,
        "url": url,
        "status": response.status_code,
        "content_type": response.headers.get("content-type"),
        "bytes": len(response.content),
        "term_counts": counts(text),
        "call_counts": counts(text, CALL_TERMS),
        "endpoints": extract_endpoints(text),
        "high_value_snippets": snippets_for_terms(text, HIGH_VALUE_TERMS, max_total=24, max_per_term=2),
        "call_snippets": snippets_for_terms(text, CALL_TERMS, max_total=16, max_per_term=2),
    }


def page_probe(session: requests.Session, url: str, kind: str) -> dict:
    response, text = fetch_text(session, url)
    return {
        "kind": kind,
        "url": url,
        "status": response.status_code,
        "bytes": len(response.content),
        "term_counts": counts(text),
        "endpoints": extract_endpoints(text),
    }


def test_deep_spin_orientation_probe():
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 (compatible; one-off-research/18.0)"})

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

    summaries = [bundle_summary(session, url) for url in script_urls[:60]]
    ranked = sorted((item for item in summaries if item["score"]), key=lambda item: (-item["score"], item["name"]))

    preferred_urls = []
    for url in script_urls:
        name = PurePosixPath(urlparse(url).path).name.lower()
        if "spin-axis-pitches" in name:
            preferred_urls.append(url)
    for item in ranked:
        if item["url"] not in preferred_urls and len(preferred_urls) < 5:
            preferred_urls.append(item["url"])

    report = {
        "script_url_count": len(script_urls),
        "ranked_bundle_summaries": ranked[:15],
        "deep_bundle_probes": [deep_bundle_probe(session, url) for url in preferred_urls[:5]],
        "page_summaries": [
            page_probe(session, PLAYER_URL, "player_page"),
            page_probe(session, SPIN_URL, "spin_direction_page"),
            page_probe(session, BREAKDOWN_URL, "pitch_breakdown_fragment"),
        ],
    }
    rendered = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2)
    assert len(rendered) < 80_000, f"targeted bundle report unexpectedly grew to {len(rendered)} bytes"
    pytest.fail("\n===== TARGETED SAVANT JS BUNDLE REPORT =====\n" + rendered)
