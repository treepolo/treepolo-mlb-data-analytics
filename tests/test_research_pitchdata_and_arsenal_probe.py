from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from collections import Counter
from urllib.parse import urljoin

import pytest
import requests

pytestmark = pytest.mark.integration

BASE = "https://baseballsavant.mlb.com"
PLAYERS = {
    "kershaw": 477132,
    "scherzer": 453286,
    "verlander": 434378,
    "ohtani": 660271,
    "taj_bradley": 671737,
    "alek_manoah": 666201,
    "paul_skenes": 694973,
}


def get_csv(session: requests.Session, url: str):
    r = session.get(url, timeout=60)
    r.raise_for_status()
    text = r.content.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    rows = list(reader)
    return r, rows, list(reader.fieldnames or [])


def summarize(rows):
    dates = [str(r.get("game_date") or "")[:10] for r in rows if r.get("game_date")]
    years = Counter(d[:4] for d in dates if len(d) >= 4)
    ids = [(r.get("game_pk"), r.get("play_id")) for r in rows]
    return {
        "rows": len(rows),
        "min_date": min(dates) if dates else None,
        "max_date": max(dates) if dates else None,
        "years": dict(sorted(years.items())),
        "unique_game_play": len(set(ids)),
        "duplicate_game_play": len(ids) - len(set(ids)),
    }


def headers_subset(r):
    keys = ["content-type", "content-length", "content-range", "link", "x-total-count", "cache-control", "etag", "last-modified", "age"]
    return {k: r.headers.get(k) for k in keys if r.headers.get(k) is not None}


def contexts(text: str, needles, radius=900):
    out = []
    low = text.lower()
    for needle in needles:
        start = 0
        nlow = needle.lower()
        hits = []
        while True:
            i = low.find(nlow, start)
            if i < 0:
                break
            hits.append(text[max(0, i-radius):min(len(text), i+len(needle)+radius)])
            start = i + len(needle)
            if len(hits) >= 8:
                break
        if hits:
            out.append((needle, hits))
    return out


def test_research_pitchdata_and_arsenal_probe():
    s = requests.Session()
    s.headers.update({"User-Agent": "Mozilla/5.0 (compatible; one-off-research/1.0)", "Referer": BASE + "/"})
    report = []

    report.append("=== MLB PITCH-DATA COVERAGE ===")
    base_payloads = {}
    for name in ("kershaw", "scherzer", "verlander", "ohtani", "taj_bradley", "alek_manoah", "paul_skenes"):
        pid = PLAYERS[name]
        url = f"{BASE}/app/pitch-data/{pid}"
        r, rows, keys = get_csv(s, url)
        raw = r.content
        base_payloads[name] = raw
        report.append(json.dumps({
            "name": name,
            "player_id": pid,
            "url": url,
            "summary": summarize(rows),
            "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "headers": headers_subset(r),
            "field_count": len(keys),
        }, sort_keys=True))

    report.append("=== POSSIBLE PAGINATION/PARAM BEHAVIOR (KERSHAW) ===")
    pid = PLAYERS["kershaw"]
    variants = [
        "?page=2", "?page=1&limit=10", "?limit=10", "?offset=10&limit=10",
        "?year=2020", "?season=2020",
    ]
    base_hash = hashlib.sha256(base_payloads["kershaw"]).hexdigest()
    for q in variants:
        r, rows, _ = get_csv(s, f"{BASE}/app/pitch-data/{pid}{q}")
        h = hashlib.sha256(r.content).hexdigest()
        report.append(json.dumps({"query": q, "same_as_base": h == base_hash, "sha256": h, "summary": summarize(rows), "headers": headers_subset(r)}, sort_keys=True))

    report.append("=== MINORS=1 COVERAGE ===")
    for name in ("taj_bradley", "alek_manoah", "paul_skenes"):
        pid = PLAYERS[name]
        url = f"{BASE}/app/pitch-data/{pid}?minors=1"
        r, rows, keys = get_csv(s, url)
        report.append(json.dumps({
            "name": name,
            "player_id": pid,
            "url": url,
            "summary": summarize(rows),
            "bytes": len(r.content),
            "headers": headers_subset(r),
            "field_count": len(keys),
            "game_types": dict(sorted(Counter(str(x.get("game_type") or "") for x in rows).items())),
            "venues": Counter(str(x.get("venue_name") or "") for x in rows).most_common(12),
        }, sort_keys=True))

    report.append("=== REFETCH STABILITY ===")
    for name in ("ohtani", "paul_skenes"):
        pid = PLAYERS[name]
        r1 = s.get(f"{BASE}/app/pitch-data/{pid}", timeout=60); r1.raise_for_status()
        r2 = s.get(f"{BASE}/app/pitch-data/{pid}", timeout=60); r2.raise_for_status()
        report.append(json.dumps({
            "name": name,
            "same_bytes_immediate_refetch": r1.content == r2.content,
            "hash1": hashlib.sha256(r1.content).hexdigest(),
            "hash2": hashlib.sha256(r2.content).hexdigest(),
            "headers1": headers_subset(r1),
            "headers2": headers_subset(r2),
        }, sort_keys=True))

    report.append("=== OHTANI PLAYER PAGE / ARSENAL ASSETS ===")
    page_url = f"{BASE}/savant-player/shohei-ohtani-660271?playerType=pitcher"
    pr = s.get(page_url, timeout=60); pr.raise_for_status()
    html = pr.text
    report.append(f"PLAYER_PAGE status={pr.status_code} bytes={len(pr.content)}")
    asset_urls = []
    for attr in re.findall(r'''(?:src|href)=[\"']([^\"']+)[\"']''', html, flags=re.I):
        if attr.startswith("javascript:") or attr.startswith("#"):
            continue
        asset_urls.append(urljoin(page_url, attr))
    interesting_assets = [u for u in asset_urls if any(k in u.lower() for k in ("script", ".js", "savant", "player", "pitch", "spin", "arsenal", "ball", "statcast"))]
    report.append("INTERESTING_PAGE_ASSETS=" + json.dumps(interesting_assets[:120]))
    for needle, hits in contexts(html, ["Statcast Pitch Arsenal", "pitch arsenal", "spin direction", "canvas", "webgl", "three", "seam", "orientation", "quaternion", "baseball", "pitch-type"]):
        report.append(f"\nHTML_CONTEXT {needle} hits={len(hits)}")
        for h in hits[:3]:
            report.append(h)

    # Fetch same-origin JS plus MLB static JS, then scan only for custom arena terms / API paths.
    js_urls = []
    for u in asset_urls:
        ul = u.lower()
        if (ul.endswith(".js") or ".js?" in ul) and ("baseballsavant.mlb.com" in ul or "mlbstatic.com" in ul or "mlb.com" in ul):
            if u not in js_urls:
                js_urls.append(u)
    report.append("JS_URLS=" + json.dumps(js_urls[:80]))
    needles = [
        "pitch arsenal", "pitch_arsenal", "pitchArsenal", "arsenal",
        "seam", "orientation", "quaternion", "rotation", "spinAxis", "spin_axis",
        "activeSpin", "active_spin", "spinDirection", "spin_direction",
        "SphereGeometry", "Canvas", "WebGL", "three", "baseball",
        "/app/", "/api/", "/leaderboard/", "/visuals/",
    ]
    for u in js_urls[:30]:
        try:
            rr = s.get(u, timeout=60)
            if not rr.ok or len(rr.content) > 8_000_000:
                report.append(f"JS_SKIP {u} status={rr.status_code} bytes={len(rr.content)}")
                continue
            text = rr.text
            found = contexts(text, needles, radius=600)
            if found:
                report.append(f"\n===== JS {u} status={rr.status_code} bytes={len(rr.content)} =====")
                for needle, hits in found:
                    report.append(f"\nJS_CONTEXT {needle} hits={len(hits)}")
                    for h in hits[:2]:
                        report.append(h)
        except Exception as exc:
            report.append(f"JS_ERROR {u} {exc!r}")

    pytest.fail("\n===== RESEARCH PROBE REPORT =====\n" + "\n".join(report)[:500_000])
