from __future__ import annotations

import argparse
import hashlib
import json
import os
import resource
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.request
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Any

from treepolo_mlb_data.benchmark import run_benchmark
from treepolo_mlb_data.config import AppConfig, save_config
from treepolo_mlb_data.cpbl_client import CPBLClient
from treepolo_mlb_data.cpbl_raw import CPBLRawArchive
from treepolo_mlb_data.cpbl_sync import CPBLSyncEngine
from treepolo_mlb_data.dataset_webapp import DatasetAppServices
from treepolo_mlb_data.datasets import dataset_config, dataset_spec
from treepolo_mlb_data.duckdb_mirror import DuckDBMirror
from treepolo_mlb_data.fast_status import prepare_fast_status
from treepolo_mlb_data.storage import StatcastStore
from treepolo_mlb_data.web_analysis import AnalysisFacade


def dump_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def config_for(root: Path, backend: str = "duckdb") -> AppConfig:
    return AppConfig(
        data_dir=str(root / "data"),
        analysis_backend=backend,
        request_timeout_seconds=90,
        request_retries=4,
        request_backoff_seconds=1.5,
        request_pause_seconds=0.15,
    )


def engine_for(config: AppConfig) -> tuple[Any, CPBLSyncEngine]:
    spec = dataset_spec(config, "cpbl")
    client = CPBLClient(
        config.request_timeout_seconds,
        config.request_retries,
        config.request_backoff_seconds,
        config.request_pause_seconds,
    )
    engine = CPBLSyncEngine(
        spec.database_path,
        spec.raw_root,
        client,
        analytics_database_path=spec.analytics_database_path,
        recent_refresh_days=spec.recent_refresh_days,
        auto_update_interval_hours=config.auto_update_interval_hours,
    )
    return spec, engine


def db_fingerprint(path: Path) -> dict[str, Any]:
    with sqlite3.connect(path) as conn:
        rows = conn.execute('SELECT pitch_uid,_row_hash FROM pitches ORDER BY pitch_uid').fetchall()
        schema = [(r[1], r[2]) for r in conn.execute('PRAGMA table_info(pitches)')]
        games = int(conn.execute('SELECT COUNT(DISTINCT game_pk) FROM pitches').fetchone()[0])
    digest = hashlib.sha256()
    for uid, row_hash in rows:
        digest.update(str(uid).encode())
        digest.update(b"\0")
        digest.update(str(row_hash).encode())
        digest.update(b"\n")
    return {"pitch_rows": len(rows), "games": games, "pitch_row_hash_sha256": digest.hexdigest(), "schema": schema}


def copy_raw(src_cpbl_root: Path, dst_cpbl_root: Path) -> None:
    src = src_cpbl_root / "raw"
    dst = dst_cpbl_root / "raw"
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)


def lifecycle(root: Path) -> None:
    config = config_for(root)
    spec, _ = engine_for(config)
    if not spec.database_path.exists():
        raise SystemExit("Stage 7D requires the Stage 7A artifact")
    before = db_fingerprint(spec.database_path)
    archive = CPBLRawArchive(spec.raw_root)
    verified = 0
    for path in archive.iter_games():
        archive.read_verified(path)
        verified += 1
    if not verified:
        raise SystemExit("Stage 7D failed: no raw game snapshots exist")

    corruption_detected = False
    first_game = next(iter(archive.iter_games()))
    temp_gz = first_game.with_name("__stage7_corrupt__.json.gz")
    temp_manifest = temp_gz.with_suffix(".manifest.json")
    shutil.copy2(first_game, temp_gz)
    shutil.copy2(first_game.with_suffix(".manifest.json"), temp_manifest)
    raw = bytearray(temp_gz.read_bytes())
    raw[len(raw) // 2] ^= 0x01
    temp_gz.write_bytes(bytes(raw))
    try:
        archive.read_verified(temp_gz)
    except Exception:
        corruption_detected = True
    finally:
        temp_gz.unlink(missing_ok=True)
        temp_manifest.unlink(missing_ok=True)

    rebuild_root = root / "rebuild_check"
    shutil.rmtree(rebuild_root, ignore_errors=True)
    rebuild_config = config_for(rebuild_root)
    rebuild_spec, rebuild_engine = engine_for(rebuild_config)
    rebuild_spec.root.mkdir(parents=True, exist_ok=True)
    copy_raw(spec.root, rebuild_spec.root)
    first_rebuild = rebuild_engine.rebuild_from_raw()
    after = db_fingerprint(rebuild_spec.database_path)
    second_rebuild = rebuild_engine.rebuild_from_raw()

    stable_day = date(2026, 5, 10)
    stable_first = rebuild_engine.backfill(stable_day, stable_day, continue_on_error=False, resume=False)
    stable_second = rebuild_engine.backfill(stable_day, stable_day, continue_on_error=False, resume=False)

    class FailOneDate:
        def __init__(self, delegate: CPBLClient, fail_day: date):
            self.delegate = delegate
            self.fail_day = fail_day
        def schedule(self, day):
            resolved = day if isinstance(day, date) else date.fromisoformat(str(day))
            if resolved == self.fail_day:
                raise RuntimeError("intentional Stage 7 interruption")
            return self.delegate.schedule(day)
        def game(self, game_id):
            return self.delegate.game(game_id)

    resume_root = root / "resume_check"
    shutil.rmtree(resume_root, ignore_errors=True)
    resume_config = config_for(resume_root)
    resume_spec = dataset_spec(resume_config, "cpbl")
    delegate = CPBLClient(pause_seconds=0.05)
    interrupted_engine = CPBLSyncEngine(
        resume_spec.database_path, resume_spec.raw_root,
        FailOneDate(delegate, date(2026, 5, 11)),
    )
    interrupted_stats = interrupted_engine.backfill(
        date(2026, 5, 10), date(2026, 5, 11), continue_on_error=True, resume=False
    )
    resumed_engine = CPBLSyncEngine(resume_spec.database_path, resume_spec.raw_root, delegate)
    resumed_stats = resumed_engine.backfill(
        date(2026, 5, 10), date(2026, 5, 11), continue_on_error=False, resume=True
    )
    with StatcastStore(resume_spec.database_path) as store:
        day10_success = store.has_successful_chunk("2026-05-10", "2026-05-10")
        day11_success = store.has_successful_chunk("2026-05-11", "2026-05-11")

    scheduler_called = {"count": 0}
    original_update = rebuild_engine.update
    def stable_update(*args, **kwargs):
        scheduler_called["count"] += 1
        return rebuild_engine.backfill(stable_day, stable_day, continue_on_error=False, resume=False)
    rebuild_engine.update = stable_update  # type: ignore[method-assign]
    with StatcastStore(rebuild_spec.database_path) as store:
        store.set_setting("auto_update_enabled", "true")
    rebuild_engine.scheduler(stop_after_one=True)
    rebuild_engine.update = original_update  # type: ignore[method-assign]

    report = {
        "stage": "7D",
        "raw_game_snapshots_verified": verified,
        "raw_corruption_detection": corruption_detected,
        "before": before,
        "after_rebuild": after,
        "first_rebuild": asdict(first_rebuild),
        "second_rebuild": asdict(second_rebuild),
        "stable_day_first": asdict(stable_first),
        "stable_day_second": asdict(stable_second),
        "interrupted": asdict(interrupted_stats),
        "resumed": asdict(resumed_stats),
        "resume_day10_success": day10_success,
        "resume_day11_success": day11_success,
        "scheduler_update_calls": scheduler_called["count"],
    }
    dump_json(root / "reports" / "7d_lifecycle.json", report)

    blockers = []
    if before != after:
        blockers.append("raw rebuild fingerprint differs from acquired database")
    if not corruption_detected:
        blockers.append("raw corruption was not detected")
    if stable_second.inserted or stable_second.updated:
        blockers.append(f"repeat stable-day ingest changed rows: {asdict(stable_second)}")
    if interrupted_stats.errors != 1 or not (day10_success and day11_success):
        blockers.append("interruption/resume lifecycle did not recover both date chunks")
    if scheduler_called["count"] != 1:
        blockers.append("scheduler --once path did not invoke exactly one update")
    if blockers:
        raise SystemExit("Stage 7D failed: " + "; ".join(blockers))


def _canon(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 8)
    if isinstance(value, dict):
        ignored = {"backend", "input_backend", "query", "sql", "elapsed_seconds"}
        return {k: _canon(v) for k, v in sorted(value.items()) if k not in ignored}
    if isinstance(value, list):
        converted = [_canon(v) for v in value]
        if converted and all(isinstance(v, dict) for v in converted):
            return sorted(converted, key=lambda v: json.dumps(v, sort_keys=True, ensure_ascii=False))
        return converted
    return value


def representative_payloads(path: Path) -> list[tuple[str, dict[str, Any]]]:
    with sqlite3.connect(path) as conn:
        pitch_types = [str(r[0]) for r in conn.execute(
            "SELECT pitch_type FROM pitches WHERE pitch_type IS NOT NULL GROUP BY pitch_type ORDER BY COUNT(*) DESC"
        )]
        min_date, max_date = conn.execute("SELECT MIN(game_date),MAX(game_date) FROM pitches").fetchone()
    if not pitch_types:
        raise RuntimeError("No canonical pitch types exist")
    target = "ST" if "ST" in pitch_types else next((p for p in pitch_types if p not in {"FF", "SI"}), pitch_types[0])
    filters = [{"field": "game_year", "op": "eq", "value": 2026}]
    return [
        ("basic", {"mode":"basic","filters":filters,"group_by":["pitch_type"],"metrics":[{"function":"count"},{"function":"avg","field":"release_speed"}],"result_sort":[{"field":"pitch_type"}],"limit":5000}),
        ("sequence", {"mode":"sequence_pattern","filters":filters,"event":{"field":"pitch_type","op":"eq","value":target},"occurrence":1,"arrangement":"any","result_sort":[{"field":"game_pk"},{"field":"at_bat_number"},{"field":"pitch_number"}]}),
        ("follow", {"mode":"follow_event","filters":filters,"anchor":{"field":"pitch_type","op":"eq","value":target},"target":{"field":"pitch_type","op":"eq","value":target},"between":[{"field":"pitch_type","op":"eq","value":"FF"}],"max_gap":3,"result_sort":[{"field":"game_pk"},{"field":"at_bat_number"},{"field":"pitch_number"}]}),
        ("arsenal", {"mode":"arsenal","filters":filters,"entity_fields":["pitcher"],"min_usage":0.05,"tie_method":"dense_rank","result_sort":[{"field":"pitcher"},{"field":"role_rank"}]}),
        ("role", {"mode":"pitch_role","filters":filters,"entity_fields":["pitcher"],"metric_kind":"usage_rate","rank":1,"descending":True,"exclude_pitch_types":["FF"],"tie_method":"row_number","result_sort":[{"field":"pitcher"},{"field":"pitch_type"}]}),
        ("temporal", {"mode":"temporal","filters":filters,"entity_fields":["pitcher","pitch_type"],"period_field":"game_pk","value_field":"release_speed","function":"avg","direction":"previous","offset":1,"result_sort":[{"field":"pitcher"},{"field":"pitch_type"},{"field":"game_pk"}]}),
        ("percentile", {"mode":"percentile","filters":filters,"entity_fields":["pitcher"],"value_field":"release_speed","threshold":0.8,"side":"high","result_sort":[{"field":"pitcher"},{"field":"game_pk"},{"field":"at_bat_number"},{"field":"pitch_number"}]}),
        ("cross", {"mode":"cross_level","filters":[*filters,{"field":"pitch_type","op":"eq","value":"FF"}],"unit_fields":["pitcher","game_pk"],"baseline_fields":["pitcher"],"value_field":"release_speed","function":"avg","result_sort":[{"field":"pitcher"},{"field":"game_pk"}]}),
        ("arsenal_change", {"mode":"arsenal_change","filters":filters,"entity_fields":["pitcher"],"min_usage":0.05,"period_a":{"start":str(min_date),"end":"2026-06-30"},"period_b":{"start":"2026-07-01","end":str(max_date)},"result_sort":[{"field":"pitcher"},{"field":"pitch_type"}]}),
        ("workflow", {"mode":"workflow","filters":filters,"stages":[{"kind":"aggregate","group_by":["pitcher","pitch_type"],"metrics":[{"function":"count","alias":"pitch_count"},{"function":"avg","field":"vert_appr_angle","alias":"avg_vaa"}]},{"kind":"rank","partition_by":["pitcher"],"order_by":[{"field":"pitch_count","descending":True}],"method":"dense_rank","alias":"usage_rank"},{"kind":"sort","order_by":[{"field":"pitcher"},{"field":"pitch_type"}]}],"limit":5000}),
    ]


def parity(root: Path) -> None:
    config = config_for(root)
    spec = dataset_spec(config, "cpbl")
    mirror = DuckDBMirror(spec.database_path, spec.analytics_database_path)
    full = mirror.ensure(force_rebuild=True)
    noop = mirror.ensure()
    sqlite_facade = AnalysisFacade(spec.database_path, backend="sqlite", dataset_id="cpbl", dataset_label="CPBL / Trackman", speed_unit="kph", distance_unit="m")
    duck_facade = AnalysisFacade(spec.database_path, spec.analytics_database_path, backend="duckdb", dataset_id="cpbl", dataset_label="CPBL / Trackman", speed_unit="kph", distance_unit="m")
    checks = []
    failures = []
    for name, payload in representative_payloads(spec.database_path):
        sqlite_result = sqlite_facade.analyze(payload)
        duck_result = duck_facade.analyze(payload)
        same = _canon(sqlite_result) == _canon(duck_result)
        checks.append({
            "name": name,
            "same": same,
            "sqlite_rows": sqlite_result.get("row_count"),
            "duckdb_rows": duck_result.get("row_count"),
        })
        if not same:
            failures.append(name)
    dump_json(root / "reports" / "7e_parity.json", {
        "stage": "7E",
        "mirror_full_build": full,
        "mirror_noop_refresh": noop,
        "checks": checks,
    })
    if failures:
        raise SystemExit("Stage 7E failed parity: " + ", ".join(failures))


def wait_http(url: str, timeout: float = 30.0) -> float:
    started = time.perf_counter()
    deadline = started + timeout
    while time.perf_counter() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return time.perf_counter() - started
        except Exception:
            time.sleep(0.15)
    raise RuntimeError(f"UI did not become ready: {url}")


def performance(root: Path) -> None:
    config = config_for(root)
    spec = dataset_spec(config, "cpbl")
    prepare_fast_status(spec.database_path)
    bench = run_benchmark(dataset_config(config, "cpbl"), year=2026, runs=3, backend="both")  # type: ignore[arg-type]

    services = DatasetAppServices(config, "cpbl")
    payload = representative_payloads(spec.database_path)[0][1]
    started = time.perf_counter(); first = services.analyze(payload); first_seconds = time.perf_counter() - started
    started = time.perf_counter(); second = services.analyze(payload); second_seconds = time.perf_counter() - started
    history = services.history(10)
    saved = services.save_analysis({
        "name": "Stage 7 cache acceptance",
        "notes": "automated Stage 7F",
        "analysis_payload": payload,
        "cache_key": second.get("cache", {}).get("key"),
        "data_revision": second.get("cache", {}).get("data_revision"),
    })
    loaded_saved = services.saved_analysis(int(saved["id"]))
    services.analysis_state.close()

    config_path = root / "stage7_config.json"
    save_config(config_path, config)
    proc = subprocess.Popen([
        sys.executable, "-m", "treepolo_mlb_data.cli", "--config", str(config_path), "--dataset", "cpbl",
        "ui", "--host", "127.0.0.1", "--port", "8877", "--no-browser",
    ], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env={**os.environ, "PYTHONUNBUFFERED": "1"})
    try:
        startup_seconds = wait_http("http://127.0.0.1:8877/api/meta", 45)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()

    report = {
        "stage": "7F",
        "benchmark": bench,
        "cache": {
            "first_seconds": first_seconds,
            "second_seconds": second_seconds,
            "first_hit": first.get("cache", {}).get("hit"),
            "second_hit": second.get("cache", {}).get("hit"),
            "history_items": len(history),
            "saved_loaded": bool(loaded_saved),
        },
        "ui_startup_seconds": startup_seconds,
        "process_max_rss_kb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
    }
    dump_json(root / "reports" / "7f_performance.json", report)
    blockers = []
    if first.get("cache", {}).get("hit"):
        blockers.append("first Stage 7F request unexpectedly reused prior cache")
    if not second.get("cache", {}).get("hit"):
        blockers.append("identical second analysis was not a persistent-cache hit")
    if not history or not loaded_saved:
        blockers.append("history/save/reload lifecycle failed")
    if blockers:
        raise SystemExit("Stage 7F failed: " + "; ".join(blockers))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("lifecycle", "parity", "performance"))
    parser.add_argument("--root", type=Path, default=Path("stage7_work"))
    args = parser.parse_args()
    if args.command == "lifecycle":
        lifecycle(args.root)
    elif args.command == "parity":
        parity(args.root)
    else:
        performance(args.root)


if __name__ == "__main__":
    main()
