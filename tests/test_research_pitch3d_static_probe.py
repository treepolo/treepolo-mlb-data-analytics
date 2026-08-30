from __future__ import annotations

import csv
import io
import json
import re

import pytest
import requests

pytestmark = pytest.mark.integration

BUNDLE = "https://builds.mlbstatic.com/baseballsavant.mlb.com/v1/sections/pitch3d/builds/481da0828fbd1bffbc8b85f622851c074e1cc2b3/scripts/build/pitch3d.js"
SAMPLE = "https://baseballsavant.mlb.com/app/pitch-data/606115"
SOURCE_PREFIX = "/usr/local/app/sections/pitch3d/"


def custom_contexts(text: str, needle: str, radius: int = 1800, limit: int = 12):
    out = []
    start = 0
    while len(out) < limit:
        idx = text.find(needle, start)
        if idx < 0:
            break
        left_path = text.rfind(SOURCE_PREFIX, max(0, idx-12000), idx)
        if left_path >= 0:
            path_end = text.find("`", left_path)
            path = text[left_path:path_end if path_end > 0 else idx]
            out.append((path, text[max(0, idx-radius):min(len(text), idx+len(needle)+radius)]))
        start = idx + len(needle)
    return out


def test_research_pitch3d_render_and_schema_probe():
    s = requests.Session()
    s.headers.update({"User-Agent": "Mozilla/5.0 (compatible; one-off-research/1.0)", "Referer": "https://baseballsavant.mlb.com/visuals/pitch3d"})
    r = s.get(BUNDLE, timeout=45)
    r.raise_for_status()
    text = r.text
    report = [f"BUNDLE status={r.status_code} bytes={len(r.content)}"]

    for needle in (
        "spinRate", "revsToPlate", "release.angle", "release.direction",
        "SphereGeometry", "baseball", "seam", "texture", "rotation",
        "rotateOnAxis", "setFromAxisAngle",
    ):
        found = custom_contexts(text, needle)
        report.append(f"\n===== CUSTOM {needle} occurrences={len(found)} =====")
        for i, (path, ctx) in enumerate(found, 1):
            report.append(f"\n--- {needle} #{i} source~{path} ---\n{ctx}")

    sr = s.get(SAMPLE, timeout=45)
    report.append(f"\nSAMPLE status={sr.status_code} bytes={len(sr.content)} content_type={sr.headers.get('content-type')}")
    sr.raise_for_status()
    decoded = sr.content.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(decoded))
    rows = list(reader)
    keys = list(reader.fieldnames or [])
    report.append(f"SAMPLE_ROWS={len(rows)}")
    report.append("SAMPLE_KEYS=" + json.dumps(keys))
    suspicious = [k for k in keys if any(term in k.lower() for term in ("seam", "orient", "quatern", "rotat", "spin", "axis", "pose", "revol", "angle", "direction", "omega"))]
    report.append("SAMPLE_POSE_RELATED_KEYS=" + json.dumps(suspicious))
    if rows:
        first = rows[0]
        report.append("FIRST_ROW_POSE_RELATED=" + json.dumps({k: first.get(k) for k in suspicious}, default=str))
        report.append("FIRST_ROW_IDS=" + json.dumps({k:first.get(k) for k in ("game_pk","play_id","pitcher","game_date","api_pitch_type")}, default=str))

    pytest.fail("\n===== PITCH3D FINAL SCHEMA PROBE =====\n" + "\n".join(report)[:260_000])
