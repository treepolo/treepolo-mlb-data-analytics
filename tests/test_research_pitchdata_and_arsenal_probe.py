from __future__ import annotations

import json
import re
from urllib.parse import urljoin

import pytest
import requests

pytestmark = pytest.mark.integration

BUNDLE = (
    "https://builds.mlbstatic.com/baseballsavant.mlb.com/v1/sections/player-update/"
    "builds/365eecaecb2cdd235bf4378010b37fef2f181f45/scripts/build/index.js"
)
TERMS = (
    "image_spin_x",
    "image_orientation_angle",
    "hawkeye_measured",
    "spinAxis",
    "pitches-seasonal",
    "statcast-pitches-breakdown",
    "/evp/add",
    "play_id",
    "sporty-videos",
)
SOURCE_HINTS = (
    "Chart/Pitch/Spin/Axis.jsx",
    "player-update/scripts/lib/evp/Index.jsx",
    "player-update",
)


def compact(text: str, limit: int = 5000) -> str:
    value = re.sub(r"\s+", " ", str(text)).strip()
    if len(value) <= limit:
        return value
    half = max(1, (limit - 5) // 2)
    return value[:half] + " ... " + value[-half:]


def contexts(text: str, needle: str, radius: int = 2200, max_hits: int = 4) -> list[str]:
    low = text.lower()
    target = needle.lower()
    start = 0
    out = []
    while len(out) < max_hits:
        index = low.find(target, start)
        if index < 0:
            break
        out.append(compact(text[max(0, index - radius): index + len(needle) + radius], radius * 2 + 800))
        start = index + len(target)
    return out


def endpoint_literals(text: str, limit: int = 80) -> list[str]:
    out = []
    for raw in re.findall(r'''["'`]((?:https?://[^"'`\s${}]+|/[^"'`\s${}]+))["'`]''', text):
        low = raw.lower()
        if any(token in low for token in ("spin", "pitch", "hawk", "orient", "evp", "play", "video", "player-service", "savant/api")):
            if raw not in out:
                out.append(raw)
        if len(out) >= limit:
            break
    return out


def network_snippets(text: str, needle: str) -> list[str]:
    out = []
    for hit in contexts(text, needle, radius=4000, max_hits=5):
        if any(token in hit.lower() for token in ("fetch(", ".ajax(", "ajax({", ".get(", ".post(", "axios", "xmlhttprequest")):
            out.append(hit)
    return out


def sourcemap_candidates(bundle_text: str) -> list[str]:
    out = []
    for raw in re.findall(r"sourceMappingURL=([^\s*]+)", bundle_text[-5000:]):
        url = urljoin(BUNDLE, raw.strip())
        if url not in out:
            out.append(url)
    conventional = BUNDLE + ".map"
    if conventional not in out:
        out.append(conventional)
    return out


def source_report(source_name: str, content: str) -> dict:
    found = {term: content.lower().count(term.lower()) for term in TERMS}
    return {
        "source": source_name,
        "bytes": len(content.encode("utf-8", errors="ignore")),
        "term_counts": found,
        "endpoints": endpoint_literals(content),
        "contexts": {
            term: contexts(content, term, radius=1800, max_hits=3)
            for term, count in found.items()
            if count
        },
    }


def test_deep_spin_orientation_probe():
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 (compatible; one-off-research/7.0)"})
    report = {}

    bundle = session.get(BUNDLE, timeout=90)
    bundle.raise_for_status()
    text = bundle.text
    report["bundle"] = {
        "url": BUNDLE,
        "status": bundle.status_code,
        "bytes": len(bundle.content),
        "tail": compact(text[-3000:], 3000),
        "term_counts": {term: text.lower().count(term.lower()) for term in TERMS},
        "evp_add_contexts": contexts(text, "/evp/add", radius=5000, max_hits=5),
        "evp_add_network_contexts": network_snippets(text, "/evp/add"),
        "spin_axis_contexts": contexts(text, "image_spin_x", radius=3500, max_hits=3),
        "seasonal_network_contexts": network_snippets(text, "pitches-seasonal"),
        "breakdown_network_contexts": network_snippets(text, "statcast-pitches-breakdown"),
    }

    report["source_maps"] = []
    for map_url in sourcemap_candidates(text):
        response = session.get(map_url, timeout=120)
        item = {
            "url": response.url,
            "status": response.status_code,
            "content_type": response.headers.get("content-type"),
            "bytes": len(response.content),
        }
        if not response.ok:
            item["text_head"] = compact(response.text[:2000], 1200)
            report["source_maps"].append(item)
            continue
        try:
            body = response.json()
        except Exception:
            item["json"] = False
            item["text_head"] = compact(response.text[:4000], 1800)
            report["source_maps"].append(item)
            continue

        item["json"] = True
        sources = body.get("sources") or []
        contents = body.get("sourcesContent") or []
        item["source_count"] = len(sources)
        item["has_sources_content"] = bool(contents)
        item["matching_source_names"] = [
            name for name in sources
            if any(hint.lower() in str(name).lower() for hint in SOURCE_HINTS)
        ][:100]
        matches = []
        if contents and len(contents) == len(sources):
            for name, content in zip(sources, contents):
                if not isinstance(content, str):
                    continue
                lower = content.lower()
                if any(term.lower() in lower for term in TERMS):
                    matches.append(source_report(str(name), content))
        item["matching_sources"] = matches[:40]
        report["source_maps"].append(item)

    rendered = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2)
    assert len(rendered) < 220_000, f"source-map report unexpectedly grew to {len(rendered)} bytes"
    pytest.fail("\n===== SAVANT PLAYER SOURCE MAP TRACE =====\n" + rendered)
