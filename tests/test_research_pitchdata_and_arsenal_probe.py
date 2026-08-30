from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from urllib.parse import urljoin

import pytest
import requests

pytestmark = pytest.mark.integration
BASE = "https://baseballsavant.mlb.com"
OHTANI = 660271


def ctx(text: str, needle: str, radius: int = 1800, max_hits: int = 5):
    low = text.lower(); nlow = needle.lower(); start = 0; out = []
    while True:
        i = low.find(nlow, start)
        if i < 0 or len(out) >= max_hits: break
        left = max(0, i-radius); right = min(len(text), i+len(needle)+radius)
        frag = text[left:right]
        # nearest source-path marker when available in webpack bundle
        srcs = re.findall(r"/usr/local/app/[^`\"']+", frag)
        out.append({"source_hints": srcs[-3:], "text": frag})
        start = i + len(needle)
    return out


def asset_urls(html: str, base_url: str):
    vals = []
    for raw in re.findall(r'''(?:src|href)=[\"']([^\"']+)[\"']''', html, flags=re.I):
        if raw.startswith("javascript:") or raw.startswith("#"): continue
        u = urljoin(base_url, raw)
        if u not in vals: vals.append(u)
    return vals


def report_contexts(report, label, text, needles, radius=1800):
    for needle in needles:
        hits = ctx(text, needle, radius=radius)
        if hits:
            report.append(f"\n===== {label} {needle} HITS={len(hits)} =====")
            for i, hit in enumerate(hits, 1):
                report.append(f"--- HIT {i} SOURCE_HINTS={json.dumps(hit['source_hints'])} ---\n{hit['text']}")


def test_deep_spin_orientation_probe():
    s = requests.Session()
    s.headers.update({"User-Agent": "Mozilla/5.0 (compatible; one-off-research/1.0)", "Referer": BASE + "/"})
    report = []

    # 1) Verify conditional-refresh semantics on the trajectory endpoint.
    purl = f"{BASE}/app/pitch-data/{OHTANI}"
    r = s.get(purl, timeout=60); r.raise_for_status()
    lm = r.headers.get("last-modified")
    cond_headers = {"If-Modified-Since": lm} if lm else {}
    cr = s.get(purl, headers=cond_headers, timeout=60)
    report.append("=== CONDITIONAL PITCH-DATA ===")
    report.append(json.dumps({
        "url": purl, "first_status": r.status_code, "last_modified": lm,
        "cache_control": r.headers.get("cache-control"), "age": r.headers.get("age"),
        "conditional_status": cr.status_code, "conditional_bytes": len(cr.content),
        "conditional_last_modified": cr.headers.get("last-modified"),
    }, sort_keys=True))

    # 2) Player page: exact aggregate spin/orientation payload and JS implementation.
    page_url = f"{BASE}/savant-player/shohei-ohtani-{OHTANI}?playerType=pitcher"
    pr = s.get(page_url, timeout=60); pr.raise_for_status(); html = pr.text
    report.append("\n=== PLAYER PAGE EXACT SPIN PAYLOAD ===")
    report.append(f"url={page_url} status={pr.status_code} bytes={len(pr.content)}")
    payload_needles = [
        "serverVals.spinAxis", "image_spin_x", "image_spin_y", "image_spin_z",
        "image_orientation_angle", "hawkeye_measured", "movement_inferred",
        "alan_active_spin_pct", "active_spin",
    ]
    report_contexts(report, "PLAYER_HTML", html, payload_needles, radius=2600)

    assets = asset_urls(html, page_url)
    js_urls = [u for u in assets if (u.lower().endswith(".js") or ".js?" in u.lower()) and ("mlbstatic.com" in u.lower() or "baseballsavant.mlb.com" in u.lower())]
    report.append("\nPLAYER_JS_URLS=" + json.dumps(js_urls))
    exact_needles = [
        "image_spin_x", "image_spin_y", "image_spin_z", "image_orientation_angle",
        "hawkeye_measured", "movement_inferred", "alan_active_spin_pct", "active_spin",
        "spinAxis", "SphereGeometry", "Quaternion", "quaternion", "rotateOnAxis",
        "setFromAxisAngle", "Canvas", "useFrame", "baseball", "seam", ".glb", ".gltf",
    ]
    for u in js_urls:
        try:
            jr = s.get(u, timeout=60)
            if not jr.ok or len(jr.content) > 12_000_000: continue
            text = jr.text
            if any(n.lower() in text.lower() for n in exact_needles):
                report.append(f"\n=== PLAYER_JS {u} status={jr.status_code} bytes={len(jr.content)} ===")
                report_contexts(report, "PLAYER_JS", text, exact_needles, radius=2200)
                # Pull nearby URL/path literals from neighborhoods around exact data-field names.
                for field in ("image_spin_x", "image_orientation_angle", "hawkeye_measured"):
                    for hit in ctx(text, field, radius=5000, max_hits=3):
                        paths = sorted(set(re.findall(r'''[\"'`]((?:/|https?://)[^\"'`\s]{2,180})[\"'`]''', hit["text"])))
                        report.append(f"NEARBY_PATHS field={field} paths=" + json.dumps(paths[:80]))
        except Exception as exc:
            report.append(f"PLAYER_JS_ERROR {u} {exc!r}")

    # 3) Spin Direction leaderboard: inspect server payload / download path / scripts.
    lb_url = f"{BASE}/leaderboard/spin-direction-pitches?year=2026&pitch_type=FF&playerName=Shohei%20Ohtani&min=0"
    lr = s.get(lb_url, timeout=60); lr.raise_for_status(); lhtml = lr.text
    report.append("\n=== SPIN DIRECTION LEADERBOARD ===")
    report.append(f"url={lb_url} status={lr.status_code} bytes={len(lr.content)}")
    report_contexts(report, "LEADERBOARD_HTML", lhtml, [
        "Download CSV", "image_spin_x", "image_orientation_angle", "hawkeye_measured",
        "movement_inferred", "active_spin", "spin-direction-pitches", "csv", "download",
    ], radius=2600)
    la = asset_urls(lhtml, lb_url)
    report.append("LEADERBOARD_ASSETS=" + json.dumps(la))
    ljs = [u for u in la if (u.lower().endswith(".js") or ".js?" in u.lower()) and ("mlbstatic.com" in u.lower() or "baseballsavant.mlb.com" in u.lower())]
    for u in ljs:
        try:
            jr = s.get(u, timeout=60)
            if not jr.ok or len(jr.content) > 12_000_000: continue
            text = jr.text
            needles = ["spin-direction-pitches", "Download CSV", "download-csv", "image_spin_x", "hawkeye_measured", "movement_inferred", "active_spin", ".csv", "/leaderboard/"]
            if any(n.lower() in text.lower() for n in needles):
                report.append(f"\n=== LEADERBOARD_JS {u} bytes={len(jr.content)} ===")
                report_contexts(report, "LEADERBOARD_JS", text, needles, radius=2200)
        except Exception as exc:
            report.append(f"LB_JS_ERROR {u} {exc!r}")

    # 4) Generic exact-field search across a few known Savant pages; helps expose a reusable data route.
    for probe_url in [
        f"{BASE}/leaderboard/spin-direction-comparison?year=2026&team=&min=0",
        f"{BASE}/leaderboard/active-spin?year=2026&team=&min=0",
    ]:
        rr = s.get(probe_url, timeout=60)
        report.append(f"\n=== OTHER_PAGE {probe_url} status={rr.status_code} bytes={len(rr.content)} ===")
        if rr.ok:
            report_contexts(report, "OTHER_HTML", rr.text, ["image_spin_x", "image_orientation_angle", "hawkeye_measured", "movement_inferred", "active_spin", "Download CSV"], radius=2200)

    pytest.fail("\n===== DEEP SPIN ORIENTATION RESEARCH REPORT =====\n" + "\n".join(report)[:650_000])
