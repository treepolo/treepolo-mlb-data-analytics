from __future__ import annotations

import argparse
import csv
import json
import shutil
import sqlite3
from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Any

from treepolo_mlb_data.config import AppConfig
from treepolo_mlb_data.cpbl_client import CPBLClient
from treepolo_mlb_data.cpbl_normalize import normalize_game
from treepolo_mlb_data.cpbl_raw import CPBLRawArchive
from treepolo_mlb_data.cpbl_semantics import canonical_pitch_call, canonical_pitch_type, semantic_value_sets
from treepolo_mlb_data.cpbl_sync import CPBLSyncEngine
from treepolo_mlb_data.datasets import dataset_spec
from treepolo_mlb_data.storage import StatcastStore


NUMERIC_FIELDS = (
    "release_speed", "release_spin_rate", "release_extension", "release_pos_x", "release_pos_z",
    "plate_x", "plate_z", "rel_speed_kph", "spin_rate", "extension_m", "rel_height_m", "rel_side_m",
    "zone_speed_kph", "horz_appr_angle", "vert_appr_angle", "hit_exit_speed_kph", "hit_launch_angle",
    "hit_direction", "hit_spin_rate", "contact_x", "contact_y", "contact_z", "land_bearing",
    "land_distance_m", "land_hang_time",
)


def dump_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def make_config(root: Path) -> AppConfig:
    return AppConfig(
        data_dir=str(root / "data"),
        analysis_backend="duckdb",
        request_timeout_seconds=90,
        request_retries=4,
        request_backoff_seconds=1.5,
        request_pause_seconds=0.15,
    )


def make_engine(config: AppConfig) -> tuple[Any, CPBLSyncEngine]:
    spec = dataset_spec(config, "cpbl")
    client = CPBLClient(
        config.request_timeout_seconds,
        config.request_retries,
        config.request_backoff_seconds,
        config.request_pause_seconds,
    )
    return spec, CPBLSyncEngine(
        spec.database_path,
        spec.raw_root,
        client,
        analytics_database_path=spec.analytics_database_path,
        recent_refresh_days=spec.recent_refresh_days,
        auto_update_interval_hours=config.auto_update_interval_hours,
    )


def acquire(root: Path, start: date, end: date, clean: bool) -> None:
    if clean and root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)
    config = make_config(root)
    spec, engine = make_engine(config)
    spec.root.mkdir(parents=True, exist_ok=True)
    stats = engine.backfill(start, end, continue_on_error=True, resume=False)
    with StatcastStore(spec.database_path) as store:
        verify = store.verify()
        store.optimize()
        runs = [dict(row) for row in store.conn.execute(
            "SELECT id,kind,start_date,end_date,status,rows_received,rows_inserted,rows_updated,error FROM sync_runs ORDER BY id"
        )]
    result = {
        "stage": "7A",
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "stats": asdict(stats),
        "verify": verify,
        "runs": runs,
        "database_path": str(spec.database_path),
    }
    dump_json(root / "reports" / "7a_acquisition.json", result)
    if stats.errors:
        raise SystemExit(f"Stage 7A failed: {stats.errors} date chunks had source/API errors")
    if not spec.database_path.exists():
        raise SystemExit("Stage 7A failed: normalized CPBL database was not created")


def latest_raw_games(archive: CPBLRawArchive) -> dict[str, tuple[Any, dict[str, Any]]]:
    selected: dict[str, tuple[Any, dict[str, Any]]] = {}
    for path in archive.iter_games():
        snapshot, payload = archive.read_verified(path)
        if not isinstance(payload, dict):
            continue
        game_id = str(payload.get("GameId") or payload.get("gameId") or "")
        if not game_id:
            continue
        old = selected.get(game_id)
        if old is None or snapshot.fetched_at > old[0].fetched_at:
            selected[game_id] = (snapshot, payload)
    return selected


def schedule_games(archive: CPBLRawArchive) -> tuple[set[str], dict[str, str]]:
    game_ids: set[str] = set()
    source_date: dict[str, str] = {}
    schedule_root = archive.root / "schedule"
    if not schedule_root.exists():
        return game_ids, source_date
    for path in sorted(schedule_root.glob("**/*.json.gz")):
        snapshot, payload = archive.read_verified(path)
        if not isinstance(payload, list):
            continue
        for item in payload:
            if not isinstance(item, dict):
                continue
            game_id = item.get("GameId") or item.get("gameId")
            if game_id not in (None, ""):
                text = str(game_id)
                game_ids.add(text)
                source_date.setdefault(text, snapshot.start_date)
    return game_ids, source_date


def walk_paths(value: Any, prefix: str = ""):
    if isinstance(value, dict):
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            yield path, child
            yield from walk_paths(child, path)
    elif isinstance(value, list):
        for child in value:
            yield from walk_paths(child, prefix + "[]")


def acquisition_window(root: Path) -> tuple[str | None, str | None]:
    path = root / "reports" / "7a_acquisition.json"
    if not path.exists():
        return None, None
    payload = json.loads(path.read_text(encoding="utf-8"))
    window = payload.get("window") if isinstance(payload, dict) else None
    if not isinstance(window, dict):
        return None, None
    start = window.get("start")
    end = window.get("end")
    return (str(start) if start else None, str(end) if end else None)


def audit(root: Path) -> None:
    config = make_config(root)
    spec = dataset_spec(config, "cpbl")
    archive = CPBLRawArchive(spec.raw_root)
    raw_games = latest_raw_games(archive)
    scheduled_ids, source_dates = schedule_games(archive)
    window_start, window_end = acquisition_window(root)

    if not spec.database_path.exists():
        raise SystemExit("Stage 7B/C failed: CPBL database artifact is missing")

    conn = sqlite3.connect(spec.database_path)
    conn.row_factory = sqlite3.Row
    normalized_by_game = {
        str(row[0]): (int(row[1]), int(row[2]))
        for row in conn.execute(
            "SELECT cpbl_game_id, COUNT(*), SUM(COALESCE(cpbl_has_trackman,0)) FROM pitches GROUP BY cpbl_game_id"
        )
    }
    duplicate_uid = int(conn.execute(
        "SELECT COUNT(*) FROM (SELECT pitch_uid FROM pitches GROUP BY pitch_uid HAVING COUNT(*)>1)"
    ).fetchone()[0])
    min_game_date, max_game_date = conn.execute("SELECT MIN(game_date),MAX(game_date) FROM pitches").fetchone()
    out_of_window_rows = 0
    out_of_window_games: list[dict[str, Any]] = []
    if window_end:
        out_of_window_rows = int(
            conn.execute("SELECT COUNT(*) FROM pitches WHERE game_date > ?", (window_end,)).fetchone()[0]
        )
        out_of_window_games = [
            {"game_id": str(row[0]), "game_date": str(row[1]), "pitches": int(row[2])}
            for row in conn.execute(
                "SELECT cpbl_game_id,game_date,COUNT(*) FROM pitches WHERE game_date > ? GROUP BY cpbl_game_id,game_date ORDER BY game_date,cpbl_game_id",
                (window_end,),
            )
        ]

    reconciliation: list[dict[str, Any]] = []
    auto_values: Counter[str] = Counter()
    tagged_values: Counter[str] = Counter()
    call_values: Counter[str] = Counter()
    game_status_values: Counter[str] = Counter()
    game_kind_values: Counter[str] = Counter()
    raw_path_types: dict[str, Counter[str]] = defaultdict(Counter)

    known_pitch = set(semantic_value_sets()["pitch_type"])
    known_calls = set(semantic_value_sets()["description"])
    total_identifiable = 0
    total_trackman = 0
    mismatches = 0

    for game_id in sorted(set(scheduled_ids) | set(raw_games)):
        raw_entry = raw_games.get(game_id)
        game = raw_entry[1] if raw_entry else None
        rows = normalize_game(
            game,
            fallback_date=date.fromisoformat(source_dates.get(game_id, "2026-01-01")),
        ) if game else []
        identifiable = len(rows)
        normalized, tracked = normalized_by_game.get(game_id, (0, 0))
        total_identifiable += identifiable
        total_trackman += tracked
        if identifiable != normalized:
            mismatches += 1

        for row in rows:
            if row.get("auto_pitch_type") not in (None, ""):
                auto_values[str(row["auto_pitch_type"])] += 1
            if row.get("tagged_pitch_type") not in (None, ""):
                tagged_values[str(row["tagged_pitch_type"])] += 1
            if row.get("pitch_call") not in (None, ""):
                call_values[str(row["pitch_call"])] += 1

        if game:
            for status_key in ("GameStatus", "Status", "GameStatusName"):
                value = game.get(status_key)
                if value not in (None, ""):
                    game_status_values[str(value)] += 1
            for kind_key in ("KindCode", "GameKind", "kindCode"):
                value = game.get(kind_key)
                if value not in (None, ""):
                    game_kind_values[str(value)] += 1
            for raw_path, value in walk_paths(game):
                raw_path_types[raw_path][type(value).__name__] += 1

        unknown_auto = sum(
            1
            for row in rows
            if row.get("auto_pitch_type")
            and canonical_pitch_type(row.get("auto_pitch_type"), row.get("tagged_pitch_type")) not in known_pitch
        )
        unknown_tagged = sum(
            1
            for row in rows
            if not row.get("auto_pitch_type")
            and row.get("tagged_pitch_type")
            and canonical_pitch_type(None, row.get("tagged_pitch_type")) not in known_pitch
        )
        unknown_call = sum(
            1
            for row in rows
            if row.get("pitch_call") and canonical_pitch_call(row.get("pitch_call")) not in known_calls
        )
        reconciliation.append({
            "game_id": game_id,
            "source_date": source_dates.get(game_id),
            "source_status": next(
                (str(game.get(k)) for k in ("GameStatusName", "GameStatus", "Status") if game and game.get(k) not in (None, "")),
                None,
            ),
            "raw_schedule_present": game_id in scheduled_ids,
            "raw_game_present": bool(raw_entry),
            "live_log_entries": len(game.get("LiveLog") or []) if game else 0,
            "identifiable_pitches": identifiable,
            "normalized_pitches": normalized,
            "trackman_pitches": tracked,
            "missing_trackman_pitches": max(0, normalized - tracked),
            "duplicate_pitch_uid": 0,
            "unknown_auto_pitch_type": unknown_auto,
            "unknown_tagged_pitch_type": unknown_tagged,
            "unknown_pitch_call": unknown_call,
        })

    report_dir = root / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    columns = list(reconciliation[0].keys()) if reconciliation else []
    with (report_dir / "7b_reconciliation.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(reconciliation)

    numeric_coverage: dict[str, Any] = {}
    total_rows = int(conn.execute("SELECT COUNT(*) FROM pitches").fetchone()[0])
    db_columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(pitches)")}
    for field in NUMERIC_FIELDS:
        if field not in db_columns:
            numeric_coverage[field] = {"available": False}
            continue
        non_null, minimum, maximum = conn.execute(
            f'SELECT COUNT("{field}"), MIN("{field}"), MAX("{field}") FROM pitches'
        ).fetchone()
        sample = [
            row[0]
            for row in conn.execute(f'SELECT "{field}" FROM pitches WHERE "{field}" IS NOT NULL LIMIT 5')
        ]
        numeric_coverage[field] = {
            "available": True,
            "non_null": int(non_null),
            "null": total_rows - int(non_null),
            "missing_rate": (total_rows - int(non_null)) / total_rows if total_rows else None,
            "min": minimum,
            "max": maximum,
            "sample": sample,
        }

    canonical_pitch_counts = {
        str(r[0]): int(r[1])
        for r in conn.execute("SELECT pitch_type,COUNT(*) FROM pitches GROUP BY pitch_type ORDER BY 2 DESC")
        if r[0] is not None
    }
    canonical_call_counts = {
        str(r[0]): int(r[1])
        for r in conn.execute("SELECT description,COUNT(*) FROM pitches GROUP BY description ORDER BY 2 DESC")
        if r[0] is not None
    }
    conn.close()

    unknown_auto_values = sorted(v for v in auto_values if canonical_pitch_type(v, None) not in known_pitch)
    unknown_tagged_values = sorted(v for v in tagged_values if canonical_pitch_type(None, v) not in known_pitch)
    unknown_call_values = sorted(v for v in call_values if canonical_pitch_call(v) not in known_calls)

    summary = {
        "stage": "7B/7C",
        "acquisition_window": {"start": window_start, "end": window_end},
        "database_game_date_min": min_game_date,
        "database_game_date_max": max_game_date,
        "out_of_window_pitch_rows": out_of_window_rows,
        "out_of_window_games": out_of_window_games,
        "schedule_games": len(scheduled_ids),
        "raw_games": len(raw_games),
        "normalized_games": len(normalized_by_game),
        "normalized_pitches": total_rows,
        "identifiable_pitches_from_latest_raw": total_identifiable,
        "trackman_pitches": total_trackman,
        "pitches_without_trackman": total_rows - total_trackman,
        "duplicate_pitch_uid": duplicate_uid,
        "game_reconciliation_mismatches": mismatches,
        "games_missing_raw_payload": sorted(scheduled_ids - set(raw_games)),
    }
    dump_json(report_dir / "7b_summary.json", summary)
    dump_json(report_dir / "7c_field_census.json", {
        "auto_pitch_type": dict(auto_values),
        "tagged_pitch_type": dict(tagged_values),
        "pitch_call": dict(call_values),
        "canonical_pitch_type": canonical_pitch_counts,
        "canonical_description": canonical_call_counts,
        "unknown_auto_pitch_type_values": unknown_auto_values,
        "unknown_tagged_pitch_type_values": unknown_tagged_values,
        "unknown_pitch_call_values": unknown_call_values,
        "game_status_values": dict(game_status_values),
        "game_kind_values": dict(game_kind_values),
        "numeric_coverage": numeric_coverage,
        "raw_field_paths": {key: dict(value) for key, value in sorted(raw_path_types.items())},
        "source_unavailable_savant_fields": [
            "pfx_x", "pfx_z", "estimated_woba_using_speedangle", "estimated_ba_using_speedangle",
            "launch_speed_angle", "spin_axis", "api_break_z_with_gravity", "api_break_x_arm",
        ],
    })

    blocking = []
    if duplicate_uid:
        blocking.append(f"duplicate pitch_uid groups={duplicate_uid}")
    if mismatches:
        blocking.append(f"raw-vs-normalized pitch count mismatches={mismatches}")
    if scheduled_ids - set(raw_games):
        blocking.append(f"schedule games without raw game payload={len(scheduled_ids - set(raw_games))}")
    if out_of_window_rows:
        blocking.append(
            f"canonical game_date escaped acquisition window end {window_end}: rows={out_of_window_rows}, games={len(out_of_window_games)}"
        )
    if blocking:
        raise SystemExit("Stage 7B/C failed: " + "; ".join(blocking))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("acquire", "audit"))
    parser.add_argument("--root", type=Path, default=Path("stage7_work"))
    parser.add_argument("--start", default="2026-01-01")
    parser.add_argument("--end", default="2026-09-15")
    parser.add_argument("--clean", action="store_true")
    args = parser.parse_args()
    if args.command == "acquire":
        acquire(args.root, date.fromisoformat(args.start), date.fromisoformat(args.end), args.clean)
    else:
        audit(args.root)


if __name__ == "__main__":
    main()
