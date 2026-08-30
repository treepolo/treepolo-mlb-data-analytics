from __future__ import annotations

import csv
import html as html_lib
import io
import json
import re
from collections import Counter
from urllib.parse import urljoin

import pytest
import requests

pytestmark = pytest.mark.integration
BASE = "https://baseballsavant.mlb.com"
OHTANI = 660271
PLAYER_URL = f"{BASE}/savant-player/shohei-ohtani-{OHTANI}?playerType=pitcher"
PITCH3D_URL = f"{BASE}/app/pitch-data/{OHTANI}"
TRACE_TERMS = (
    "pid", "playId", "play_id", "showVideo", "sporty-videos",
    "hawkeye", "orientation", "image_spin_x", "seam", "pitch-data",
)
PATH_TERMS = (
    "pitch", "play", "video", "hawk", "orient", "spin", "seam", "sporty",
    "tracking", "media", "evp", "savant", "player-service",
)


def compact(text: str, limit: int = 2600) -> str:
    value = re.sub(r"\s+", " ", text).strip()
    if len(value) <= limit:
        return value
    half = max(1, (limit - 5) // 2)
    return value[:half] + " ... " + value[-half:]


def context(text: str, needle: str, radius: int = 1800, limit: int = 3600) -> str | None:
    index = text.lower().find(needle.lower())
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


def endpoint_literals(text: str, limit: int = 120) -> list[str]:
    out: list[str] = []
    patterns = (
        r'''[\"'`]((?:https?://[^\"'`\s${}]+|/[^\"'`\s${}]+))[\"'`]''',
        r'''url\s*:\s*[\"'`]([^\"'`]+)[\"'`]''',
    )
    for pattern in patterns:
        for raw in re.findall(pattern, text, flags=re.I):
            value = html_lib.unescape(raw)
            low = value.lower()
            if any(term in low for term in PATH_TERMS) and value not in out:
                out.append(value)
                if len(out) >= limit:
                    return out
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


def trace_scripts(scripts: list[tuple[str, str]]) -> list[dict]:
    out = []
    for url, text in scripts:
        counts = {term: text.lower().count(term.lower()) for term in TRACE_TERMS}
        if not any(counts.values()):
            continue
        contexts = {}
        for term in ("showVideo", "sporty-videos", "playId", "pid", "hawkeye", "orientation", "pitch-data"):
            hit = context(text, term, radius=1800, limit=3600)
            if hit:
                contexts[term] = hit
        out.append({
            "script": url,
            "counts": counts,
            "endpoints": endpoint_literals(text),
            "contexts": contexts,
        })
    return out


def seasonal_rows(body: dict) -> list[dict]:
    pitches = body.get("pitches") or {}
    rows = []
    for pitch_type, values in pitches.items():
        for row in values or []:
            item = dict(row)
            item["_bucket_pitch_type"] = pitch_type
            rows.append(item)
    return rows


def pitch3d_rows(response: requests.Response) -> tuple[list[str], list[dict]]:
    response.raise_for_status()
    reader = csv.DictReader(io.StringIO(response.content.decode("utf-8-sig")))
    rows = list(reader)
    return list(reader.fieldnames or []), rows


def subset(row: dict | None, keys: tuple[str, ...]) -> dict | None:
    if row is None:
        return None
    return {key: row.get(key) for key in keys if key in row}


def test_deep_spin_orientation_probe():
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (compatible; one-off-research/6.0)",
        "Referer": PLAYER_URL,
    })
    report: dict = {}

    player_response = session.get(PLAYER_URL, timeout=60)
    player_response.raise_for_status()
    player_html = player_response.text

    seasonal_response = session.get(
        BASE + "/player-services/pitches-seasonal",
        params={"playerId": OHTANI, "season": 2026},
        timeout=60,
    )
    seasonal_response.raise_for_status()
    seasonal_body = seasonal_response.json()
    season_rows = seasonal_rows(seasonal_body)
    seasonal_ids = [str(row.get("pid") or "").strip() for row in season_rows if row.get("pid")]
    seasonal_set = set(seasonal_ids)

    p3_response = session.get(PITCH3D_URL, timeout=60)
    p3_headers, p3_rows = pitch3d_rows(p3_response)
    p3_by_play = {
        str(row.get("play_id") or "").strip(): row
        for row in p3_rows if str(row.get("play_id") or "").strip()
    }
    p3_set = set(p3_by_play)
    overlap = seasonal_set & p3_set
    season_only = sorted(seasonal_set - p3_set)

    joined_examples = []
    for pid in sorted(overlap)[:5]:
        season_row = next(row for row in season_rows if row.get("pid") == pid)
        pitch3d_row = p3_by_play[pid]
        joined_examples.append({
            "pid": pid,
            "seasonal": subset(season_row, ("gd", "pt", "vel", "x", "z", "showVideo")),
            "pitch3d": subset(pitch3d_row, (
                "game_pk", "play_id", "game_date", "game_year", "pitch_type", "release_speed",
                "pitch_number", "at_bat_number", "pitcher", "batter",
            )),
        })

    report["seasonal_to_pitch3d_join"] = {
        "seasonal_url": seasonal_response.url,
        "seasonal_rows": len(season_rows),
        "seasonal_unique_pid": len(seasonal_set),
        "seasonal_duplicate_pid": len(seasonal_ids) - len(seasonal_set),
        "seasonal_pitch_type_counts": dict(Counter(row.get("_bucket_pitch_type") for row in season_rows)),
        "pitch3d_url": p3_response.url,
        "pitch3d_rows": len(p3_rows),
        "pitch3d_unique_play_id": len(p3_set),
        "pitch3d_headers": p3_headers,
        "overlap": len(overlap),
        "seasonal_match_rate": round(len(overlap) / len(seasonal_set), 8) if seasonal_set else None,
        "seasonal_unmatched_count": len(season_only),
        "seasonal_unmatched_examples": season_only[:10],
        "joined_examples": joined_examples,
    }

    # Gamelogs is another independent public surface carrying game_pk + play_id.
    gamelogs_response = session.get(
        BASE + "/player-services/gamelogs",
        params={"playerId": OHTANI, "playerType": 1, "viewType": "pitching", "season": 2026},
        timeout=60,
    )
    gamelogs_response.raise_for_status()
    gamelogs = gamelogs_response.json()
    gamelog_ids = {str(row.get("play_id") or "").strip() for row in gamelogs if row.get("play_id")}
    report["gamelog_join"] = {
        "url": gamelogs_response.url,
        "rows": len(gamelogs),
        "unique_play_id": len(gamelog_ids),
        "overlap_with_seasonal": len(gamelog_ids & seasonal_set),
        "overlap_with_pitch3d": len(gamelog_ids & p3_set),
        "first_row": subset(gamelogs[0] if gamelogs else None, ("game_pk", "play_id", "gd", "ab_num", "pt", "v", "event")),
    }

    player_scripts = load_scripts(session, player_html, PLAYER_URL)
    report["player_frontend_trace"] = trace_scripts(player_scripts)

    # Follow one known per-pitch UUID into Savant's video route and inspect its own frontend data flow.
    example_pid = next(iter(sorted(overlap or seasonal_set)), None)
    if example_pid:
        sporty_url = f"{BASE}/sporty-videos?playId={example_pid}"
        sporty_response = session.get(sporty_url, timeout=60)
        sporty = {
            "url": sporty_response.url,
            "status": sporty_response.status_code,
            "bytes": len(sporty_response.content),
            "content_type": sporty_response.headers.get("content-type"),
            "term_counts": {term: sporty_response.text.lower().count(term.lower()) for term in TRACE_TERMS},
            "endpoints": endpoint_literals(sporty_response.text),
            "contexts": {
                term: context(sporty_response.text, term, radius=1600, limit=3200)
                for term in ("playId", "play_id", "hawkeye", "orientation", "video", "pitch")
                if context(sporty_response.text, term, radius=1600, limit=3200)
            },
        }
        if sporty_response.ok:
            sporty["scripts"] = trace_scripts(load_scripts(session, sporty_response.text, sporty_response.url))
        report["sporty_video_trace"] = sporty

    rendered = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2)
    assert len(rendered) < 180_000, f"research report unexpectedly grew to {len(rendered)} bytes"
    pytest.fail("\n===== SAVANT PER-PITCH KEY AND VIDEO TRACE =====\n" + rendered)
