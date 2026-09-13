import sqlite3
from pathlib import Path

from treepolo_mlb_data.web_analysis import AnalysisFacade


def make_pitch_db(path: Path) -> None:
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
        description TEXT
    )""")
    conn.commit()
    conn.close()


def insert_rows(path: Path, rows) -> None:
    conn = sqlite3.connect(path)
    conn.executemany("INSERT INTO pitches VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()


def test_event_pattern_cohorts_are_composable_in_workflow(tmp_path):
    path = tmp_path / "event.sqlite"
    make_pitch_db(path)
    rows = [
        ("a1", 1, 1, 1, "2026-04-01", 2026, 10, 100, "ST", 82.0, 2500, 1.0, 0.0, "ball"),
        ("a2", 1, 1, 2, "2026-04-01", 2026, 10, 100, "ST", 82.1, 2501, 1.0, 0.0, "ball"),
        ("a3", 1, 1, 3, "2026-04-01", 2026, 10, 100, "ST", 82.2, 2502, 1.0, 0.0, "strike"),
        ("b1", 2, 1, 1, "2026-04-02", 2026, 10, 101, "ST", 81.0, 2490, 1.1, -0.1, "ball"),
        ("b2", 2, 1, 2, "2026-04-02", 2026, 10, 101, "FF", 94.0, 2350, -0.8, 1.5, "ball"),
        ("b3", 2, 1, 3, "2026-04-02", 2026, 10, 101, "ST", 81.5, 2495, 1.0, -0.1, "ball"),
        ("b4", 2, 1, 4, "2026-04-02", 2026, 10, 101, "SI", 93.0, 2200, -1.0, 1.0, "ball"),
        ("b5", 2, 1, 5, "2026-04-02", 2026, 10, 101, "ST", 82.0, 2500, 1.1, -0.1, "strike"),
    ]
    insert_rows(path, rows)
    result = AnalysisFacade(path, backend="sqlite").analyze({
        "mode": "workflow",
        "filters": [{"field": "game_year", "op": "eq", "value": 2026}],
        "stages": [{
            "kind": "event_pattern_cohorts",
            "event": {"field": "pitch_type", "op": "eq", "value": "ST"},
            "occurrence": 3,
            "exact_count": 3,
            "require_last_event": True,
            "arrangements": ["consecutive", "none_adjacent"],
            "cohort_alias": "pattern_cohort",
        }],
        "limit": 20,
    })
    assert result["row_count"] == 2
    by_cohort = {row["pattern_cohort"]: row for row in result["rows"]}
    assert by_cohort["consecutive"]["pitch_number"] == 3
    assert by_cohort["none_adjacent"]["pitch_number"] == 5


def test_pitch_role_annotation_supports_dynamic_reference_metrics(tmp_path):
    path = tmp_path / "role.sqlite"
    make_pitch_db(path)
    rows = []
    uid = 0
    pitch_no = 0
    for pitch_type, count, speed, spin, x, z in (
        ("FF", 5, 95.0, 2400, -0.8, 1.5),
        ("CH", 4, 86.0, 1700, -1.0, .6),
        ("SL", 2, 84.0, 2500, .2, .4),
    ):
        for _ in range(count):
            uid += 1
            pitch_no += 1
            rows.append((f"p{uid}", 1, 1, pitch_no, "2026-04-01", 2026, 10, 100, pitch_type, speed, spin, x, z, "ball"))
    insert_rows(path, rows)
    result = AnalysisFacade(path, backend="sqlite").analyze({
        "mode": "workflow",
        "stages": [
            {
                "kind": "pitch_role_annotate",
                "entity_fields": ["pitcher"],
                "metric_kind": "usage_rate",
                "exclude_pitch_types": ["FF"],
                "rank": 1,
                "tie_method": "row_number",
                "alias": "selected_pitch_type",
            },
            {
                "kind": "aggregate",
                "group_by": ["pitcher"],
                "metrics": [
                    {
                        "function": "count",
                        "alias": "candidate_count",
                        "condition": {
                            "field": "pitch_type",
                            "op": "eq",
                            "value_field": "selected_pitch_type",
                        },
                    },
                    {
                        "function": "count",
                        "alias": "ff_count",
                        "condition": {"field": "pitch_type", "op": "eq", "value": "FF"},
                    },
                ],
            },
        ],
        "limit": 20,
    })
    assert result["row_count"] == 1
    assert result["rows"][0]["candidate_count"] == 4
    assert result["rows"][0]["ff_count"] == 5


def test_empirical_percentile_stage_and_generic_result_limit(tmp_path):
    path = tmp_path / "pct.sqlite"
    make_pitch_db(path)
    rows = [
        (f"p{i}", 1, 1, i, "2026-04-01", 2026, 10, 100, "FF", 90.0 + i, 2300 + i, -.8, 1.5, "ball")
        for i in range(1, 7)
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
        "result_limit": 2,
    })
    assert result["row_count"] == 6
    assert result["returned_row_count"] == 2
    assert len(result["rows"]) == 2
    assert "speed_percentile" in result["rows"][0]


def test_pitch_usage_excludes_null_pitch_type_from_arsenal(tmp_path):
    path = tmp_path / "arsenal.sqlite"
    make_pitch_db(path)
    rows = [
        ("p1", 1, 1, 1, "2026-04-01", 2026, 10, 100, "FF", 95.0, 2400, -.8, 1.5, "ball"),
        ("p2", 1, 1, 2, "2026-04-01", 2026, 10, 100, "FF", 95.0, 2400, -.8, 1.5, "ball"),
        ("p3", 1, 1, 3, "2026-04-01", 2026, 10, 100, None, 90.0, 2200, 0.0, 0.0, "ball"),
    ]
    insert_rows(path, rows)
    result = AnalysisFacade(path, backend="sqlite").analyze({
        "mode": "arsenal",
        "entity_fields": ["pitcher"],
        "min_usage": 0.05,
    })
    assert all(row["pitch_type"] is not None for row in result["rows"])
    assert all("None" not in str(row.get("arsenal")) for row in result["rows"])


def test_cluster_compare_candidate_obeys_minimum_usage(tmp_path):
    path = tmp_path / "cluster.sqlite"
    make_pitch_db(path)
    rows = []
    uid = 0
    pitch_no = 0

    def add(pitch_type, speeds, spin, x_values, z_values):
        nonlocal uid, pitch_no
        for speed, x, z in zip(speeds, x_values, z_values):
            uid += 1
            pitch_no += 1
            rows.append((f"p{uid}", 1, 1, pitch_no, "2026-04-01", 2026, 10, 100,
                         pitch_type, speed, spin, x, z, "ball"))

    add("FF", [95.0] * 10, 2400, [-.8] * 10, [1.5] * 10)
    add("CH", [85.0, 85.5, 86.0, 86.5, 87.0, 87.5, 88.0, 88.5], 1700,
        [-1.2, -1.1, -1.0, -.9, 1.0, 1.1, 1.2, 1.3],
        [.5, .6, .5, .6, -.5, -.6, -.5, -.6])
    add("SL", [83.0] * 8, 2500, [.2] * 8, [.4] * 8)
    add("FC", [99.0], 2450, [0.0], [1.0])
    insert_rows(path, rows)

    result = AnalysisFacade(path, backend="sqlite").analyze({
        "mode": "cluster_compare",
        "entity_fields": ["pitcher"],
        "min_usage": 0.10,
        "reference_pitch_type": "FF",
        "selection_value_field": "release_speed",
        "selection_function": "avg",
        "selection_direction": "desc",
        "features": ["pfx_x", "pfx_z"],
        "method": "kmeans",
        "clusters": 2,
        "standardize": True,
        "seed": 42,
        "evaluation_field": "release_speed",
        "evaluation_direction": "desc",
        "tie_method": "row_number",
        "max_input_rows": 1000,
    })
    comparison = result["sections"][0]
    assert comparison["row_count"] == 1
    assert comparison["rows"][0]["candidate_pitch_type"] == "CH"


def test_acceptance_ui_bundle_contains_required_controls_across_canonical_owners():
    root = Path(__file__).parents[1]
    static = root / "src/treepolo_mlb_data/web_static"
    acceptance = (static / "acceptance-fixes.js").read_text(encoding="utf-8")
    controls = (static / "stage4-controls.js").read_text(encoding="utf-8")
    paging = (static / "result-paging.js").read_text(encoding="utf-8")
    checklists = (static / "field-checklists.js").read_text(encoding="utf-8")
    fast_status = (static / "fast-status.js").read_text(encoding="utf-8")

    for token in (
        "Result Row Limit",
        "event_pattern_cohorts",
        "pitch_role_annotate",
        "empirical_percentile",
        "Add After",
        "ta-metric-cond-value-field",
    ):
        assert token in acceptance
    assert "Result Not Stored" in controls
    assert "PAGE_SIZE = 200" in paging
    assert "field-checklist-search" not in checklists
    assert "locateOnlySearch" not in checklists
    assert "已選" in checklists
    assert "/acceptance-fixes.js" in fast_status
