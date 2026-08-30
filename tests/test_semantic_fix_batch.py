import math
import sqlite3
from pathlib import Path

import pytest

from treepolo_mlb_data.analysis.feature_semantics import encode_circular_features
from treepolo_mlb_data.analysis.model import Grain
from treepolo_mlb_data.analysis.numerical import NumericalTable
from treepolo_mlb_data.schema import field_capabilities
from treepolo_mlb_data.web_analysis import AnalysisFacade


def make_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE pitches (
        pitch_uid TEXT PRIMARY KEY,
        game_pk INTEGER,
        at_bat_number INTEGER,
        pitch_number INTEGER,
        game_date TEXT,
        game_year INTEGER,
        pitcher INTEGER,
        batter INTEGER,
        pitch_type TEXT,
        release_speed REAL,
        release_spin_rate REAL,
        pfx_x REAL,
        pfx_z REAL,
        spin_axis REAL,
        estimated_woba_using_speedangle REAL,
        description TEXT
    )""")
    conn.commit()
    conn.close()


def insert_rows(path: Path, rows) -> None:
    conn = sqlite3.connect(path)
    conn.executemany("INSERT INTO pitches VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()


def test_field_01_identifier_columns_are_not_continuous_numeric_features():
    pitcher = set(field_capabilities("pitcher", "INTEGER"))
    game_pk = set(field_capabilities("game_pk", "INTEGER"))
    speed = set(field_capabilities("release_speed", "REAL"))

    assert "identifier" in pitcher and "numeric" not in pitcher
    assert "identifier" in game_pk and "numeric" not in game_pk
    assert "numeric" in speed and "identifier" not in speed


def test_rs_02_spin_axis_uses_unit_circle_encoding():
    table = NumericalTable(
        ("pitch_uid", "spin_axis"),
        (
            {"pitch_uid": "a", "spin_axis": 1.0},
            {"pitch_uid": "b", "spin_axis": 359.0},
        ),
        Grain(("pitch_uid",), "pitch"),
    )
    encoded, features, encodings = encode_circular_features(table, ("spin_axis",))

    assert features == ("spin_axis_sin", "spin_axis_cos")
    assert encodings == {"spin_axis": ("spin_axis_sin", "spin_axis_cos")}
    a, b = encoded.rows
    distance = math.hypot(
        a["spin_axis_sin"] - b["spin_axis_sin"],
        a["spin_axis_cos"] - b["spin_axis_cos"],
    )
    assert distance == pytest.approx(2 * math.sin(math.radians(1.0)), rel=1e-9)
    assert distance < 0.04

    with pytest.raises(ValueError, match="identifier fields"):
        encode_circular_features(
            NumericalTable(("pitcher",), ({"pitcher": 123},), Grain(("pitcher",), "pitcher")),
            ("pitcher",),
        )


def test_rs_01_extreme_ties_receive_mid_distribution_percentile(tmp_path):
    path = tmp_path / "percentile.sqlite"
    make_db(path)
    rows = [
        (f"p{i}", 1, 1, i, "2026-04-01", 2026, 10, 100 + i, "FF",
         90.0, 2300.0, -0.8, 1.5, 180.0, 0.30, "ball")
        for i in range(1, 5)
    ]
    insert_rows(path, rows)

    result = AnalysisFacade(path, backend="sqlite").analyze({
        "mode": "workflow",
        "stages": [{
            "kind": "empirical_percentile",
            "field": "release_speed",
            "partition_by": ["pitcher"],
            "alias": "speed_percentile",
        }],
        "limit": 20,
    })

    assert result["row_count"] == 4
    assert {row["speed_percentile"] for row in result["rows"]} == {pytest.approx(0.5)}


def test_bug_10b_skips_only_entity_with_too_few_complete_cluster_rows(tmp_path):
    path = tmp_path / "cluster.sqlite"
    make_db(path)
    rows = []
    uid = 0

    def add_pitcher(pitcher: int, *, sparse_candidate: bool) -> None:
        nonlocal uid
        pitch_no = 0
        for i in range(4):
            uid += 1
            pitch_no += 1
            rows.append((
                f"p{uid}", pitcher, 1, pitch_no, "2026-04-01", 2026, pitcher, 100,
                "FF", 95.0 + i * 0.1, 2400.0 + i, -0.8, 1.5, 180.0, 0.35, "ball",
            ))
        candidate = (
            (85.0, 1700.0, -1.2, 0.5, 0.10),
            (85.2, None if sparse_candidate else 1710.0, -1.1, 0.6, 0.12),
            (88.0, None if sparse_candidate else 1900.0, 1.0, -0.5, 0.30),
            (88.2, None if sparse_candidate else 1910.0, 1.1, -0.6, 0.32),
        )
        for speed, spin, x, z, outcome in candidate:
            uid += 1
            pitch_no += 1
            rows.append((
                f"p{uid}", pitcher, 1, pitch_no, "2026-04-01", 2026, pitcher, 100,
                "CH", speed, spin, x, z, 225.0, outcome, "ball",
            ))

    add_pitcher(10, sparse_candidate=False)
    add_pitcher(20, sparse_candidate=True)
    insert_rows(path, rows)

    result = AnalysisFacade(path, backend="sqlite").analyze({
        "mode": "cluster_compare",
        "entity_fields": ["pitcher"],
        "min_usage": 0.10,
        "reference_pitch_type": "FF",
        "selection_value_field": "estimated_woba_using_speedangle",
        "selection_function": "avg",
        "selection_direction": "asc",
        "features": ["release_speed", "pfx_x", "pfx_z", "release_spin_rate"],
        "method": "kmeans",
        "clusters": 2,
        "standardize": True,
        "seed": 42,
        "evaluation_field": "estimated_woba_using_speedangle",
        "evaluation_direction": "asc",
        "tie_method": "row_number",
        "max_input_rows": 1000,
    })

    comparison = result["sections"][0]
    assert comparison["row_count"] == 1
    assert comparison["rows"][0]["pitcher"] == 10
    assert comparison["rows"][0]["candidate_pitch_type"] == "CH"

    skipped = next(section for section in result["sections"] if section["title"].startswith("略過的分析個體"))
    assert skipped["row_count"] == 1
    assert skipped["rows"][0]["pitcher"] == 20
    assert skipped["rows"][0]["complete_rows"] == 1
    assert skipped["rows"][0]["requested_clusters"] == 2
    assert result["numerical"]["skipped_entities"] == 1
