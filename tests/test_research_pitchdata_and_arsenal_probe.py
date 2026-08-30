from __future__ import annotations

import json
import re

import pytest
import requests

pytestmark = pytest.mark.integration
BASE = "https://baseballsavant.mlb.com"
OHTANI = 660271
PLAYER_URL = f"{BASE}/savant-player/shohei-ohtani-{OHTANI}?playerType=pitcher"
TARGET_TERMS = (
    "image_spin_x", "image_spin_y", "image_spin_z", "image_orientation_angle",
    "hawkeye_measured", "movement_inferred", "orientation", "seam",
    "game_pk", "play_id", "at_bat", "pitch_number", "game_date",
)


def compact(text: str, limit: int = 3500) -> str:
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


def all_keys(value, out=None, depth=0):
    if out is None:
        out = set()
    if depth > 8:
        return out
    if isinstance(value, dict):
        for key, child in value.items():
            out.add(str(key))
            all_keys(child, out, depth + 1)
    elif isinstance(value, list):
        for child in value[:40]:
            all_keys(child, out, depth + 1)
    return out


def shape(value, depth=0):
    if depth >= 4:
        return type(value).__name__
    if isinstance(value, dict):
        out = {"type": "dict", "keys": list(value)[:120]}
        children = {}
        for key, child in list(value.items())[:30]:
            if isinstance(child, (dict, list)):
                children[key] = shape(child, depth + 1)
        if children:
            out["children"] = children
        return out
    if isinstance(value, list):
        out = {"type": "list", "length": len(value)}
        if value:
            out["first"] = shape(value[0], depth + 1)
        return out
    return {"type": type(value).__name__, "value": value}


def interesting_keys(value):
    keys = sorted(all_keys(value))
    tokens = ("spin", "orient", "seam", "hawk", "game", "play", "pitch", "date", "bat", "uid", "id")
    return [key for key in keys if any(token in key.lower() for token in tokens)][:300]


def json_probe(response: requests.Response) -> dict:
    out = {
        "url": response.url,
        "status": response.status_code,
        "content_type": response.headers.get("content-type"),
        "bytes": len(response.content),
        "target_term_counts": {term: response.text.lower().count(term.lower()) for term in TARGET_TERMS},
    }
    try:
        body = response.json()
    except Exception:
        out["text_head"] = compact(response.text[:10000], 5000)
        return out
    out["shape"] = shape(body)
    out["interesting_keys"] = interesting_keys(body)
    if isinstance(body, list) and body and isinstance(body[0], dict):
        out["first_row"] = body[0]
    elif isinstance(body, dict):
        for key in ("pitches", "pitchDetails", "pitchBreakdown", "data", "rows"):
            child = body.get(key)
            if isinstance(child, list) and child:
                out[f"sample_{key}"] = child[0]
            elif isinstance(child, dict) and child:
                first_key = next(iter(child))
                sample = child[first_key]
                out[f"sample_{key}"] = {
                    "first_key": first_key,
                    "value_type": type(sample).__name__,
                    "value_length": len(sample) if isinstance(sample, (list, dict)) else None,
                    "first_value": sample[0] if isinstance(sample, list) and sample else sample if not isinstance(sample, (list, dict)) else None,
                    "child_keys": list(sample)[:120] if isinstance(sample, dict) else None,
                    "first_child": sample[0] if isinstance(sample, list) and sample else None,
                }
    return out


def html_probe(response: requests.Response) -> dict:
    text = response.text
    return {
        "url": response.url,
        "status": response.status_code,
        "content_type": response.headers.get("content-type"),
        "bytes": len(response.content),
        "target_term_counts": {term: text.lower().count(term.lower()) for term in TARGET_TERMS},
        "game_pk_context": context(text, "game_pk", radius=1600, limit=3200),
        "play_id_context": context(text, "play_id", radius=1600, limit=3200),
        "orientation_context": context(text, "orientation", radius=1600, limit=3200),
        "text_head": compact(text[:8000], 3500),
    }


def test_deep_spin_orientation_probe():
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (compatible; one-off-research/5.0)",
        "Referer": PLAYER_URL,
    })
    report: dict = {}

    page = session.get(PLAYER_URL, timeout=60)
    page.raise_for_status()
    html = page.text
    report["initial_servervals_contexts"] = {
        name: context(html, name, radius=2600, limit=5200)
        for name in ("pitchDetails", "statcastPitches", "pitchBreakdown", "spinAxis")
    }

    seasonal = session.get(
        BASE + "/player-services/pitches-seasonal",
        params={"playerId": OHTANI, "season": 2026},
        timeout=60,
    )
    report["pitches_seasonal"] = json_probe(seasonal)

    gamelogs = session.get(
        BASE + "/player-services/gamelogs",
        params={"playerId": OHTANI, "playerType": 1, "viewType": "pitching", "season": 2026},
        timeout=60,
    )
    report["gamelogs_pitching"] = json_probe(gamelogs)

    # The detailed-pitch table service returns HTML. Empty/default filter values mirror the initial UI state.
    breakdown = session.get(
        BASE + "/player-services/statcast-pitches-breakdown",
        params={
            "playerId": OHTANI,
            "position": 1,
            "hand": "",
            "pitchBreakdown": "pitch-type",
            "timeFrame": "year",
            "season": 2026,
            "pitchType": "",
            "count": 50,
            "gameType": "R",
            "updatePitches": "false",
        },
        timeout=60,
    )
    report["statcast_pitches_breakdown"] = html_probe(breakdown)

    # A few other JSON player services are included only to establish whether any carries raw spin/orientation fields.
    histogram = session.get(
        BASE + "/player-services/histogram",
        params={
            "playerId": OHTANI, "pos": 1, "fieldType": "release_speed", "hand": "",
            "size": 5, "season": 2026, "event": "", "pitchType": "",
        },
        timeout=60,
    )
    report["histogram"] = json_probe(histogram)

    rolling = session.get(
        BASE + "/player-services/roll",
        params={"playerId": OHTANI, "playerType": 1, "count": 50, "type": "release_speed", "year": 2026},
        timeout=60,
    )
    report["roll"] = json_probe(rolling)

    rendered = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2)
    assert len(rendered) < 150_000, f"research report unexpectedly grew to {len(rendered)} bytes"
    pytest.fail("\n===== SAVANT PLAYER PITCH SERVICE GRANULARITY =====\n" + rendered)
