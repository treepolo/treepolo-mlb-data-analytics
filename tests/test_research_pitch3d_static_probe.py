from __future__ import annotations

import json
import re
from urllib.parse import urljoin

import pytest
import requests

pytestmark = pytest.mark.integration

BUNDLE = "https://builds.mlbstatic.com/baseballsavant.mlb.com/v1/sections/pitch3d/builds/481da0828fbd1bffbc8b85f622851c074e1cc2b3/scripts/build/pitch3d.js"


def context(text: str, needle: str, radius: int = 4000) -> str:
    idx = text.find(needle)
    if idx < 0:
        return "<NOT FOUND>"
    return text[max(0, idx-radius):min(len(text), idx+len(needle)+radius)]


def test_research_pitch3d_static_bundle_probe():
    s = requests.Session()
    s.headers["User-Agent"] = "Mozilla/5.0 (compatible; one-off-static-research/1.0)"
    r = s.get(BUNDLE, timeout=45)
    r.raise_for_status()
    text = r.text
    report = [f"BUNDLE status={r.status_code} bytes={len(r.content)}"]

    for needle in (
        "/app/pitch-data/",
        "/usr/local/app/sections/pitch3d/src/pitch3d/data/pitch_data.jsx",
        "polynomial_x_1",
        "api_p_release_extension",
    ):
        report.append(f"\n===== CONTEXT {needle} =====\n{context(text, needle)}")

    # Extract property names referenced in the pitch-data neighborhood.
    marker = text.find("/usr/local/app/sections/pitch3d/src/pitch3d/data/pitch_data.jsx")
    if marker >= 0:
        neighborhood = text[max(0, marker-15000):min(len(text), marker+30000)]
        props = sorted(set(re.findall(r"\b[eEtTnNrRiIaAoOsScClLuUdDfFpPmMhHgG_]\.([A-Za-z_$][A-Za-z0-9_$]*)", neighborhood)))
        raw_keys = sorted(set(re.findall(r"\b(?:api|polynomial|spin|seam|orient|rotat|axis|release|pitch|game|play)[A-Za-z0-9_]*\b", neighborhood, flags=re.I)))
        report.append("\nPITCH_DATA_NEIGHBOR_PROPS=" + json.dumps(props))
        report.append("\nPITCH_DATA_NEIGHBOR_KEYS=" + json.dumps(raw_keys))

    tail = text[-2000:]
    report.append("\n===== BUNDLE TAIL =====\n" + tail)
    m = re.search(r"sourceMappingURL=([^\s]+)", tail)
    if m:
        map_url = urljoin(BUNDLE, m.group(1).strip())
        report.append(f"\nMAP_URL={map_url}")
        mr = s.get(map_url, timeout=45)
        report.append(f"MAP status={mr.status_code} bytes={len(mr.content)}")
        if mr.ok:
            try:
                sm = mr.json()
                sources = sm.get("sources") or []
                contents = sm.get("sourcesContent") or []
                matches = []
                for i, source in enumerate(sources):
                    if "pitch_data" in source.lower() or "pitch-data" in source.lower():
                        matches.append((source, contents[i] if i < len(contents) else None))
                report.append("MAP_MATCH_SOURCES=" + json.dumps([x[0] for x in matches]))
                for source, source_text in matches:
                    report.append(f"\n===== SOURCE {source} =====\n{source_text}")
            except Exception as exc:
                report.append(f"MAP_PARSE_ERROR={exc!r}")

    pytest.fail("\n===== PITCH3D STATIC PROBE =====\n" + "\n".join(report)[:180_000])
