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
    return {
        "all_url_count": len(urls),
        "target_url_count": len(target),
        "same_origin_url_count": len(same_origin),
        "same_origin_endpoint_count": len(endpoint_inventory(same_origin)),
        "api_endpoints": endpoint_inventory(api),
        "pitch_spin_tracking_endpoints": endpoint_inventory(candidates),
    }


def capture_network(chrome: str, page_url: str) -> dict:
    with tempfile.TemporaryDirectory(prefix="savant-netlog-") as temp:
        root = Path(temp)
        netlog_path = root / "netlog.json"
        profile = root / "profile"
        command = [
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
            "stderr_tail": completed.stderr[-1800:],
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


def test_deep_spin_orientation_probe():
    chrome = chrome_executable()
    version = subprocess.run([chrome, "--version"], capture_output=True, text=True, check=False)
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 (compatible; one-off-research/14.0)"})
    pid = first_video_pid(session)

    report = {
        "chrome": chrome,
        "chrome_version": version.stdout.strip() or version.stderr.strip(),
        "captures": [
            capture_network(chrome, PLAYER_URL),
            capture_network(chrome, SPIN_URL),
            capture_network(chrome, f"{BASE}/sporty-videos?playId={pid}"),
        ],
    }
    rendered = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2)
    assert len(rendered) < 60_000, f"browser network report unexpectedly grew to {len(rendered)} bytes"
    pytest.fail("\n===== REAL CHROME SAVANT NETWORK REPORT =====\n" + rendered)
