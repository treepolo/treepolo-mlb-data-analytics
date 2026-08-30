from __future__ import annotations

import csv
import hashlib
import io
import json

import pytest
import requests

pytestmark = pytest.mark.integration
BASE = "https://baseballsavant.mlb.com"
OHTANI = 660271
SPIN_PATH = "/savant/api/v1/spin-direction-pitches"
PITCH3D_PATH = f"/app/pitch-data/{OHTANI}"
SEASONAL_PATH = "/player-services/pitches-seasonal"
POSE_FIELDS = (
    "image_spin_x", "image_spin_y", "image_spin_z", "image_orientation_angle",
    "hawkeye_measured", "movement_inferred", "active_spin", "alan_active_spin_pct",
)
PER_PITCH_KEYS = (
    "play_id", "playId", "pid", "game_pk", "gamePk", "game_date",
    "at_bat_number", "pitch_number", "pitch_uid",
)


def body_hash(response: requests.Response) -> str:
    return hashlib.sha256(response.content).hexdigest()


def json_rows(response: requests.Response):
    try:
        body = response.json()
    except Exception:
        return None, None
    if isinstance(body, list):
        return body, "$root"
    if isinstance(body, dict):
        for key, value in body.items():
            if isinstance(value, list):
                return value, key
        return [body], "$dict"
    return [], "$scalar"


def response_signature(response: requests.Response) -> dict:
    rows, container = json_rows(response)
    out = {
        "url": response.url,
        "status": response.status_code,
        "content_type": response.headers.get("content-type"),
        "bytes": len(response.content),
        "sha256": body_hash(response),
    }
    if rows is None:
        out["text_head"] = response.text[:500].replace("\n", " ")
        return out
    out["row_container"] = container
    out["row_count"] = len(rows)
    if rows and isinstance(rows[0], dict):
        row = rows[0]
        out["first_row_keys"] = list(row)
        out["pose_fields_present"] = [key for key in POSE_FIELDS if key in row]
        out["per_pitch_keys_present"] = [key for key in PER_PITCH_KEYS if key in row]
        out["first_row_core"] = {
            key: row.get(key)
            for key in (
                "player_id", "api_pitch_type", "api_pitch_name", "n_pitches",
                *POSE_FIELDS, *PER_PITCH_KEYS,
            )
            if key in row
        }
    return out


def classify(signature: dict, baseline: dict) -> str:
    if signature["status"] >= 400:
        return "error"
    if signature.get("row_container") is None:
        return "non_json"
    if signature["sha256"] == baseline["sha256"]:
        return "ignored_or_no_effect"
    row = signature.get("first_row_core") or {}
    if row.get("n_pitches") == 1 or signature.get("per_pitch_keys_present"):
        return "possible_per_pitch"
    return "changed_aggregate"


def load_pitch3d(session: requests.Session) -> tuple[list[str], list[dict]]:
    response = session.get(BASE + PITCH3D_PATH, timeout=90)
    response.raise_for_status()
    reader = csv.DictReader(io.StringIO(response.content.decode("utf-8-sig")))
    return list(reader.fieldnames or []), list(reader)


def load_seasonal(session: requests.Session) -> list[dict]:
    response = session.get(
        BASE + SEASONAL_PATH,
        params={"playerId": OHTANI, "season": 2026},
        timeout=90,
    )
    response.raise_for_status()
    body = response.json()
    rows = []
    for pitch_type, values in (body.get("pitches") or {}).items():
        for value in values or []:
            row = dict(value)
            row["_bucket_pitch_type"] = pitch_type
            rows.append(row)
    return rows


def find_known_ff_pitch(session: requests.Session) -> dict:
    seasonal = load_seasonal(session)
    headers, pitch3d = load_pitch3d(session)
    by_play = {
        str(row.get("play_id") or "").strip(): row
        for row in pitch3d
        if str(row.get("play_id") or "").strip()
    }
    selected = next(
        row for row in seasonal
        if row.get("_bucket_pitch_type") == "FF" and str(row.get("pid") or "") in by_play
    )
    pid = str(selected["pid"])
    joined = by_play[pid]
    return {
        "pid": pid,
        "seasonal": {
            key: selected.get(key)
            for key in ("gd", "pt", "vel", "showVideo")
        },
        "pitch3d": {
            key: joined.get(key)
            for key in (
                "game_pk", "play_id", "game_date", "game_year", "pitch_type",
                "pitch_number", "at_bat_number", "pitcher", "batter", "release_speed",
            )
            if key in headers
        },
    }


def get_spin(session: requests.Session, params: dict, *, path: str = SPIN_PATH) -> requests.Response:
    return session.get(BASE + path, params=params, timeout=45)


def test_deep_spin_orientation_probe():
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (compatible; one-off-research/9.0)",
        "Referer": BASE + "/leaderboard/spin-direction-pitches",
    })

    known = find_known_ff_pitch(session)
    game_pk = known["pitch3d"].get("game_pk")
    game_date = known["pitch3d"].get("game_date") or str(known["seasonal"].get("gd") or "")[:10]
    play_id = known["pid"]

    base_params = {"pitcher": OHTANI, "year": 2026, "pov": "Pit", "pitch_type": "FF"}
    baseline_response = get_spin(session, base_params)
    baseline_response.raise_for_status()
    baseline = response_signature(baseline_response)

    # First establish the pitch-type parameter name by asking for Ohtani's sweeper row.
    control_trials = [
        ("pitch_type_ST", {**base_params, "pitch_type": "ST"}),
        ("pitchType_ST", {**base_params, "pitchType": "ST"}),
        ("api_pitch_type_ST", {**base_params, "api_pitch_type": "ST"}),
    ]

    # Then test exact per-pitch / game / date keys already proven to identify this pitch elsewhere in Savant.
    fine_trials = [
        ("play_id", {**base_params, "play_id": play_id}),
        ("playId", {**base_params, "playId": play_id}),
        ("pid", {**base_params, "pid": play_id}),
        ("game_pk", {**base_params, "game_pk": game_pk}),
        ("gamePk", {**base_params, "gamePk": game_pk}),
        ("game_date", {**base_params, "game_date": game_date}),
        ("date", {**base_params, "date": game_date}),
        ("type_details", {**base_params, "type": "details"}),
        ("type_detail", {**base_params, "type": "detail"}),
        ("detail_true", {**base_params, "detail": "true"}),
        ("raw_true", {**base_params, "raw": "true"}),
        ("group_by_play_id", {**base_params, "group_by": "play_id"}),
        ("groupBy_play_id", {**base_params, "groupBy": "play_id"}),
        ("min_zero", {**base_params, "min": 0}),
    ]

    trials = []
    for name, params in control_trials + fine_trials:
        response = get_spin(session, params)
        signature = response_signature(response)
        trials.append({
            "name": name,
            "extra_params": {key: value for key, value in params.items() if base_params.get(key) != value or key not in base_params},
            "classification": classify(signature, baseline),
            "signature": signature,
        })

    # A few route-shape variants are worth one exact request each now that the working route is known.
    suffix_trials = []
    for suffix in (f"/{play_id}", "/details", "/detail"):
        response = get_spin(session, base_params, path=SPIN_PATH + suffix)
        signature = response_signature(response)
        suffix_trials.append({
            "suffix": suffix,
            "classification": classify(signature, baseline),
            "signature": signature,
        })

    report = {
        "known_exact_pitch": known,
        "baseline": baseline,
        "query_trials": trials,
        "suffix_trials": suffix_trials,
    }
    rendered = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2)
    assert len(rendered) < 110_000, f"research report unexpectedly grew to {len(rendered)} bytes"
    pytest.fail("\n===== SPIN DIRECTION PER-PITCH FILTER PROBE =====\n" + rendered)
