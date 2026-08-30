from __future__ import annotations

import json
import re

import pytest
import requests

pytestmark = pytest.mark.integration

BUNDLE = "https://builds.mlbstatic.com/baseballsavant.mlb.com/v1/sections/pitch3d/builds/481da0828fbd1bffbc8b85f622851c074e1cc2b3/scripts/build/pitch3d.js"
SAMPLE = "https://baseballsavant.mlb.com/app/pitch-data/606115"


def contexts(text: str, needle: str, radius: int = 1800, limit: int = 8) -> list[str]:
    result = []
    start = 0
    while len(result) < limit:
        idx = text.find(needle, start)
        if idx < 0:
            break
        result.append(text[max(0, idx-radius):min(len(text), idx+len(needle)+radius)])
        start = idx + len(needle)
    return result


def test_research_pitch3d_render_and_schema_probe():
    s = requests.Session()
    s.headers.update({"User-Agent": "Mozilla/5.0 (compatible; one-off-research/1.0)", "Referer": "https://baseballsavant.mlb.com/visuals/pitch3d"})
    r = s.get(BUNDLE, timeout=45)
    r.raise_for_status()
    text = r.text
    report = [f"BUNDLE status={r.status_code} bytes={len(r.content)}"]

    for needle in (
        "spinRate", "revsToPlate", "release.angle", "release.direction",
        "SphereGeometry", "SphereBufferGeometry", "baseball", "seam", "texture",
        ".rotation", "rotateOnAxis", "setFromAxisAngle",
    ):
        found = contexts(text, needle)
        report.append(f"\n===== {needle} occurrences={len(found)} =====")
        for i, ctx in enumerate(found, 1):
            report.append(f"\n--- {needle} #{i} ---\n{ctx}")

    sr = s.get(SAMPLE, timeout=45)
    report.append(f"\nSAMPLE status={sr.status_code} bytes={len(sr.content)} content_type={sr.headers.get('content-type')}")
    sr.raise_for_status()
    data = sr.json()
    report.append(f"SAMPLE_TYPE={type(data).__name__} LEN={len(data) if isinstance(data, list) else 'n/a'}")
    if isinstance(data, list) and data:
        keys = sorted(set().union(*(row.keys() for row in data[:min(20, len(data))] if isinstance(row, dict))))
        report.append("SAMPLE_KEYS=" + json.dumps(keys))
        suspicious = [k for k in keys if any(term in k.lower() for term in ("seam", "orient", "quatern", "rotat", "spin", "axis", "pose", "revol", "angle", "direction", "omega"))]
        report.append("SAMPLE_POSE_RELATED_KEYS=" + json.dumps(suspicious))
        first = data[0]
        report.append("FIRST_ROW_POSE_RELATED=" + json.dumps({k: first.get(k) for k in suspicious}, default=str))
        report.append("FIRST_ROW_IDS=" + json.dumps({k:first.get(k) for k in ("game_pk","play_id","pitcher","game_date","api_pitch_type")}, default=str))

    pytest.fail("\n===== PITCH3D RENDER+SCHEMA PROBE =====\n" + "\n".join(report)[:220_000])
