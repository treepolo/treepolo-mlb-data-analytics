import sqlite3

import pytest

from research_fixtures import INGESTED, make_research_db
from treepolo_mlb_data.analysis import AnalysisEngine, Filter, PITCH_GRAIN, Source
from treepolo_mlb_data.research.config_schema import ConfigError
from treepolo_mlb_data.research.scope import compute_scope_fingerprint, normalize_scope, scope_filter_expr


def test_scope_normalization_sorts_and_defaults_game_types():
    assert normalize_scope({"game_years": [2024, 2023, 2024]}, required=True) == {"game_years": [2023, 2024], "game_types": ["R"]}
    assert normalize_scope({"date_from": "2023-04-01", "date_to": "2023-05-01", "game_types": ["S", "R", "R"]}, required=True) == {
        "date_from": "2023-04-01", "date_to": "2023-05-01", "game_types": ["R", "S"],
    }


def test_scope_optional_and_required():
    assert normalize_scope(None, required=False) == {}
    with pytest.raises(ConfigError, match="required"):
        normalize_scope(None, required=True)


@pytest.mark.parametrize("raw", [
    {},
    {"game_years": [2024], "date_from": "2024-01-01", "date_to": "2024-12-31"},
    {"date_from": "2024-01-01"},
    {"game_years": []},
    {"game_years": [1999]},
    {"game_years": [True]},
    {"game_years": ["2024"]},
    {"date_from": "2024-02-30", "date_to": "2024-03-01"},
    {"date_from": "2024-05-02", "date_to": "2024-05-01"},
    {"game_years": [2024], "game_types": []},
    {"game_years": [2024], "game_types": ["regular"]},
    {"game_years": [2024], "extra": 1},
    "nope",
])
def test_scope_rejects_bad_input(raw):
    with pytest.raises(ConfigError):
        normalize_scope(raw, required=True)


def uids(path, scope):
    expr = scope_filter_expr(scope)
    node = Source("pitches", PITCH_GRAIN) if expr is None else Filter(Source("pitches", PITCH_GRAIN), expr)
    return sorted(row["pitch_uid"] for row in AnalysisEngine(path).execute(node).rows)


def test_scope_filter_selects_expected_rows(tmp_path):
    db = make_research_db(tmp_path / "db.sqlite")
    assert uids(db, normalize_scope({"game_years": [2024]}, required=True)) == ["3:1:1", "3:1:2", "4:1:1", "4:1:2"]
    assert uids(db, normalize_scope({"game_years": [2023], "game_types": ["S"]}, required=True)) == ["2:1:1", "2:1:2"]
    assert uids(db, normalize_scope({"date_from": "2024-05-15", "date_to": "2025-05-01"}, required=True)) == ["4:1:1", "4:1:2", "5:1:1", "5:1:2"]
    assert scope_filter_expr({}) is None
    assert len(uids(db, {})) == 12


def test_fingerprint_is_stable_and_detects_changes_inside_scope_only(tmp_path):
    db = make_research_db(tmp_path / "db.sqlite")
    scope = normalize_scope({"game_years": [2024]}, required=True)
    first = compute_scope_fingerprint(db, scope)
    assert first["total_rows"] == 4
    assert [(s["game_year"], s["game_type"], s["rows"]) for s in first["seasons"]] == [(2024, "R", 4)]
    assert compute_scope_fingerprint(db, scope)["scope_fingerprint"] == first["scope_fingerprint"]

    conn = sqlite3.connect(db)
    conn.execute("UPDATE settings SET value='rev-2'")  # changes data_revision: cache must not hide the next changes
    conn.execute("UPDATE pitches SET _ingested_at='2027-01-01T00:00:00+00:00' WHERE pitch_uid='1:1:1'")  # outside scope
    conn.commit()
    assert compute_scope_fingerprint(db, scope)["scope_fingerprint"] == first["scope_fingerprint"]

    conn.execute("UPDATE settings SET value='rev-3'")
    conn.execute("UPDATE pitches SET _ingested_at='2027-02-01T00:00:00+00:00' WHERE pitch_uid='3:1:1'")  # inside scope
    conn.commit()
    changed = compute_scope_fingerprint(db, scope)
    assert changed["scope_fingerprint"] != first["scope_fingerprint"]

    conn.execute("UPDATE settings SET value='rev-4'")
    conn.execute(
        "INSERT INTO pitches VALUES ('9:1:1',9,1,1,2024,'R','2024-07-01',10,100,'FF','ball',NULL,NULL,90,0,2,'R','R',?)", (INGESTED,)
    )
    conn.commit()
    conn.close()
    assert compute_scope_fingerprint(db, scope)["total_rows"] == 5


def test_empty_scope_uses_revision_fingerprint(tmp_path):
    db = make_research_db(tmp_path / "db.sqlite")
    result = compute_scope_fingerprint(db, {})
    assert result["scope_fingerprint"] == "revision:rev-1" and result["total_rows"] is None


def test_fingerprint_reports_missing_columns(tmp_path):
    db = tmp_path / "bad.sqlite"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE pitches (pitch_uid TEXT, game_year INTEGER)")
    conn.commit(); conn.close()
    with pytest.raises(ConfigError, match="missing columns"):
        compute_scope_fingerprint(db, {"game_years": [2024], "game_types": ["R"]})
    with pytest.raises(ConfigError, match="does not exist"):
        compute_scope_fingerprint(tmp_path / "nope.sqlite", {"game_years": [2024], "game_types": ["R"]})
