from pathlib import Path

from treepolo_mlb_data.storage import StatcastStore

CSV1 = b'''pitch_type,game_date,game_pk,at_bat_number,pitch_number,pitcher,batter,release_speed,description\nFF,2024-04-01,1,10,1,100,200,96.1,called_strike\nSL,2024-04-01,1,10,2,100,200,85.0,swinging_strike\n'''
CSV2 = b'''pitch_type,game_date,game_pk,at_bat_number,pitch_number,pitcher,batter,release_speed,description,new_metric\nFF,2024-04-01,1,10,1,100,200,97.2,called_strike,abc\nSL,2024-04-01,1,10,2,100,200,85.0,swinging_strike,def\n'''


def test_idempotent_upsert_and_schema_evolution(tmp_path: Path):
    with StatcastStore(tmp_path / "db.sqlite3") as store:
        a = store.ingest_csv(CSV1, "s1")
        assert (a.received, a.inserted, a.updated, a.unchanged) == (2, 2, 0, 0)
        b = store.ingest_csv(CSV1, "s2")
        assert (b.inserted, b.updated, b.unchanged) == (0, 0, 2)
        c = store.ingest_csv(CSV2, "s3")
        assert c.new_columns == ("new_metric",)
        assert c.updated == 2
        row = store.conn.execute("SELECT release_speed,new_metric FROM pitches WHERE pitch_uid='1:10:1'").fetchone()
        assert row[0] == 97.2 and row[1] == "abc"
        report = store.verify()
        assert report["pitch_rows"] == 2
        assert report["duplicate_pitch_uid"] == 0
        assert "new_metric" in report["new_or_undocumented_columns"]


def test_missing_natural_key_is_preserved_and_reported(tmp_path: Path):
    payload = b"pitch_type,game_date,game_pk,at_bat_number,pitch_number,pitcher,batter\nFF,2024-04-01,1,10,,100,200\n"
    with StatcastStore(tmp_path / "db.sqlite3") as store:
        stats = store.ingest_csv(payload, "s1")
        assert stats.missing_key == 1
        report = store.verify()
        assert report["missing_natural_key"] == 1
        assert report["missing_required_ids"]["pitch_number"] == 1
