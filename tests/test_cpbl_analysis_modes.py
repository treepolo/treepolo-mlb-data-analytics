from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from treepolo_mlb_data.web_analysis import AnalysisFacade


def _make_cpbl_db(path: Path) -> None:
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
        description TEXT,
        zone INTEGER,
        release_speed REAL,
        release_spin_rate REAL,
        horz_appr_angle REAL,
        vert_appr_angle REAL,
        auto_pitch_type TEXT,
        tagged_pitch_type TEXT,
        pitch_call TEXT
    )""")

    rows = []
    uid = 0
    for pitcher, game_a, game_b in ((10, 101, 102), (20, 201, 202)):
        batter = 1000 + pitcher

        def add(game_pk, pa, pn, day, pitch_type, description, speed, haa, vaa):
            nonlocal uid
            uid += 1
            raw_type = {"FF": "FourSeamFastBall", "CH": "ChangeUp", "SL": "Slider"}[pitch_type]
            raw_call = {
                "called_strike": "StrikeCalled",
                "swinging_strike": "StrikeSwinging",
                "ball": "BallCalled",
                "hit_into_play": "InPlay",
            }[description]
            rows.append((
                f"cpbl-{uid}", game_pk, pa, pn, day, 2026, pitcher, batter,
                pitch_type, description, 5, float(speed), 1000.0 + 10.0 * float(speed),
                float(haa), float(vaa), raw_type, raw_type, raw_call,
            ))

        # Period A: FF + CH. CH deliberately has two obvious shape clusters.
        add(game_a, 1, 1, "2026-04-01", "FF", "called_strike", 145, 1.0, -4.5)
        add(game_a, 1, 2, "2026-04-01", "CH", "swinging_strike", 140, 0.0, -6.0)
        add(game_a, 1, 3, "2026-04-01", "CH", "ball", 141, 0.1, -6.1)
        add(game_a, 1, 4, "2026-04-01", "FF", "hit_into_play", 146, 1.1, -4.4)
        add(game_a, 2, 1, "2026-04-01", "CH", "ball", 135, 5.0, -1.0)
        add(game_a, 2, 2, "2026-04-01", "CH", "called_strike", 136, 5.1, -1.1)
        add(game_a, 2, 3, "2026-04-01", "FF", "ball", 144, 0.9, -4.6)
        add(game_a, 2, 4, "2026-04-01", "FF", "called_strike", 145, 1.0, -4.5)

        # Period B introduces SL. PA1 is exactly three consecutive sliders.
        add(game_b, 1, 1, "2026-04-02", "SL", "called_strike", 129, -3.0, -2.0)
        add(game_b, 1, 2, "2026-04-02", "SL", "swinging_strike", 130, -3.1, -2.1)
        add(game_b, 1, 3, "2026-04-02", "SL", "swinging_strike", 131, -2.9, -1.9)
        # FF -> CH gives a deterministic follow-event pair.
        add(game_b, 2, 1, "2026-04-02", "FF", "called_strike", 146, 1.1, -4.4)
        add(game_b, 2, 2, "2026-04-02", "CH", "swinging_strike", 142, 0.0, -6.0)
        add(game_b, 2, 3, "2026-04-02", "SL", "ball", 128, -3.2, -2.2)
        add(game_b, 3, 1, "2026-04-02", "CH", "called_strike", 143, 0.1, -6.1)
        add(game_b, 3, 2, "2026-04-02", "FF", "ball", 145, 1.0, -4.5)
        add(game_b, 3, 3, "2026-04-02", "SL", "hit_into_play", 129, -3.0, -2.0)
        add(game_b, 4, 1, "2026-04-02", "CH", "ball", 136, 5.0, -1.0)
        add(game_b, 4, 2, "2026-04-02", "CH", "called_strike", 137, 5.1, -1.1)
        add(game_b, 4, 3, "2026-04-02", "FF", "called_strike", 144, 0.9, -4.6)

    conn.executemany("INSERT INTO pitches VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()


def _facade(path: Path) -> AnalysisFacade:
    return AnalysisFacade(
        path,
        backend="sqlite",
        dataset_id="cpbl",
        dataset_label="CPBL / Trackman",
        speed_unit="kph",
        distance_unit="m",
    )


def _section(result, title_prefix):
    return next(section for section in result["sections"] if section["title"].startswith(title_prefix))


def test_cpbl_relational_modes_share_the_existing_analysis_core(tmp_path):
    path = tmp_path / "cpbl.sqlite3"
    _make_cpbl_db(path)
    facade = _facade(path)

    basic = facade.analyze({
        "mode": "basic",
        "group_by": ["pitch_type"],
        "metrics": [{"function": "count"}, {"function": "avg", "field": "release_speed"}],
        "result_sort": [{"field": "row_count", "descending": True}],
    })
    counts = {row["pitch_type"]: row["row_count"] for row in basic["rows"]}
    assert counts == {"CH": 16, "FF": 16, "SL": 10}

    sequence = facade.analyze({
        "mode": "sequence_pattern",
        "event": {"field": "pitch_type", "op": "eq", "value": "SL"},
        "occurrence": 3,
        "exact_count": 3,
        "require_last_event": True,
        "arrangement": "consecutive",
    })
    assert len(sequence["rows"]) == 2
    assert all(row["pitch_type"] == "SL" for row in sequence["rows"])

    follow = facade.analyze({
        "mode": "follow_event",
        "anchor": {"field": "pitch_type", "op": "eq", "value": "FF"},
        "target": {"field": "pitch_type", "op": "eq", "value": "CH"},
        "max_gap": 1,
    })
    assert len(follow["rows"]) >= 2

    arsenal = facade.analyze({"mode": "arsenal", "entity_fields": ["pitcher"], "min_usage": 0.05})
    assert {row["pitcher"] for row in arsenal["rows"]} == {10, 20}
    assert all("FF" in row["arsenal"] and "CH" in row["arsenal"] and "SL" in row["arsenal"] for row in arsenal["rows"])

    role = facade.analyze({
        "mode": "pitch_role",
        "entity_fields": ["pitcher"],
        "metric_kind": "field_metric",
        "value_field": "release_speed",
        "function": "avg",
        "rank": 1,
        "descending": True,
        "tie_method": "row_number",
    })
    assert {row["pitch_type"] for row in role["rows"]} == {"FF"}

    temporal = facade.analyze({
        "mode": "temporal",
        "entity_fields": ["pitcher"],
        "period_field": "game_pk",
        "value_field": "release_speed",
        "function": "avg",
        "direction": "previous",
        "offset": 1,
    })
    assert sum(row["reference_value"] is not None for row in temporal["rows"]) == 2

    percentile = facade.analyze({
        "mode": "percentile",
        "entity_fields": ["pitcher"],
        "value_field": "vert_appr_angle",
        "threshold": 0.8,
        "side": "high",
    })
    assert percentile["rows"]

    cross = facade.analyze({
        "mode": "cross_level",
        "unit_fields": ["pitcher", "game_pk"],
        "baseline_fields": ["pitcher"],
        "value_field": "release_speed",
        "function": "avg",
    })
    assert len(cross["rows"]) == 4

    change = facade.analyze({
        "mode": "arsenal_change",
        "entity_fields": ["pitcher"],
        "min_usage": 0.05,
        "period_a": {"start": "2026-04-01", "end": "2026-04-01"},
        "period_b": {"start": "2026-04-02", "end": "2026-04-02"},
    })
    added = change["sections"][0]["rows"]
    assert {(row["pitcher"], row["pitch_type"]) for row in added} == {(10, "SL"), (20, "SL")}


def test_cpbl_workflow_and_numerical_modes_use_native_trackman_features(tmp_path):
    path = tmp_path / "cpbl.sqlite3"
    _make_cpbl_db(path)
    facade = _facade(path)

    workflow = facade.analyze({
        "mode": "workflow",
        "stages": [{
            "kind": "aggregate",
            "group_by": ["pitcher", "pitch_type"],
            "metrics": [
                {"function": "count", "alias": "pitch_count"},
                {"function": "avg", "field": "vert_appr_angle", "alias": "avg_vaa"},
            ],
        }],
        "result_sort": [{"field": "pitch_count", "descending": True}],
        "limit": 100,
    })
    assert len(workflow["rows"]) == 6
    assert "avg_vaa" in workflow["columns"]

    clustering = facade.analyze({
        "mode": "clustering",
        "features": ["horz_appr_angle", "vert_appr_angle"],
        "id_fields": ["pitch_uid"],
        "partition_fields": ["pitcher"],
        "clusters": 2,
        "method": "kmeans",
        "seed": 7,
        "max_input_rows": 1000,
    })
    assert clustering["backend"] == "numerical"
    assert _section(clustering, "分群摘要")["row_count"] == 4

    regression = facade.analyze({
        "mode": "regression",
        "dependent": "release_spin_rate",
        "independent": ["release_speed"],
        "model": "linear",
        "max_input_rows": 1000,
    })
    coefficients = {row["term"]: row for row in _section(regression, "迴歸係數")["rows"]}
    assert coefficients["release_speed"]["estimate"] == pytest.approx(10.0, abs=1e-8)

    bootstrap = facade.analyze({
        "mode": "bootstrap",
        "value_field": "release_speed",
        "resample_unit_fields": ["game_pk"],
        "statistic": "mean",
        "iterations": 200,
        "seed": 11,
        "max_input_rows": 1000,
    })
    bootstrap_row = _section(bootstrap, "Bootstrap 結果")["rows"][0]
    assert bootstrap_row["resample_units"] == 4
    assert 130 < bootstrap_row["estimate"] < 150


def test_cpbl_cluster_compare_runs_on_haa_vaa_without_savant_only_fields(tmp_path):
    path = tmp_path / "cpbl.sqlite3"
    _make_cpbl_db(path)
    result = _facade(path).analyze({
        "mode": "cluster_compare",
        "entity_fields": ["pitcher"],
        "min_usage": 0.05,
        "reference_pitch_type": "FF",
        "selection_value_field": "release_speed",
        "selection_function": "avg",
        "selection_direction": "desc",
        "features": ["horz_appr_angle", "vert_appr_angle"],
        "method": "kmeans",
        "clusters": 2,
        "standardize": True,
        "seed": 7,
        "evaluation_field": "release_speed",
        "evaluation_direction": "desc",
        "tie_method": "row_number",
        "max_input_rows": 1000,
    })
    comparison = result["sections"][0]
    assert comparison["row_count"] == 2
    for row in comparison["rows"]:
        assert row["candidate_pitch_type"] == "CH"
        assert row["reference_pitch_type"] == "FF"
        assert row["candidate_value"] > 140
        assert row["reference_value"] > row["candidate_value"]
