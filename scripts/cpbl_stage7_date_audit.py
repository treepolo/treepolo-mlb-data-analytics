from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from treepolo_mlb_data.config import AppConfig
from treepolo_mlb_data.cpbl_raw import CPBLRawArchive
from treepolo_mlb_data.cpbl_sync import _raw_canonical_game_dates
from treepolo_mlb_data.datasets import dataset_spec


def dump_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def run(root: Path) -> None:
    config = AppConfig(data_dir=str(root / "data"), analysis_backend="duckdb")
    spec = dataset_spec(config, "cpbl")
    archive = CPBLRawArchive(spec.raw_root)
    expected_dates = _raw_canonical_game_dates(archive)

    acquisition_path = root / "reports" / "7a_acquisition.json"
    acquisition = json.loads(acquisition_path.read_text(encoding="utf-8"))
    window_end = str(acquisition["window"]["end"])

    conn = sqlite3.connect(spec.database_path)
    observed = {
        str(row[0]): str(row[1])
        for row in conn.execute(
            "SELECT cpbl_game_id,MIN(game_date) FROM pitches GROUP BY cpbl_game_id"
        )
        if row[0] is not None and row[1] is not None
    }
    multiple_dates = [
        {
            "game_id": str(row[0]),
            "distinct_game_dates": int(row[1]),
            "min_game_date": str(row[2]),
            "max_game_date": str(row[3]),
        }
        for row in conn.execute(
            "SELECT cpbl_game_id,COUNT(DISTINCT game_date),MIN(game_date),MAX(game_date) "
            "FROM pitches GROUP BY cpbl_game_id HAVING COUNT(DISTINCT game_date)<>1"
        )
    ]
    out_of_window = [
        {"game_id": str(row[0]), "game_date": str(row[1]), "pitches": int(row[2])}
        for row in conn.execute(
            "SELECT cpbl_game_id,game_date,COUNT(*) FROM pitches "
            "WHERE game_date>? GROUP BY cpbl_game_id,game_date ORDER BY game_date,cpbl_game_id",
            (window_end,),
        )
    ]
    conn.close()

    mismatches = []
    for game_id, observed_date in sorted(observed.items()):
        expected_date = expected_dates.get(game_id)
        if expected_date != observed_date:
            mismatches.append(
                {
                    "game_id": game_id,
                    "expected_schedule_play_date": expected_date,
                    "observed_database_game_date": observed_date,
                }
            )

    report = {
        "stage": "7B-date-semantics",
        "normalized_games": len(observed),
        "schedule_date_mappings": len(expected_dates),
        "acquisition_window_end": window_end,
        "game_date_mismatches": mismatches,
        "multiple_game_dates_per_game": multiple_dates,
        "out_of_window_games": out_of_window,
        "passed": not mismatches and not multiple_dates and not out_of_window,
    }
    dump_json(root / "reports" / "7b_date_semantics.json", report)

    failures = []
    if mismatches:
        failures.append(f"schedule-vs-db game date mismatches={len(mismatches)}")
    if multiple_dates:
        failures.append(f"games with multiple canonical game dates={len(multiple_dates)}")
    if out_of_window:
        failures.append(f"games beyond acquisition window={len(out_of_window)}")
    if failures:
        raise SystemExit("Stage 7B date semantics failed: " + "; ".join(failures))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("stage7_work"))
    args = parser.parse_args()
    run(args.root)
