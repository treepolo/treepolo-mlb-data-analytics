from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from cpbl_stage7_engineering import _canon, config_for, representative_payloads
from treepolo_mlb_data.datasets import dataset_spec
from treepolo_mlb_data.duckdb_mirror import DuckDBMirror
from treepolo_mlb_data.web_analysis import AnalysisFacade


CASES = (
    "basic",
    "sequence",
    "follow",
    "arsenal",
    "role",
    "temporal",
    "percentile",
    "cross",
    "arsenal_change",
    "workflow",
)


def dump_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def prepare(root: Path) -> dict[str, object]:
    config = config_for(root)
    spec = dataset_spec(config, "cpbl")
    mirror = DuckDBMirror(spec.database_path, spec.analytics_database_path)
    started = time.perf_counter()
    built = mirror.ensure(force_rebuild=True)
    elapsed = time.perf_counter() - started
    report = {"stage": "7E", "operation": "prepare", "mirror": built, "elapsed_seconds": elapsed}
    dump_json(root / "reports" / "7e_parity_prepare.json", report)
    print(json.dumps(report, ensure_ascii=False), flush=True)
    return report


def run_case(root: Path, case: str) -> dict[str, object]:
    config = config_for(root)
    spec = dataset_spec(config, "cpbl")
    if not spec.database_path.exists():
        raise SystemExit("Stage 7E requires the Stage 7D database artifact")

    mirror = DuckDBMirror(spec.database_path, spec.analytics_database_path)
    if not spec.analytics_database_path.exists():
        mirror.ensure(force_rebuild=True)
    else:
        mirror.ensure()

    payloads = dict(representative_payloads(spec.database_path))
    if case not in payloads:
        raise SystemExit(f"Unknown Stage 7E parity case: {case}")
    payload = payloads[case]

    sqlite_facade = AnalysisFacade(
        spec.database_path,
        backend="sqlite",
        dataset_id="cpbl",
        dataset_label="CPBL / Trackman",
        speed_unit="kph",
        distance_unit="m",
    )
    duck_facade = AnalysisFacade(
        spec.database_path,
        spec.analytics_database_path,
        backend="duckdb",
        dataset_id="cpbl",
        dataset_label="CPBL / Trackman",
        speed_unit="kph",
        distance_unit="m",
    )

    print(f"Stage 7E {case}: SQLite start", flush=True)
    started = time.perf_counter()
    sqlite_result = sqlite_facade.analyze(payload)
    sqlite_elapsed = time.perf_counter() - started
    print(f"Stage 7E {case}: SQLite done in {sqlite_elapsed:.3f}s", flush=True)

    print(f"Stage 7E {case}: DuckDB start", flush=True)
    started = time.perf_counter()
    duck_result = duck_facade.analyze(payload)
    duck_elapsed = time.perf_counter() - started
    print(f"Stage 7E {case}: DuckDB done in {duck_elapsed:.3f}s", flush=True)

    same = _canon(sqlite_result) == _canon(duck_result)
    report = {
        "stage": "7E",
        "case": case,
        "same": same,
        "sqlite_rows": sqlite_result.get("row_count"),
        "duckdb_rows": duck_result.get("row_count"),
        "sqlite_elapsed_seconds": sqlite_elapsed,
        "duckdb_elapsed_seconds": duck_elapsed,
    }
    dump_json(root / "reports" / f"7e_parity_{case}.json", report)
    print(json.dumps(report, ensure_ascii=False), flush=True)
    if not same:
        raise SystemExit(f"Stage 7E failed parity: {case}")
    return report


def finalize(root: Path) -> None:
    missing: list[str] = []
    failed: list[str] = []
    reports: list[dict[str, object]] = []
    for case in CASES:
        path = root / "reports" / f"7e_parity_{case}.json"
        if not path.exists():
            missing.append(case)
            continue
        report = json.loads(path.read_text(encoding="utf-8"))
        reports.append(report)
        if not report.get("same"):
            failed.append(case)
    if missing or failed:
        raise SystemExit(f"Stage 7E incomplete: missing={missing}, failed={failed}")

    prepare(root)
    summary = {
        "stage": "7E",
        "cases": reports,
        "case_count": len(reports),
        "all_equal": True,
        "max_sqlite_elapsed_seconds": max(float(r["sqlite_elapsed_seconds"]) for r in reports),
        "max_duckdb_elapsed_seconds": max(float(r["duckdb_elapsed_seconds"]) for r in reports),
    }
    dump_json(root / "reports" / "7e_parity.json", summary)
    print(json.dumps(summary, ensure_ascii=False), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one isolated CPBL Stage 7 SQLite/DuckDB parity case")
    parser.add_argument("--root", type=Path, default=Path("stage7_work"))
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--case", choices=CASES)
    group.add_argument("--prepare", action="store_true")
    group.add_argument("--finalize", action="store_true")
    args = parser.parse_args()

    if args.prepare:
        prepare(args.root)
    elif args.finalize:
        finalize(args.root)
    else:
        run_case(args.root, args.case)


if __name__ == "__main__":
    main()
