from __future__ import annotations

import json
import re
from urllib.parse import urljoin

import pytest
import requests


pytestmark = pytest.mark.integration


def _contexts(text: str, term: str, limit: int = 8, radius: int = 220) -> list[str]:
    out: list[str] = []
    lower = text.lower()
    needle = term.lower()
    start = 0
    while len(out) < limit:
        index = lower.find(needle, start)
        if index < 0:
            break
        left = max(0, index - radius)
        right = min(len(text), index + len(term) + radius)
        out.append(text[left:right].replace("\n", " "))
        start = index + len(term)
    return out


def _walk_keys(value, prefix: str = "") -> list[str]:
    paths: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            paths.append(path)
            paths.extend(_walk_keys(child, path))
    elif isinstance(value, list):
        for child in value[:3]:
            paths.extend(_walk_keys(child, prefix + "[]"))
    return paths


def test_research_pitch3d_network_probe():
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (compatible; treepolo-research/1.0)",
        "Accept": "text/html,application/xhtml+xml,application/json,text/plain,*/*",
        "Referer": "https://baseballsavant.mlb.com/",
    })

    report: list[str] = []
    page_url = "https://baseballsavant.mlb.com/visuals/pitch3d?player_id=605488"
    response = session.get(page_url, timeout=30)
    report.append(f"PAGE status={response.status_code} final={response.url} bytes={len(response.content)}")
    response.raise_for_status()
    html = response.text

    scripts = re.findall(r'<script[^>]+src=["\']([^"\']+)["\']', html, flags=re.I)
    styles = re.findall(r'<link[^>]+href=["\']([^"\']+)["\']', html, flags=re.I)
    report.append("SCRIPTS=" + json.dumps(scripts))
    report.append("LINKS=" + json.dumps(styles[:30]))

    terms = [
        "pitch3d", "plays1", "game_pk", "play_id", "playId", "seam", "orientation",
        "quaternion", "rotation", "spinAxis", "spin_axis", "hawkeye", "hawk-eye",
        "player-services", "/gf", "fetch(", "axios", "graphql", "tracking",
    ]
    endpoint_pattern = re.compile(r'(?:(?:https?:)?//[^"\'`\\\s]+|/[A-Za-z0-9_.~!$&()*+,;=:@%?/#-]{4,})')

    for src in scripts:
        script_url = urljoin(response.url, src)
        try:
            js = session.get(script_url, timeout=45)
            report.append(f"SCRIPT {script_url} status={js.status_code} bytes={len(js.content)}")
            if not js.ok or len(js.content) > 15_000_000:
                continue
            text = js.text
            found_terms = [term for term in terms if term.lower() in text.lower()]
            report.append(f"FOUND_TERMS {script_url}: {found_terms}")
            for term in found_terms:
                for context in _contexts(text, term, limit=5):
                    report.append(f"CTX[{term}] {context}")
            endpoints = []
            for match in endpoint_pattern.findall(text):
                lower = match.lower()
                if any(token in lower for token in ("pitch", "game", "play", "player", "statcast", "savant", "api", "hawk", "track", "seam", "spin", "visual")):
                    endpoints.append(match[:500])
            report.append("ENDPOINT_LITERALS=" + json.dumps(list(dict.fromkeys(endpoints))[:250]))
        except Exception as exc:  # pragma: no cover - research diagnostics only
            report.append(f"SCRIPT_ERROR {script_url}: {exc!r}")

    gf_url = "https://baseballsavant.mlb.com/gf?game_pk=823200"
    gf = session.get(gf_url, timeout=30)
    report.append(f"GF status={gf.status_code} bytes={len(gf.content)} content_type={gf.headers.get('content-type')}")
    if gf.ok:
        try:
            data = gf.json()
            report.append("GF_TOP_KEYS=" + json.dumps(list(data.keys()) if isinstance(data, dict) else []))
            paths = _walk_keys(data)
            key_terms = ("seam", "orient", "quatern", "rotat", "spin", "axis", "track", "pose")
            matching_paths = [path for path in paths if any(term in path.lower() for term in key_terms)]
            report.append("GF_MATCHING_PATHS=" + json.dumps(list(dict.fromkeys(matching_paths))[:500]))

            if isinstance(data, dict):
                for root_key in ("team_home", "team_away", "home_pitchers", "away_pitchers"):
                    root = data.get(root_key)
                    report.append(f"GF_ROOT {root_key} type={type(root).__name__}")
                    candidates = []
                    if isinstance(root, list):
                        candidates = root
                    elif isinstance(root, dict):
                        for val in root.values():
                            if isinstance(val, list):
                                candidates.extend(val)
                            elif isinstance(val, dict):
                                candidates.append(val)
                    for item in candidates:
                        if isinstance(item, dict) and ("pitch_type" in item or "play_id" in item):
                            report.append(f"GF_SAMPLE_{root_key}_KEYS=" + json.dumps(list(item.keys())))
                            report.append(f"GF_SAMPLE_{root_key}=" + json.dumps(item, default=str)[:10000])
                            break
        except Exception as exc:  # pragma: no cover
            report.append(f"GF_JSON_ERROR {exc!r}")

    # Intentionally fail once so GitHub Actions preserves the complete diagnostic report in job logs.
    pytest.fail("\n===== PITCH3D RESEARCH PROBE =====\n" + "\n".join(report)[:120_000])
