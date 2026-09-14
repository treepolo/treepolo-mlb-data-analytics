from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from datetime import date
from typing import Any, Iterable

from .cpbl_semantics import canonical_pitch_call, canonical_pitch_type

_GAME_RE = re.compile(r"^(?P<year>\d{4})-(?P<kind>[A-Za-z]+)-(?P<number>\d+)$")


def _dig(value: Any, *path: str) -> Any:
    current = value
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _first(mapping: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in mapping and mapping[name] not in (None, ""):
            return mapping[name]
    return None


def _int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _text(value: Any) -> str | None:
    return None if value in (None, "") else str(value)


def canonical_game_pk(game_id: str) -> int:
    match = _GAME_RE.match(game_id)
    if match:
        year = int(match.group("year"))
        kind = match.group("kind").upper()
        number = int(match.group("number"))
        kind_code = sum((index + 1) * ord(ch) for index, ch in enumerate(kind)) % 900 + 1
        return year * 10_000_000 + kind_code * 10_000 + number
    digest = hashlib.sha256(game_id.encode("utf-8")).hexdigest()
    return int(digest[:14], 16) % 9_000_000_000_000_000


def _game_date(game: dict[str, Any], fallback: date | None) -> str:
    raw = _first(game, "PreExeDate", "ExeDate", "GameDate", "Date")
    if raw:
        return str(raw).split("T", 1)[0].replace("/", "-")
    if fallback:
        return fallback.isoformat()
    match = _GAME_RE.match(str(game.get("GameId") or ""))
    return f"{match.group('year')}-01-01" if match else "1970-01-01"


def _is_pitch(log: dict[str, Any]) -> bool:
    trackman = log.get("Trackman")
    if not isinstance(trackman, dict) or not trackman:
        return False
    tag = _dig(trackman, "Play", "PitchTag")
    release = _dig(trackman, "Pitch", "Release")
    location = _dig(trackman, "Pitch", "Location")
    return any(isinstance(value, dict) and bool(value) for value in (tag, release, location))


def _estimated_zone(side: float | None, height: float | None) -> int | None:
    if side is None or height is None:
        return None
    half_width, bottom, top = 0.215, 0.46, 1.07
    if not (-half_width <= side <= half_width and bottom <= height <= top):
        return 11
    col = min(2, max(0, int((side + half_width) / ((2 * half_width) / 3))))
    row_from_bottom = min(2, max(0, int((height - bottom) / ((top - bottom) / 3))))
    return (2 - row_from_bottom) * 3 + col + 1


def normalize_game(game: dict[str, Any], *, fallback_date: date | None = None) -> list[dict[str, Any]]:
    game_id = str(game.get("GameId") or game.get("gameId") or "")
    if not game_id:
        raise ValueError("CPBL game payload has no GameId")
    game_date = _game_date(game, fallback_date)
    try:
        game_year = int(game_date[:4])
    except ValueError:
        game_year = None
    game_pk = canonical_game_pk(game_id)
    kind_code = _text(_first(game, "KindCode", "GameKind", "kindCode"))
    field = game.get("Field") if isinstance(game.get("Field"), dict) else {}

    rows: list[dict[str, Any]] = []
    pa_index = 0
    pitch_in_pa = 0
    previous_matchup: tuple[Any, Any] | None = None
    previous_count: tuple[int | None, int | None] | None = None

    for source_index, log in enumerate(game.get("LiveLog") or [], 1):
        if not isinstance(log, dict) or not _is_pitch(log):
            continue
        pitcher_acnt = _text(_first(log, "PitcherAcnt", "PitcherAccount", "PitcherId"))
        batter_acnt = _text(_first(log, "HitterAcnt", "BatterAcnt", "HitterAccount", "BatterId"))
        matchup = (pitcher_acnt, batter_acnt)
        balls = _int(_first(log, "BallCnt", "Balls"))
        strikes = _int(_first(log, "StrikeCnt", "Strikes"))
        current_count = (balls, strikes)

        explicit_pa = _int(_first(log, "AtBatNumber", "AtBatSeq", "BattingActionSeq", "PaSeq"))
        if explicit_pa is not None:
            if explicit_pa != pa_index:
                pa_index = explicit_pa
                pitch_in_pa = 0
        else:
            count_reset = previous_count is not None and current_count == (0, 0) and previous_count != (0, 0)
            if pa_index == 0 or matchup != previous_matchup or count_reset:
                pa_index += 1
                pitch_in_pa = 0

        pitch_in_pa += 1
        trackman = log.get("Trackman") or {}
        tag = _dig(trackman, "Play", "PitchTag") or {}
        pitch = trackman.get("Pitch") if isinstance(trackman.get("Pitch"), dict) else {}
        release = pitch.get("Release") if isinstance(pitch.get("Release"), dict) else {}
        location = pitch.get("Location") if isinstance(pitch.get("Location"), dict) else {}
        trajectory = _dig(pitch, "Flight", "PolyFit", "PitchTrajectory") or {}
        hit = trackman.get("Hit") if isinstance(trackman.get("Hit"), dict) else {}
        launch = hit.get("Launch") if isinstance(hit.get("Launch"), dict) else {}
        contact = launch.get("ContactPosition") if isinstance(launch.get("ContactPosition"), dict) else {}
        landing = hit.get("LandingFlat") if isinstance(hit.get("LandingFlat"), dict) else {}

        plate_x = _float(location.get("PlateLocSide"))
        plate_z = _float(location.get("PlateLocHeight"))
        auto_type = _text(tag.get("AutoPitchType"))
        tagged_type = _text(tag.get("TaggedPitchType"))
        pitch_call = _text(tag.get("PitchCall"))

        rows.append({
            "game_pk": game_pk,
            "game_date": game_date,
            "game_year": game_year,
            "at_bat_number": pa_index,
            "pitch_number": pitch_in_pa,
            "pitcher": _int(pitcher_acnt),
            "batter": _int(batter_acnt),
            "pitch_type": canonical_pitch_type(auto_type, tagged_type),
            "description": canonical_pitch_call(pitch_call),
            "inning": _int(_first(log, "InningSeq", "Inning")),
            "balls": balls,
            "strikes": strikes,
            "outs_when_up": _int(_first(log, "OutCnt", "Outs")),
            "zone": _estimated_zone(plate_x, plate_z),
            "release_speed": _float(release.get("RelSpeed")),
            "release_spin_rate": _float(release.get("SpinRate")),
            "release_extension": _float(release.get("Extension")),
            "release_pos_x": _float(release.get("RelSide")),
            "release_pos_z": _float(release.get("RelHeight")),
            "plate_x": plate_x,
            "plate_z": plate_z,
            "cpbl_game_id": game_id,
            "cpbl_game_kind": kind_code,
            "cpbl_field_no": _text(field.get("No")),
            "cpbl_field_name": _text(field.get("Abbe")),
            "cpbl_source_index": source_index,
            "cpbl_source_pitch_cnt": _int(log.get("PitchCnt")),
            "cpbl_pitcher_acnt": pitcher_acnt,
            "cpbl_pitcher_name": _text(log.get("PitcherName")),
            "cpbl_batter_acnt": batter_acnt,
            "cpbl_batter_name": _text(_first(log, "HitterName", "BatterName")),
            "cpbl_batting_action": _text(log.get("BattingActionName")),
            "cpbl_content": _text(log.get("Content")),
            "cpbl_is_ball": _int(log.get("IsBall")),
            "cpbl_is_strike": _int(log.get("IsStrike")),
            "cpbl_is_score": _int(log.get("IsScoreCnt")),
            "pitch_call": pitch_call,
            "auto_pitch_type": auto_type,
            "tagged_pitch_type": tagged_type,
            "rel_speed_kph": _float(release.get("RelSpeed")),
            "spin_rate": _float(release.get("SpinRate")),
            "extension_m": _float(release.get("Extension")),
            "rel_height_m": _float(release.get("RelHeight")),
            "rel_side_m": _float(release.get("RelSide")),
            "zone_speed_kph": _float(location.get("ZoneSpeed")),
            "horz_appr_angle": _float(location.get("HorzApprAngle")),
            "vert_appr_angle": _float(location.get("VertApprAngle")),
            "traj_x_json": json.dumps(trajectory.get("X"), ensure_ascii=False) if trajectory.get("X") is not None else None,
            "traj_y_json": json.dumps(trajectory.get("Y"), ensure_ascii=False) if trajectory.get("Y") is not None else None,
            "traj_z_json": json.dumps(trajectory.get("Z"), ensure_ascii=False) if trajectory.get("Z") is not None else None,
            "hit_exit_speed_kph": _float(launch.get("ExitSpeed")),
            "hit_launch_angle": _float(launch.get("Angle")),
            "hit_direction": _float(launch.get("Direction")),
            "hit_spin_rate": _float(launch.get("HitSpinRate")),
            "contact_x": _float(contact.get("X")),
            "contact_y": _float(contact.get("Y")),
            "contact_z": _float(contact.get("Z")),
            "land_bearing": _float(landing.get("Bearing")),
            "land_distance_m": _float(landing.get("Distance")),
            "land_hang_time": _float(landing.get("HangTime")),
            "cpbl_zone_estimated": 1 if plate_x is not None and plate_z is not None else 0,
        })
        previous_matchup = matchup
        previous_count = current_count
    return rows


def rows_to_csv(rows: Iterable[dict[str, Any]]) -> bytes:
    rows = list(rows)
    if not rows:
        return b""
    fields: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                fields.append(key)
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: "" if value is None else value for key, value in row.items()})
    return output.getvalue().encode("utf-8")
