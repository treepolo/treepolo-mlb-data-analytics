from __future__ import annotations

import argparse
import math
import sqlite3
import time
from pathlib import Path
from typing import Any

import cpbl_stage7_acceptance as base


def question9(facade, conn: sqlite3.Connection) -> dict[str, Any]:
    """Validate the percentile mode against the SQL CUME_DIST definition.

    The product deliberately implements empirical percentile with CUME_DIST.
    For tied values CUME_DIST is (# values <= x) / n, not a mid-rank
    percentile.  The earlier Stage 7 oracle used a midpoint tie rule and could
    therefore reject a correct result when many pitches shared the same rounded
    release speed.
    """
    result = facade.analyze(
        {
            "mode": "percentile",
            "entity_fields": ["pitcher"],
            "value_field": "release_speed",
            "threshold": 0.8,
            "side": "high",
        }
    )
    base.require(bool(result.get("rows")), "#9 percentile result empty")
    row = next((r for r in result["rows"] if r.get("release_speed") is not None), None)
    base.require(row is not None, "#9 no numeric output row")

    values = sorted(
        float(r[0])
        for r in conn.execute(
            "SELECT release_speed FROM pitches WHERE pitcher=? AND release_speed IS NOT NULL",
            (row["pitcher"],),
        )
    )
    value = float(row["release_speed"])
    less = sum(v < value for v in values)
    equal = sum(v == value for v in values)
    manual_cume_dist = (less + equal) / len(values)
    actual = float(row["percentile"])

    base.require(
        manual_cume_dist >= 0.8 - 1e-12,
        "#9 output row is below the requested per-pitcher CUME_DIST threshold",
    )
    base.require(
        math.isclose(actual, manual_cume_dist, rel_tol=1e-9, abs_tol=1e-12),
        f"#9 percentile mismatch: product={actual}, manual_cume_dist={manual_cume_dist}",
    )
    return {
        "rows": result.get("row_count"),
        "spot_check": {
            "pitcher": row["pitcher"],
            "release_speed": value,
            "product_percentile": actual,
            "manual_cume_dist": manual_cume_dist,
            "sample_size": len(values),
            "less": less,
            "equal": equal,
        },
    }


def run(root: Path) -> None:
    config = base.config_for(root)
    spec = base.dataset_spec(config, "cpbl")
    if not spec.database_path.exists():
        raise SystemExit("Stage 7G requires Stage 7A dataset artifact")

    facade = base.facade_for(spec.database_path, spec.analytics_database_path, "duckdb")
    conn = sqlite3.connect(spec.database_path)
    conn.row_factory = sqlite3.Row
    tests: list[dict[str, Any]] = []
    failures: list[int] = []
    target = None
    reference_type = base.reference_fastball_type(conn)
    funcs = [
        base.question1,
        base.question2,
        base.question3,
        base.question4,
        base.question5,
        base.question6,
        base.question7,
        base.question8,
        question9,
        base.question10,
    ]

    for index, fn in enumerate(funcs, 1):
        started = time.perf_counter()
        try:
            if index == 1:
                detail, target = fn(facade, conn, reference_type)
            elif index == 4:
                detail = fn(facade, conn, reference_type)
            elif index == 5:
                detail = fn(facade, reference_type)
            elif index == 6:
                detail = fn(facade, conn, target, reference_type)
            elif index == 7:
                detail = fn(facade, conn, reference_type)
            elif index == 10:
                detail = fn(facade, conn, reference_type)
            elif index in {2, 3, 8, 9}:
                detail = fn(facade, conn)
            else:
                detail = fn(facade)
            tests.append(
                {
                    "number": index,
                    "status": "PASS",
                    "seconds": time.perf_counter() - started,
                    "detail": detail,
                }
            )
        except Exception as exc:
            tests.append(
                {
                    "number": index,
                    "status": "FAIL",
                    "seconds": time.perf_counter() - started,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            failures.append(index)

    conn.close()
    report = {
        "stage": "7G-backend-real-data",
        "percentile_oracle": "cume_dist",
        "reference_fastball_type": reference_type,
        "tests": tests,
        "passed": 10 - len(failures),
        "failed": failures,
    }
    base.dump_json(root / "reports" / "7g_ten_questions_backend.json", report)
    if failures:
        raise SystemExit(
            "Stage 7G backend acceptance failed questions: " + ",".join(map(str, failures))
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("stage7_work"))
    args = parser.parse_args()
    run(args.root)


if __name__ == "__main__":
    main()
