from __future__ import annotations

import argparse
import json
import math
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any

from treepolo_mlb_data.config import AppConfig
from treepolo_mlb_data.datasets import dataset_spec
from treepolo_mlb_data.web_analysis import AnalysisFacade


def dump_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def config_for(root: Path) -> AppConfig:
    return AppConfig(data_dir=str(root / "data"), analysis_backend="duckdb")


def facade_for(path: Path, analytics: Path | None = None, backend: str = "sqlite") -> AnalysisFacade:
    return AnalysisFacade(
        path,
        analytics,
        backend=backend,
        dataset_id="cpbl",
        dataset_label="CPBL / Trackman",
        speed_unit="kph",
        distance_unit="m",
    )


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def result_rows(result: dict[str, Any]) -> list[dict[str, Any]]:
    if isinstance(result.get("rows"), list):
        return result["rows"]
    rows: list[dict[str, Any]] = []
    for section in result.get("sections") or []:
        rows.extend(section.get("rows") or [])
    return rows


def reference_fastball_type(conn: sqlite3.Connection) -> str:
    counts = {
        str(row[0]): int(row[1])
        for row in conn.execute(
            "SELECT pitch_type,COUNT(*) FROM pitches WHERE pitch_type IS NOT NULL GROUP BY pitch_type"
        )
    }
    # CPBL 2026 public Trackman only establishes the coarse class ``fastball``.
    # Keep FF as a compatibility fallback for fixtures/future detailed feeds.
    for candidate in ("fastball", "FF"):
        if counts.get(candidate, 0) > 0:
            return candidate
    raise AssertionError(f"No fastball/reference pitch class is available; observed={sorted(counts)}")


def target_with_two_sequence_cohorts(
    facade: AnalysisFacade,
    conn: sqlite3.Connection,
    reference_type: str,
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    types = [
        str(r[0])
        for r in conn.execute(
            "SELECT pitch_type FROM pitches WHERE pitch_type IS NOT NULL GROUP BY pitch_type ORDER BY COUNT(*) DESC"
        )
    ]
    preferred_order = ("breakingball", "ST", "SL", "CU", "CH", "FC", "FS")
    preferred = [p for p in preferred_order if p in types and p != reference_type]
    preferred += [p for p in types if p != reference_type and p not in preferred]
    for pitch_type in preferred:
        base = {
            "mode": "sequence_pattern",
            "event": {"field": "pitch_type", "op": "eq", "value": pitch_type},
            "occurrence": 3,
            "exact_count": 3,
            "require_last_event": True,
        }
        consecutive = facade.analyze({**base, "arrangement": "consecutive"})
        none_adjacent = facade.analyze({**base, "arrangement": "none_adjacent"})
        if consecutive.get("row_count", len(consecutive.get("rows", []))) and none_adjacent.get(
            "row_count", len(none_adjacent.get("rows", []))
        ):
            return pitch_type, consecutive, none_adjacent
    raise AssertionError("No real CPBL pitch class produced both exact-three consecutive and none-adjacent cohorts")


def pa_sequence(conn: sqlite3.Connection, row: dict[str, Any]) -> list[tuple[int, str | None]]:
    return [
        (int(r[0]), r[1])
        for r in conn.execute(
            "SELECT pitch_number,pitch_type FROM pitches WHERE game_pk=? AND at_bat_number=? ORDER BY pitch_number",
            (row["game_pk"], row["at_bat_number"]),
        )
    ]


def question1(
    facade: AnalysisFacade,
    conn: sqlite3.Connection,
    reference_type: str,
) -> tuple[dict[str, Any], str]:
    pitch_type, consecutive, none_adjacent = target_with_two_sequence_cohorts(facade, conn, reference_type)
    evidence = []
    for label, result in (("consecutive", consecutive), ("none_adjacent", none_adjacent)):
        row = result["rows"][0]
        sequence = pa_sequence(conn, row)
        positions = [pn for pn, pt in sequence if pt == pitch_type]
        require(len(positions) == 3, f"#1 {label}: PA does not contain exactly three {pitch_type}")
        require(positions[-1] == sequence[-1][0], f"#1 {label}: final pitch does not match target")
        require(int(row["pitch_number"]) == positions[2], f"#1 {label}: output is not third occurrence")
        if label == "consecutive":
            require(
                positions[1] == positions[0] + 1 and positions[2] == positions[1] + 1,
                "#1 consecutive spot-check failed",
            )
        else:
            require(all(b - a > 1 for a, b in zip(positions, positions[1:])), "#1 none-adjacent spot-check failed")
        evidence.append(
            {
                "cohort": label,
                "game_pk": row["game_pk"],
                "at_bat_number": row["at_bat_number"],
                "sequence": sequence,
            }
        )
    workflow = facade.analyze(
        {
            "mode": "workflow",
            "stages": [
                {
                    "kind": "event_pattern_cohorts",
                    "event": {"field": "pitch_type", "op": "eq", "value": pitch_type},
                    "occurrence": 3,
                    "exact_count": 3,
                    "require_last_event": True,
                    "arrangements": ["consecutive", "none_adjacent"],
                    "cohort_alias": "pattern_cohort",
                }
            ],
            "limit": 5000,
        }
    )
    cohorts = {r.get("pattern_cohort") for r in workflow.get("rows", [])}
    require({"consecutive", "none_adjacent"}.issubset(cohorts), "#1 workflow cohorts are not composable")
    return (
        {
            "reference_fastball_type": reference_type,
            "target_pitch_type": pitch_type,
            "consecutive_rows": consecutive.get("row_count"),
            "none_adjacent_rows": none_adjacent.get("row_count"),
            "spot_checks": evidence,
            "workflow_rows": workflow.get("row_count"),
        },
        pitch_type,
    )


def question2(facade: AnalysisFacade, conn: sqlite3.Connection) -> dict[str, Any]:
    arsenal = facade.analyze(
        {"mode": "arsenal", "entity_fields": ["pitcher"], "min_usage": 0.05, "tie_method": "dense_rank"}
    )
    require(bool(arsenal.get("rows")), "#2 arsenal produced no rows")
    role = facade.analyze(
        {
            "mode": "pitch_role",
            "entity_fields": ["pitcher"],
            "metric_kind": "usage_rate",
            "rank": 1,
            "descending": True,
            "tie_method": "dense_rank",
        }
    )
    require(bool(role.get("rows")), "#2 pitch role produced no rows")
    workflow = facade.analyze(
        {
            "mode": "workflow",
            "stages": [
                {
                    "kind": "arsenal_signature",
                    "entity_fields": ["pitcher"],
                    "pitch_field": "pitch_type",
                    "min_usage": 0.05,
                    "alias": "arsenal",
                },
                {
                    "kind": "pitch_role_select",
                    "entity_fields": ["arsenal"],
                    "pitch_field": "pitch_type",
                    "metric_kind": "usage_rate",
                    "rank": 1,
                    "tie_method": "dense_rank",
                    "alias": "arsenal_role_rank",
                },
                {
                    "kind": "aggregate",
                    "group_by": ["arsenal", "pitch_type"],
                    "metrics": [{"function": "count", "alias": "pitch_rows"}],
                },
            ],
            "limit": 5000,
        }
    )
    require(bool(workflow.get("rows")), "#2 same-arsenal relative selector workflow produced no rows")
    sample = arsenal["rows"][0]
    pitcher = sample["pitcher"]
    total = int(
        conn.execute("SELECT COUNT(*) FROM pitches WHERE pitcher=? AND pitch_type IS NOT NULL", (pitcher,)).fetchone()[0]
    )
    counts = {
        str(r[0]): int(r[1])
        for r in conn.execute(
            "SELECT pitch_type,COUNT(*) FROM pitches WHERE pitcher=? AND pitch_type IS NOT NULL GROUP BY pitch_type",
            (pitcher,),
        )
    }
    expected = sorted(pt for pt, count in counts.items() if total and count / total >= 0.05)
    observed = sorted(str(sample.get("arsenal") or "").split("|"))
    require(expected == observed, f"#2 arsenal signature raw check differs: expected {expected}, observed {observed}")
    return {
        "arsenal_rows": arsenal.get("row_count"),
        "role_rows": role.get("row_count"),
        "workflow_rows": workflow.get("row_count"),
        "spot_check": {"pitcher": pitcher, "expected_arsenal": expected},
    }


def find_rising_example(conn: sqlite3.Connection) -> tuple[int, str, list[dict[str, Any]]]:
    rows = conn.execute(
        """
        SELECT pitcher,game_date,game_pk,pitch_type,COUNT(*) AS target_count
        FROM pitches WHERE pitch_type IS NOT NULL
        GROUP BY pitcher,game_date,game_pk,pitch_type
        ORDER BY pitcher,game_date,game_pk
        """
    ).fetchall()
    totals = {
        (int(r[0]), str(r[1]), int(r[2])): int(r[3])
        for r in conn.execute(
            "SELECT pitcher,game_date,game_pk,COUNT(*) FROM pitches GROUP BY pitcher,game_date,game_pk"
        )
    }
    by_key: dict[tuple[int, str], list[dict[str, Any]]] = defaultdict(list)
    for pitcher, gdate, gpk, ptype, count in rows:
        key = (int(pitcher), str(ptype))
        total = totals[(int(pitcher), str(gdate), int(gpk))]
        by_key[key].append(
            {"game_date": str(gdate), "game_pk": int(gpk), "usage": int(count) / total, "count": int(count)}
        )
    for (pitcher, ptype), games in by_key.items():
        if len(games) < 4:
            continue
        for i in range(2, len(games) - 1):
            a, b, c = games[i - 2 : i + 1]
            if a["usage"] < b["usage"] < c["usage"]:
                return pitcher, ptype, games[i - 2 : i + 2]
    raise AssertionError("#3 could not find a real strict three-game rising-usage sequence with a fourth game")


def question3(facade: AnalysisFacade, conn: sqlite3.Connection) -> dict[str, Any]:
    pitcher, ptype, example = find_rising_example(conn)
    workflow = facade.analyze(
        {
            "mode": "workflow",
            "filters": [{"field": "pitcher", "op": "eq", "value": pitcher}],
            "stages": [
                {
                    "kind": "aggregate",
                    "group_by": ["pitcher", "game_date", "game_pk"],
                    "metrics": [
                        {"function": "count", "alias": "total_count"},
                        {
                            "function": "count",
                            "alias": "target_count",
                            "condition": {"field": "pitch_type", "op": "eq", "value": ptype},
                        },
                        {
                            "function": "avg",
                            "field": "release_speed",
                            "alias": "target_avg_speed",
                            "condition": {"field": "pitch_type", "op": "eq", "value": ptype},
                        },
                    ],
                },
                {"kind": "derive", "alias": "usage_rate", "left": "target_count", "operator": "/", "right_field": "total_count"},
                {
                    "kind": "trend",
                    "alias": "rising_3",
                    "field": "usage_rate",
                    "direction": "up",
                    "periods": 3,
                    "partition_by": ["pitcher"],
                    "order_by": [{"field": "game_date"}, {"field": "game_pk"}],
                    "strict": True,
                },
                {
                    "kind": "offset",
                    "alias": "game4_speed",
                    "field": "target_avg_speed",
                    "direction": "lead",
                    "offset": 1,
                    "partition_by": ["pitcher"],
                    "order_by": [{"field": "game_date"}, {"field": "game_pk"}],
                },
                {"kind": "filter", "field": "rising_3", "op": "eq", "value": True},
            ],
            "limit": 5000,
        }
    )
    require(bool(workflow.get("rows")), "#3 workflow found no rising-3 output despite raw known example")
    known_game3 = example[2]["game_pk"]
    matched = next((r for r in workflow["rows"] if int(r["game_pk"]) == known_game3), None)
    require(matched is not None, "#3 known rising example not marked by workflow")
    raw_game4 = conn.execute(
        "SELECT AVG(release_speed) FROM pitches WHERE pitcher=? AND game_pk=? AND pitch_type=?",
        (pitcher, example[3]["game_pk"], ptype),
    ).fetchone()[0]
    if raw_game4 is None:
        require(matched.get("game4_speed") is None, "#3 fourth-game NULL metric mismatch")
    else:
        require(
            math.isclose(float(matched["game4_speed"]), float(raw_game4), rel_tol=1e-9, abs_tol=1e-9),
            "#3 fourth-game Lead value mismatch",
        )
    return {
        "pitcher": pitcher,
        "pitch_type": ptype,
        "raw_four_games": example,
        "workflow_rows": workflow.get("row_count"),
        "matched_game3": known_game3,
        "game4_speed": matched.get("game4_speed"),
    }


def question4(facade: AnalysisFacade, conn: sqlite3.Connection, reference_type: str) -> dict[str, Any]:
    result = facade.analyze(
        {
            "mode": "workflow",
            "stages": [
                {
                    "kind": "pitch_role_annotate",
                    "entity_fields": ["pitcher"],
                    "metric_kind": "usage_rate",
                    "exclude_pitch_types": [reference_type],
                    "rank": 1,
                    "tie_method": "row_number",
                    "alias": "selected_pitch_type",
                },
                {
                    "kind": "aggregate",
                    "group_by": ["pitcher", "selected_pitch_type"],
                    "metrics": [
                        {
                            "function": "count",
                            "alias": "candidate_count",
                            "condition": {"field": "pitch_type", "op": "eq", "value_field": "selected_pitch_type"},
                        },
                        {
                            "function": "count",
                            "alias": "reference_count",
                            "condition": {"field": "pitch_type", "op": "eq", "value": reference_type},
                        },
                        {
                            "function": "avg",
                            "field": "release_speed",
                            "alias": "candidate_speed",
                            "condition": {"field": "pitch_type", "op": "eq", "value_field": "selected_pitch_type"},
                        },
                        {
                            "function": "avg",
                            "field": "release_speed",
                            "alias": "reference_speed",
                            "condition": {"field": "pitch_type", "op": "eq", "value": reference_type},
                        },
                    ],
                },
            ],
            "limit": 5000,
        }
    )
    require(bool(result.get("rows")), "#4 dynamic non-reference annotation produced no rows")
    checked = None
    for row in result["rows"]:
        pitcher = int(row["pitcher"])
        selected = str(row["selected_pitch_type"])
        counts = [
            (str(r[0]), int(r[1]))
            for r in conn.execute(
                "SELECT pitch_type,COUNT(*) FROM pitches WHERE pitcher=? AND pitch_type IS NOT NULL AND pitch_type!=? GROUP BY pitch_type ORDER BY COUNT(*) DESC,pitch_type",
                (pitcher, reference_type),
            )
        ]
        if counts:
            require(selected == counts[0][0], f"#4 selected {selected}, raw highest-usage non-reference is {counts[0][0]}")
            checked = {"pitcher": pitcher, "selected": selected, "raw_counts": counts[:5]}
            break
    require(checked is not None, "#4 had no spot-checkable pitcher")
    return {"reference_fastball_type": reference_type, "rows": result.get("row_count"), "spot_check": checked}


def question5(facade: AnalysisFacade, reference_type: str) -> dict[str, Any]:
    result = facade.analyze(
        {
            "mode": "workflow",
            "stages": [
                {
                    "kind": "arsenal_signature",
                    "entity_fields": ["pitcher"],
                    "pitch_field": "pitch_type",
                    "min_usage": 0.05,
                    "alias": "arsenal",
                },
                {
                    "kind": "aggregate",
                    "group_by": ["arsenal", "pitcher"],
                    "metrics": [
                        {"function": "count", "alias": "total_count"},
                        {
                            "function": "count",
                            "alias": "reference_count",
                            "condition": {"field": "pitch_type", "op": "eq", "value": reference_type},
                        },
                        {"function": "avg", "field": "release_speed", "alias": "avg_speed"},
                    ],
                },
                {
                    "kind": "derive",
                    "alias": "reference_usage_rate",
                    "left": "reference_count",
                    "operator": "/",
                    "right_field": "total_count",
                },
                {
                    "kind": "empirical_percentile",
                    "field": "reference_usage_rate",
                    "partition_by": ["arsenal"],
                    "alias": "reference_usage_pct",
                },
                {"kind": "filter", "field": "reference_usage_pct", "op": "ge", "value": 0.5},
                {
                    "kind": "aggregate",
                    "group_by": ["arsenal"],
                    "metrics": [
                        {"function": "count", "alias": "pitchers_high_half"},
                        {"function": "avg", "field": "avg_speed", "alias": "cohort_avg_speed"},
                    ],
                },
            ],
            "limit": 5000,
        }
    )
    require(bool(result.get("rows")), "#5 nested arsenal-percentile-downstream workflow produced no rows")
    return {
        "reference_fastball_type": reference_type,
        "rows": result.get("row_count"),
        "sample": result["rows"][:5],
    }


def question6(
    facade: AnalysisFacade,
    conn: sqlite3.Connection,
    pitch_type: str,
    reference_type: str,
) -> dict[str, Any]:
    result = facade.analyze(
        {
            "mode": "follow_event",
            "anchor": {"field": "pitch_type", "op": "eq", "value": pitch_type},
            "target": {"field": "pitch_type", "op": "eq", "value": pitch_type},
            "between": [{"field": "pitch_type", "op": "eq", "value": reference_type}],
            "max_gap": 3,
        }
    )
    require(bool(result.get("rows")), "#6 bounded follow-event produced no rows")
    examples = []
    for want in (0, 1):
        row = next((r for r in result["rows"] if int(r.get("between_1") or 0) == want), None)
        if row is None:
            continue
        sequence = pa_sequence(conn, row)
        target_pn = int(row["pitch_number"])
        previous = [pn for pn, pt in sequence if pt == pitch_type and target_pn - 3 <= pn < target_pn]
        require(previous, "#6 target has no target-type anchor in previous N pitches")
        anchor = max(previous)
        between = [pt for pn, pt in sequence if anchor < pn < target_pn]
        require(pitch_type not in between, "#6 selected target is not first repeated target after anchor")
        require((reference_type in between) == bool(want), "#6 between reference-fastball flag mismatch")
        examples.append(
            {
                "between": want,
                "game_pk": row["game_pk"],
                "at_bat_number": row["at_bat_number"],
                "anchor": anchor,
                "target": target_pn,
                "sequence": sequence,
            }
        )
    require(examples, "#6 no raw spot-check completed")
    return {
        "reference_fastball_type": reference_type,
        "rows": result.get("row_count"),
        "target_pitch_type": pitch_type,
        "spot_checks": examples,
    }


def question7(facade: AnalysisFacade, conn: sqlite3.Connection, reference_type: str) -> dict[str, Any]:
    result = facade.analyze(
        {
            "mode": "cross_level",
            "filters": [{"field": "pitch_type", "op": "eq", "value": reference_type}],
            "unit_fields": ["pitcher", "game_pk"],
            "baseline_fields": ["pitcher"],
            "value_field": "release_speed",
            "function": "avg",
        }
    )
    require(bool(result.get("rows")), "#7 cross-grain result empty")
    row = next(
        (r for r in result["rows"] if r.get("unit_value") is not None and r.get("baseline_value") is not None),
        None,
    )
    require(row is not None, "#7 has no complete row")
    raw_unit = conn.execute(
        "SELECT AVG(release_speed) FROM pitches WHERE pitcher=? AND game_pk=? AND pitch_type=?",
        (row["pitcher"], row["game_pk"], reference_type),
    ).fetchone()[0]
    raw_base = conn.execute(
        "SELECT AVG(release_speed) FROM pitches WHERE pitcher=? AND pitch_type=?",
        (row["pitcher"], reference_type),
    ).fetchone()[0]
    require(math.isclose(float(row["unit_value"]), float(raw_unit), rel_tol=1e-9), "#7 unit average mismatch")
    require(math.isclose(float(row["baseline_value"]), float(raw_base), rel_tol=1e-9), "#7 baseline average mismatch")
    require(
        math.isclose(float(row["difference"]), float(raw_unit) - float(raw_base), rel_tol=1e-9, abs_tol=1e-9),
        "#7 difference mismatch",
    )
    return {
        "reference_fastball_type": reference_type,
        "rows": result.get("row_count"),
        "spot_check": {
            "pitcher": row["pitcher"],
            "game_pk": row["game_pk"],
            "unit": raw_unit,
            "baseline": raw_base,
            "difference": row["difference"],
        },
    }


def question8(facade: AnalysisFacade, conn: sqlite3.Connection) -> dict[str, Any]:
    min_date, max_date = conn.execute("SELECT MIN(game_date),MAX(game_date) FROM pitches").fetchone()
    result = facade.analyze(
        {
            "mode": "arsenal_change",
            "entity_fields": ["pitcher"],
            "min_usage": 0.05,
            "period_a": {"start": str(min_date), "end": "2026-06-30"},
            "period_b": {"start": "2026-07-01", "end": str(max_date)},
        }
    )
    rows = result_rows(result)
    require(rows, "#8 arsenal change produced no Added/Removed rows")
    require(all(r.get("pitch_type") is not None for r in rows), "#8 NULL pitch type appeared in arsenal change")
    sample = rows[0]
    pitcher = sample["pitcher"]
    games_a = int(
        conn.execute(
            "SELECT COUNT(*) FROM pitches WHERE pitcher=? AND game_date BETWEEN ? AND ?",
            (pitcher, str(min_date), "2026-06-30"),
        ).fetchone()[0]
    )
    games_b = int(
        conn.execute(
            "SELECT COUNT(*) FROM pitches WHERE pitcher=? AND game_date BETWEEN ? AND ?",
            (pitcher, "2026-07-01", str(max_date)),
        ).fetchone()[0]
    )
    require(games_a > 0 and games_b > 0, "#8 compared entity without samples in both periods")
    return {
        "rows": len(rows),
        "sample": sample,
        "period_a": {"start": min_date, "end": "2026-06-30"},
        "period_b": {"start": "2026-07-01", "end": max_date},
    }


def question9(facade: AnalysisFacade, conn: sqlite3.Connection) -> dict[str, Any]:
    result = facade.analyze(
        {"mode": "percentile", "entity_fields": ["pitcher"], "value_field": "release_speed", "threshold": 0.8, "side": "high"}
    )
    require(bool(result.get("rows")), "#9 percentile result empty")
    row = next((r for r in result["rows"] if r.get("release_speed") is not None), None)
    require(row is not None, "#9 no numeric output row")
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
    percentile = (less + 0.5 * equal) / len(values)
    require(percentile >= 0.8 - 1e-12, "#9 output row is below accepted per-pitcher mid-distribution percentile")
    return {
        "rows": result.get("row_count"),
        "spot_check": {
            "pitcher": row["pitcher"],
            "release_speed": value,
            "manual_percentile": percentile,
            "sample_size": len(values),
        },
    }


def usable_cluster_features(conn: sqlite3.Connection) -> list[str]:
    fields = {str(r[1]) for r in conn.execute("PRAGMA table_info(pitches)")}
    candidates = (
        "release_speed",
        "release_spin_rate",
        "release_extension",
        "plate_x",
        "plate_z",
        "hit_exit_speed_kph",
        "hit_launch_angle",
        "land_distance_m",
    )
    usable = []
    for field in candidates:
        if field not in fields:
            continue
        non_null, distinct_values = conn.execute(
            f'SELECT COUNT("{field}"), COUNT(DISTINCT "{field}") FROM pitches WHERE "{field}" IS NOT NULL'
        ).fetchone()
        if int(non_null) >= 100 and int(distinct_values) >= 2:
            usable.append(field)
    return usable


def question10(facade: AnalysisFacade, conn: sqlite3.Connection, reference_type: str) -> dict[str, Any]:
    feature_candidates = usable_cluster_features(conn)
    require(len(feature_candidates) >= 2, "#10 insufficient populated CPBL numeric Trackman features")
    features = feature_candidates[:2]
    result = facade.analyze(
        {
            "mode": "cluster_compare",
            "entity_fields": ["pitcher"],
            "min_usage": 0.05,
            "reference_pitch_type": reference_type,
            "selection_value_field": "release_speed",
            "selection_function": "avg",
            "selection_direction": "desc",
            "tie_method": "row_number",
            "features": features,
            "method": "kmeans",
            "clusters": 2,
            "standardize": True,
            "seed": 42,
            "evaluation_field": "release_speed",
            "evaluation_direction": "desc",
            "max_input_rows": 500000,
        }
    )
    comparison = (result.get("sections") or [{}])[0]
    require(bool(comparison.get("rows")), "#10 Cluster Comparison produced no accepted entities")
    for row in comparison["rows"][:20]:
        pitcher = row["pitcher"]
        candidate = row["candidate_pitch_type"]
        total = int(
            conn.execute("SELECT COUNT(*) FROM pitches WHERE pitcher=? AND pitch_type IS NOT NULL", (pitcher,)).fetchone()[0]
        )
        count = int(
            conn.execute("SELECT COUNT(*) FROM pitches WHERE pitcher=? AND pitch_type=?", (pitcher, candidate)).fetchone()[0]
        )
        require(total and count / total >= 0.05 - 1e-12, f"#10 candidate {candidate} for pitcher {pitcher} violates Minimum Usage")
    skipped = []
    for section in result.get("sections") or []:
        if "Skipped" in str(section.get("title")) or "略過" in str(section.get("title")):
            skipped = section.get("rows") or []
    return {
        "reference_fastball_type": reference_type,
        "comparison_rows": comparison.get("row_count"),
        "features": features,
        "sample": comparison["rows"][:3],
        "skipped_entities": len(skipped),
    }


def run(root: Path) -> None:
    config = config_for(root)
    spec = dataset_spec(config, "cpbl")
    if not spec.database_path.exists():
        raise SystemExit("Stage 7G requires Stage 7A dataset artifact")
    facade = facade_for(spec.database_path, spec.analytics_database_path, "duckdb")
    conn = sqlite3.connect(spec.database_path)
    conn.row_factory = sqlite3.Row
    tests = []
    failures = []
    target = None
    reference_type = reference_fastball_type(conn)
    funcs = [question1, question2, question3, question4, question5, question6, question7, question8, question9, question10]
    for index, fn in enumerate(funcs, 1):
        started = __import__("time").perf_counter()
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
                    "seconds": __import__("time").perf_counter() - started,
                    "detail": detail,
                }
            )
        except Exception as exc:
            tests.append(
                {
                    "number": index,
                    "status": "FAIL",
                    "seconds": __import__("time").perf_counter() - started,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            failures.append(index)
    conn.close()
    report = {
        "stage": "7G-backend-real-data",
        "reference_fastball_type": reference_type,
        "tests": tests,
        "passed": 10 - len(failures),
        "failed": failures,
    }
    dump_json(root / "reports" / "7g_ten_questions_backend.json", report)
    if failures:
        raise SystemExit("Stage 7G backend acceptance failed questions: " + ",".join(map(str, failures)))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("stage7_work"))
    args = parser.parse_args()
    run(args.root)


if __name__ == "__main__":
    main()
