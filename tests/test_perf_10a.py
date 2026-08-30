import sqlite3
from pathlib import Path

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
        estimated_woba_using_speedangle REAL,
        description TEXT
    )""")
    rows = []
    uid = 0

    arsenals = {
        10: ("CH", "SL"),
        20: ("CH", "CU"),
        30: ("SI", "SL"),
        40: ("CU", "FC"),
    }
    candidate_outcomes = {
        "CH": (.10, .12, .30, .32),
        "CU": (.14, .16, .34, .36),
        "SI": (.18, .20, .38, .40),
        "SL": (.48, .50, .52, .54),
        "FC": (.44, .46, .48, .50),
    }
    movement = {
        "CH": ((0.0, 0.0), (.1, -.1), (5.0, 5.0), (5.1, 5.2)),
        "CU": ((-1.0, -1.0), (-1.1, -1.2), (4.0, 4.0), (4.2, 4.1)),
        "SI": ((1.0, 0.5), (1.1, .6), (6.0, 4.5), (6.1, 4.6)),
        "SL": ((-3.0, 2.0), (-3.1, 2.1), (2.0, -2.0), (2.1, -2.1)),
        "FC": ((-.5, .8), (-.6, .9), (3.5, -1.5), (3.6, -1.6)),
    }

    for pitcher, non_ff in arsenals.items():
        pitch_no = 0
        for value in (.34, .36, .35, .35):
            uid += 1
            pitch_no += 1
            rows.append((
                f"p{uid}", pitcher, 1, pitch_no, "2026-04-01", 2026, pitcher, 100,
                "FF", 95.0, 2400.0, -0.2, 1.2, value, "ball",
            ))
        for pitch_type in non_ff:
            for (x, z), value in zip(movement[pitch_type], candidate_outcomes[pitch_type]):
                uid += 1
                pitch_no += 1
                rows.append((
                    f"p{uid}", pitcher, 1, pitch_no, "2026-04-01", 2026, pitcher, 100,
                    pitch_type, 86.0, 2100.0, x, z, value, "ball",
                ))

    conn.executemany("INSERT INTO pitches VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()


class CountingFacade(AnalysisFacade):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.execute_calls = 0

    def _execute(self, node):
        self.execute_calls += 1
        return super()._execute(node)


def test_perf_10a_cluster_compare_uses_constant_two_query_fast_path(tmp_path):
    path = tmp_path / "perf10a.sqlite"
    make_db(path)
    facade = CountingFacade(path, backend="sqlite")
    result = facade.analyze({
        "mode": "cluster_compare",
        "entity_fields": ["pitcher"],
        "min_usage": 0.05,
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
        "cluster_workers": 2,
    })

    assert facade.execute_calls == 2
    performance = result["numerical"]["performance"]
    assert performance["fast_path"] is True
    assert performance["database_queries"] == 2
    assert performance["arsenal_signatures"] == 4
    assert performance["cluster_workers"] == 2
    assert performance["candidate_rows"] > 0
    assert performance["timings_ms"]["aggregate_query_ms"] >= 0
    assert performance["timings_ms"]["numerical_clustering_ms"] >= 0
    assert performance["timings_ms"]["total_ms"] >= 0

    comparison = result["sections"][0]
    assert comparison["row_count"] == 4
    for row in comparison["rows"]:
        assert row["candidate_pitch_type"] in row["arsenal"].split("|")
        assert row["reference_pitch_type"] == "FF"
        assert row["reference_sample_size"] == 4
