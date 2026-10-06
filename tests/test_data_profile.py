import json
import sqlite3

import pytest

from seq_fixtures import COLUMNS, seq_rows
from treepolo_mlb_data.cli import main
from treepolo_mlb_data.config import AppConfig
from treepolo_mlb_data.research.config_schema import ConfigError
from treepolo_mlb_data.research.profile import DEFAULT_TRACKED_FIELDS, MIRROR_FIELDS, SECTION_NAMES
from treepolo_mlb_data.research.service import ResearchService
from treepolo_mlb_data.web_analysis import AnalysisFacade

EXTRA_REAL = sorted((set(DEFAULT_TRACKED_FIELDS) | set(MIRROR_FIELDS) | {"pfx_x", "release_pos_x", "vx0", "ax", "spin_axis"}) - set(COLUMNS) - {"zone"})
SCOPE = {"game_years": [2024], "game_types": ["R"]}


def make_profile_db(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    extra = ", ".join(f"{name} REAL" for name in EXTRA_REAL)
    conn.executescript(
        "CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL);"
        "INSERT INTO settings VALUES ('data_revision','rev-1','2026-01-01T00:00:00+00:00');"
        "CREATE TABLE pitches (pitch_uid TEXT PRIMARY KEY, game_pk INTEGER, at_bat_number INTEGER, pitch_number INTEGER, game_year INTEGER, "
        "game_type TEXT, game_date TEXT, pitcher INTEGER, batter INTEGER, pitch_type TEXT, description TEXT, events TEXT, des TEXT, "
        f"release_speed REAL, plate_x REAL, plate_z REAL, p_throws TEXT, stand TEXT, _ingested_at TEXT, zone INTEGER, {extra});"
    )
    conn.executemany(f"INSERT INTO pitches ({','.join(COLUMNS)}) VALUES ({','.join('?' for _ in COLUMNS)})", seq_rows())
    conn.execute("UPDATE pitches SET miss_distance=1.2 WHERE pitch_uid='1:1:3'")                      # the only whiff
    conn.execute("UPDATE pitches SET launch_speed=80, launch_angle=-5 WHERE pitch_uid='1:1:2'")      # 1 of 3 fouls
    conn.execute("UPDATE pitches SET bat_speed=70 WHERE description IN ('foul','swinging_strike')")
    conn.execute("UPDATE pitches SET sz_top=3.5, sz_bot=1.77, zone=5, spin_axis=200, p_throws='R'")
    conn.execute("UPDATE pitches SET sz_top=3.4 WHERE pitch_uid='1:1:1'")                             # batter 101 has two sz_top values
    conn.commit(); conn.close()
    return path


@pytest.fixture()
def service(tmp_path):
    db = make_profile_db(tmp_path / "statcast.sqlite3")
    config = AppConfig(data_dir=str(tmp_path), analysis_backend="sqlite")
    svc = ResearchService(config, AnalysisFacade(db, backend="sqlite"))
    yield svc
    svc.close()


def sections(run, store):
    result = store.load_result(run["id"])
    return {s["title"].split(" ")[0]: s for s in result["sections"]}, result


def test_all_sections_have_the_documented_numbers(service):
    run = service.run("data_profile", {"scope": SCOPE})["run"]
    by_name, result = sections(run, service.store)
    assert set(by_name) == set(SECTION_NAMES)
    assert result["extras"]["pitch_rows_in_scope"] == 20 and result["extras"]["generated_by"] == "data_profile v1"

    assert by_name["rows_by_season"]["rows"] == [{"game_year": 2024, "game_type": "R", "rows": 20, "games": 2, "min_date": "2024-05-01", "max_date": "2024-05-01"}]
    descriptions = {r["description"]: r["rows"] for r in by_name["description_values"]["rows"]}
    assert descriptions == {"ball": 6, "foul": 3, "called_strike": 3, "hit_into_play": 5, "swinging_strike": 1, "automatic_ball": 1, "foul_bunt": 1}
    events = {r["events"]: r["rows"] for r in by_name["in_play_events"]["rows"]}
    assert events == {"field_out": 2, "sac_bunt": 1, "single": 1, "home_run": 1}
    types = {r["pitch_type"]: r["rows"] for r in by_name["pitch_type_values"]["rows"]}
    assert types["(null)"] == 2 and types["FF"] == 8 and sum(types.values()) == 20

    bunt = by_name["bunt_share"]["rows"][0]
    assert (bunt["plate_appearances"], bunt["bunt_plate_appearances"], bunt["bunt_pa_pct"]) == (8, 4, 50.0)
    assert (bunt["pitches"], bunt["pitches_in_bunt_pa"], bunt["pitches_in_bunt_pa_pct"]) == (20, 6, 30.0)

    counts = {}
    for row in by_name["outcome_category_counts"]["rows"]:
        counts[row["outcome_category"]] = counts.get(row["outcome_category"], 0) + row["rows"]
    assert counts == {"ball": 6, "foul": 3, "whiff": 1, "called_strike": 3, "bunt": 4, "in_play_out": 1, "in_play_home_run": 1, "unclassified": 1}
    assert by_name["outcome_category_counts"]["backend"] == "sqlite"

    zone = by_name["strike_zone_definition"]["rows"][0]
    assert zone["batters"] == 7 and zone["pct_batters_constant_sz_top"] == pytest.approx(85.71, abs=0.01)
    assert zone["median_distinct_sz_top"] == 1 and zone["ratio_min"] == pytest.approx(((3.4 + 6 * 3.5) / 7) / 1.77, abs=1e-3)  # batter 101: one 3.4 and six 3.5 and zone["ratio_max"] == pytest.approx(3.5 / 1.77, abs=1e-3)
    assert {(r["p_throws"], r["stand"], r["rows"]) for r in by_name["handedness_groups"]["rows"]} == {("R", "R", 20)}


def test_coverage_by_outcome_counts_non_null_per_group_and_field(service):
    run = service.run("data_profile", {"scope": SCOPE, "sections": ["coverage_by_outcome"], "tracked_fields": ["miss_distance", "launch_speed", "bat_speed"]})["run"]
    rows = service.store.load_result(run["id"])["sections"][0]["rows"]
    got = {(r["outcome_group"], r["field"]): (r["rows"], r["non_null"], r["non_null_pct"]) for r in rows}
    assert got[("whiff", "miss_distance")] == (1, 1, 100.0)
    assert got[("foul", "launch_speed")] == (3, 1, 33.33)
    assert got[("foul", "bat_speed")] == (3, 3, 100.0) and got[("whiff", "bat_speed")] == (1, 1, 100.0)
    assert got[("ball", "miss_distance")][1] == 0 and got[("other", "miss_distance")][0] == 2  # foul_bunt and automatic_ball
    assert {r["outcome_group"] for r in rows} == {"whiff", "foul", "in_play", "called_strike", "ball", "other"}


def test_mirror_and_zone_sections(service):
    run = service.run("data_profile", {"scope": SCOPE, "sections": ["mirror_field_means", "zone_plate_check"]})["run"]
    by_name, _ = sections(run, service.store)
    means = {(r["p_throws"], r["stand"], r["field"]): r for r in by_name["mirror_field_means"]["rows"]}
    assert means[("R", "R", "spin_axis_share_below_180")]["mean"] == 0.0
    assert ("R", "R", "plate_x") in means and ("R", "R", "api_break_x_arm") in means
    assert by_name["zone_plate_check"]["rows"][0]["zone"] == 5 and by_name["zone_plate_check"]["rows"][0]["stand"] == "R"


def test_settings_are_validated_and_runs_are_reused(service):
    with pytest.raises(ConfigError, match="Unknown sections"):
        service.run("data_profile", {"scope": SCOPE, "sections": ["nope"]})
    with pytest.raises(ConfigError, match="At least one"):
        service.run("data_profile", {"scope": SCOPE, "sections": []})
    with pytest.raises(ConfigError, match="not in the pitches table"):
        service.run("data_profile", {"scope": SCOPE, "tracked_fields": ["not_a_column"]})
    with pytest.raises(ConfigError, match="scope is required"):
        service.run("data_profile", {})
    first = service.run("data_profile", {"scope": SCOPE})
    second = service.run("data_profile", {"scope": SCOPE})
    assert first["reused"] is False and second["reused"] is True
    assert first["run"]["config"]["tracked_fields"] == list(DEFAULT_TRACKED_FIELDS) and len(first["run"]["config"]["sections"]) == 11


def test_missing_columns_are_reported_per_section(tmp_path):
    db = tmp_path / "statcast.sqlite3"
    conn = sqlite3.connect(db)
    conn.executescript(
        "CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL);"
        "INSERT INTO settings VALUES ('data_revision','rev-1','t');"
        "CREATE TABLE pitches (pitch_uid TEXT, game_year INTEGER, game_type TEXT, game_date TEXT, game_pk INTEGER, description TEXT, _ingested_at TEXT);"
        "INSERT INTO pitches VALUES ('a',2024,'R','2024-05-01',1,'ball','t');"
    )
    conn.commit(); conn.close()
    svc = ResearchService(AppConfig(data_dir=str(tmp_path), analysis_backend="sqlite"), AnalysisFacade(db, backend="sqlite"))
    try:
        with pytest.raises(ConfigError, match="lacks columns"):
            svc.run("data_profile", {"scope": SCOPE, "tracked_fields": ["description"]})
        ok = svc.run("data_profile", {"scope": SCOPE, "sections": ["rows_by_season", "description_values"], "tracked_fields": ["description"]})
        assert ok["run"]["status"] == "success"
    finally:
        svc.close()


def test_cli_data_profile(tmp_path, capsys):
    root = tmp_path / "cli"
    make_profile_db(root / "data" / "statcast.sqlite3")
    config = root / "config.json"
    config.write_text(json.dumps({"data_dir": str(root / "data"), "analysis_backend": "sqlite"}), encoding="utf-8")
    cfg = root / "profile.json"
    cfg.write_text(json.dumps({"scope": SCOPE}), encoding="utf-8")
    assert main(["--config", str(config), "research", "run", "--kind", "data_profile", "--config-file", str(cfg), "--study", "P2 data profile"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["reused"] is False and out["run"]["summary"]["total_rows"] > 0 and out["run"]["studies"][0]["name"] == "P2 data profile"
