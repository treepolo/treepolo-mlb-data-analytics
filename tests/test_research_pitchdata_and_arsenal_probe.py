from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import parse_qsl, urlparse

import pytest
import requests

pytestmark = pytest.mark.integration
BASE = "https://baseballsavant.mlb.com"
OHTANI = 660271
PLAYER_URL = f"{BASE}/savant-player/shohei-ohtani-{OHTANI}?playerType=pitcher"
SPIN_URL = f"{BASE}/leaderboard/spin-direction-pitches?year=2026&pitch_type=FF&min=0"
CANDIDATE_TERMS = ("hawk", "orient", "seam", "track", "spin", "pitch")
API_MARKERS = ("/savant/api/", "/player-services/", "/app/", "/api/")
DETAIL_PATH = "/leaderboard/spin-axis-by-pitcher"
BREAKDOWN_PATH = "/player-services/statcast-pitches-breakdown"
DETAIL_FIELDS = (
    "play_id", "pid", "game_pk", "at_bat_number", "pitch_number",
    "pitch_type", "release_speed", "release_spin_rate", "spin_axis",
    "image_spin_x", "image_spin_y", "image_spin_z", "image_orientation_angle",
    "hawkeye_measured", "movement_inferred", "spinAxis",
)
DOM_TERMS = (
    "spin-axis-by-pitcher", "measured", "inferred", "pitch_type", "pitch-type",
    "spin direction", "spin-direction", "pov", "pitcher",
)


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


def chrome_executable() -> str:
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        path = shutil.which(name)
        if path:
            return path
    raise AssertionError("GitHub Actions runner has no Chrome/Chromium executable")


def iter_strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from iter_strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from iter_strings(child)


def extract_urls(netlog: dict) -> list[str]:
    urls = set()
    for text in iter_strings(netlog):
        for match in re.findall(r"https?://[^\s\"'<>\\]+", text):
            cleaned = match.rstrip(",);]}")
            if len(cleaned) <= 4096:
                urls.add(cleaned)
    return sorted(urls)


def target_url(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return host == "baseballsavant.mlb.com" or host == "statsapi.mlb.com"


def endpoint_signature(url: str) -> tuple[str, str, tuple[str, ...]]:
    parsed = urlparse(url)
    query_keys = tuple(sorted({key for key, _ in parse_qsl(parsed.query, keep_blank_values=True)}))
    return parsed.netloc.lower(), parsed.path or "/", query_keys


def endpoint_inventory(urls: list[str]) -> list[dict]:
    signatures = {endpoint_signature(url) for url in urls}
    return [
        {"host": host, "path": path, "query_keys": list(query_keys)}
        for host, path, query_keys in sorted(signatures)
    ]


def summarize_urls(urls: list[str]) -> dict:
    target = [url for url in urls if target_url(url)]
    api = [url for url in target if any(marker in url for marker in API_MARKERS)]
    candidates = [url for url in target if any(term in url.lower() for term in CANDIDATE_TERMS)]
    same_origin = [url for url in target if urlparse(url).netloc.lower() == "baseballsavant.mlb.com"]
    detail_urls = sorted({url for url in target if urlparse(url).path == DETAIL_PATH})
    breakdown_urls = sorted({url for url in target if urlparse(url).path == BREAKDOWN_PATH})
    return {
        "all_url_count": len(urls),
        "target_url_count": len(target),
        "same_origin_url_count": len(same_origin),
        "same_origin_endpoint_count": len(endpoint_inventory(same_origin)),
        "api_endpoints": endpoint_inventory(api),
        "pitch_spin_tracking_endpoints": endpoint_inventory(candidates),
        "spin_axis_by_pitcher_urls": detail_urls[:8],
        "statcast_pitches_breakdown_urls": breakdown_urls[:12],
    }


def chrome_base_command(chrome: str, profile: Path) -> list[str]:
    return [
        chrome,
        "--headless=new",
        "--no-sandbox",
        "--disable-gpu",
        "--disable-dev-shm-usage",
        "--disable-background-networking",
        "--disable-component-update",
        "--disable-default-apps",
        "--no-first-run",
        "--no-default-browser-check",
        f"--user-data-dir={profile}",
    ]


def capture_network(chrome: str, page_url: str) -> dict:
    with tempfile.TemporaryDirectory(prefix="savant-netlog-") as temp:
        root = Path(temp)
        netlog_path = root / "netlog.json"
        profile = root / "profile"
        command = chrome_base_command(chrome, profile) + [
            f"--log-net-log={netlog_path}",
            "--net-log-capture-mode=IncludeSensitive",
            "--virtual-time-budget=12000",
            "--dump-dom",
            page_url,
        ]
        completed = subprocess.run(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            timeout=45,
            check=False,
        )
        item = {
            "page_url": page_url,
            "returncode": completed.returncode,
            "stderr_tail": completed.stderr[-500:],
            "netlog_exists": netlog_path.exists(),
            "netlog_bytes": netlog_path.stat().st_size if netlog_path.exists() else 0,
        }
        if not netlog_path.exists():
            return item
        try:
            netlog = json.loads(netlog_path.read_text(encoding="utf-8"))
        except Exception as exc:
            item["netlog_parse_error"] = repr(exc)
            return item
        item.update(summarize_urls(extract_urls(netlog)))
        return item


def capture_dom_evidence(chrome: str, page_url: str) -> dict:
    with tempfile.TemporaryDirectory(prefix="savant-dom-") as temp:
        root = Path(temp)
        profile = root / "profile"
        dom_path = root / "dom.html"
        command = chrome_base_command(chrome, profile) + [
            "--virtual-time-budget=12000",
            "--dump-dom",
            page_url,
        ]
        with dom_path.open("w", encoding="utf-8") as output:
            completed = subprocess.run(
                command,
                stdout=output,
                stderr=subprocess.PIPE,
                text=True,
                timeout=45,
                check=False,
            )
        text = dom_path.read_text(encoding="utf-8", errors="replace") if dom_path.exists() else ""
        lower = text.lower()
        snippets = []
        seen = set()
        for term in DOM_TERMS:
            start = 0
            while len(snippets) < 24:
                index = lower.find(term.lower(), start)
                if index < 0:
                    break
                snippet = re.sub(r"\s+", " ", text[max(0, index - 260): index + 520]).strip()
                snippet = snippet[:780]
                if snippet not in seen:
                    seen.add(snippet)
                    snippets.append({"term": term, "snippet": snippet})
                start = index + max(1, len(term))
            if len(snippets) >= 24:
                break
        return {
            "page_url": page_url,
            "returncode": completed.returncode,
            "dom_bytes": len(text.encode("utf-8")),
            "term_counts": {term: lower.count(term.lower()) for term in DOM_TERMS},
            "snippets": snippets,
        }


def json_shape(value, depth: int = 0):
    if depth >= 3:
        return type(value).__name__
    if isinstance(value, dict):
        keys = sorted(value)[:30]
        return {key: json_shape(value[key], depth + 1) for key in keys}
    if isinstance(value, list):
        return {
            "type": "list",
            "length": len(value),
            "sample": json_shape(value[0], depth + 1) if value else None,
        }
    return type(value).__name__


def probe_json_url(session: requests.Session, url: str) -> dict:
    response = session.get(url, timeout=60)
    text = response.text
    item = {
        "url": url,
        "status": response.status_code,
        "content_type": response.headers.get("content-type"),
        "bytes": len(response.content),
        "field_counts": {field: text.count(field) for field in DETAIL_FIELDS},
    }
    try:
        payload = response.json()
    except Exception as exc:
        item["json_error"] = repr(exc)
        item["text_prefix"] = re.sub(r"\s+", " ", text[:1200])
        return item
    item["json_shape"] = json_shape(payload)
    return item


def test_deep_spin_orientation_probe():
    chrome = chrome_executable()
    version = subprocess.run([chrome, "--version"], capture_output=True, text=True, check=False)
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 (compatible; one-off-research/16.0)"})
    pid = first_video_pid(session)

    captures = [
        capture_network(chrome, PLAYER_URL),
        capture_network(chrome, SPIN_URL),
        capture_network(chrome, f"{BASE}/sporty-videos?playId={pid}"),
    ]
    breakdown_urls = sorted({
        url
        for capture in captures
        for url in capture.get("statcast_pitches_breakdown_urls", [])
    })

    report = {
        "chrome": chrome,
        "chrome_version": version.stdout.strip() or version.stderr.strip(),
        "captures": captures,
        "statcast_pitches_breakdown_probes": [probe_json_url(session, url) for url in breakdown_urls[:8]],
        "spin_direction_rendered_dom": capture_dom_evidence(chrome, SPIN_URL),
    }
    rendered = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2)
    assert len(rendered) < 80_000, f"browser research report unexpectedly grew to {len(rendered)} bytes"
    pytest.fail("\n===== SAVANT BREAKDOWN + RENDERED DOM REPORT =====\n" + rendered)
