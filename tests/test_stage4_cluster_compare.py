import sqlite3
from pathlib import Path

import pytest

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
        pfx_x REAL,
        pfx_z REAL,
        estimated_woba_using_speedangle REAL,
        description TEXT,
        zone INTEGER,
        p_throws TEXT,
        stand TEXT
    )""")
    rows = []
    uid = 0
    for pitcher, game_pk in ((10, 1), (20, 2)):
        pitch_no = 0
        # Reference FF: average outcome .35.
        for value in (.34, .36, .35, .35):
            uid += 1; pitch_no += 1
            rows.append((f"p{uid}", game_pk, 1, pitch_no, "2026-04-01", 2026, pitcher, 100,
                         "FF", 95.0, -0.2, 1.2, value, "ball", 11, "R", "R"))
        # CH is the best non-FF pitch across the shared arsenal group. Within each
        # pitcher it has two obvious movement clusters; the low-x/low-z cluster
        # is also the better outcome cluster (lower wOBA-like value).
        for x, z, value in ((0.0, 0.0, .10), (.1, -.1, .12), (5.0, 5.0, .30), (5.1, 5.2, .32)):
            uid += 1; pitch_no += 1
            rows.append((f"p{uid}", game_pk, 1, pitch_no, "2026-04-01", 2026, pitcher, 100,
                         "CH", 86.0, x, z, value, "ball", 11, "R", "R"))
        # SL is clearly worse, so the arsenal-group selector must reject it.
        for x, z, value in ((-3.0, 2.0, .48), (-3.1, 2.1, .50), (-2.9, 1.9, .52), (-3.2, 2.2, .50)):
            uid += 1; pitch_no += 1
            rows.append((f"p{uid}", game_pk, 1, pitch_no, "2026-04-01", 2026, pitcher, 100,
                         "SL", 84.0, x, z, value, "ball", 11, "R", "R"))
    conn.executemany("INSERT INTO pitches VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()


def test_stress_10_multistage_selector_per_pitcher_clustering_and_ff_comparison(tmp_path):
    path = tmp_path / "stage4.sqlite"
    make_db(path)
    result = AnalysisFacade(path, backend="sqlite").analyze({
        "mode": "cluster_compare",
        "entity_fields": ["pitcher"],
        "min_usage": 0.05,
        "reference_pitch_type": "FF",
        "selection_value_field": "estimated_woba_using_speedangle",
        "selection_function": "avg",
        "selection_direction": "asc",
        "features": ["pfx_x", "pfx_z"],
        "method": "kmeans",
        "clusters": 2,
        "standardize": True,
        "seed": 7,
        "evaluation_field": "estimated_woba_using_speedangle",
        "evaluation_direction": "asc",
        "tie_method": "row_number",
        "max_input_rows": 1000,
    })
    assert result["backend"] == "numerical"
    comparison = result["sections"][0]
    assert comparison["title"].startswith("最佳分群")
    assert comparison["row_count"] == 2
    by_pitcher = {row["pitcher"]: row for row in comparison["rows"]}
    assert set(by_pitcher) == {10, 20}
    for row in by_pitcher.values():
        assert row["candidate_pitch_type"] == "CH"
        assert row["candidate_sample_size"] == 2
        assert row["candidate_value"] == pytest.approx(.11)
        assert row["reference_pitch_type"] == "FF"
        assert row["reference_sample_size"] == 4
        assert row["reference_value"] == pytest.approx(.35)
        assert row["difference"] == pytest.approx(-.24)
        assert "CH" in row["arsenal"] and "FF" in row["arsenal"] and "SL" in row["arsenal"]

    cluster_summary = result["sections"][1]
    assert cluster_summary["grain"]["keys"] == ["pitcher", "cluster"]
    assert cluster_summary["row_count"] == 4
