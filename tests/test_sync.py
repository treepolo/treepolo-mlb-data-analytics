from datetime import date
from pathlib import Path

from treepolo_mlb_data.config import AppConfig
from treepolo_mlb_data.raw import RawArchive
from treepolo_mlb_data.storage import StatcastStore
from treepolo_mlb_data.sync import SyncEngine, chunk_ranges

HEADER = "pitch_type,game_date,game_pk,at_bat_number,pitch_number,pitcher,batter,description\n"


class FakeFetcher:
    def __init__(self): self.calls = []
    def fetch(self, start, end):
        self.calls.append((start, end))
        rows = ["pitch_type,game_date,game_pk,at_bat_number,pitch_number,pitcher,batter,description"]
        cur = start
        from datetime import timedelta
        while cur <= end:
            game_pk = int(cur.strftime("%Y%m%d"))
            rows.append(f"FF,{cur.isoformat()},{game_pk},1,1,10,20,called_strike")
            cur += timedelta(days=1)
        return ("\n".join(rows) + "\n").encode()


def test_chunk_ranges():
    chunks = list(chunk_ranges(date(2024,1,1), date(2024,1,12), 5))
    assert chunks == [(date(2024,1,1),date(2024,1,5)), (date(2024,1,6),date(2024,1,10)), (date(2024,1,11),date(2024,1,12))]


def test_backfill_and_raw_archive(tmp_path: Path):
    cfg = AppConfig(data_dir=str(tmp_path), backfill_chunk_days=2)
    fetcher = FakeFetcher()
    with StatcastStore(cfg.database_path) as store:
        engine = SyncEngine(cfg, store, fetcher, RawArchive(cfg.root))
        result = engine.backfill(date(2024,1,1), date(2024,1,3))
        assert result.status == "success" and result.chunks == 2 and result.inserted == 3
        assert store.verify()["raw_snapshots"] == 2
        assert len(list((tmp_path / "raw").glob("**/*.csv.gz"))) == 2


class FailingFetcher(FakeFetcher):
    def fetch(self, start, end):
        if start == date(2024, 1, 3):
            raise RuntimeError("boom")
        return super().fetch(start, end)


def test_partial_failure_and_retry(tmp_path: Path):
    cfg = AppConfig(data_dir=str(tmp_path), backfill_chunk_days=2)
    fetcher = FailingFetcher()
    with StatcastStore(cfg.database_path) as store:
        engine = SyncEngine(cfg, store, fetcher, RawArchive(cfg.root))
        result = engine.backfill(date(2024,1,1), date(2024,1,4), continue_on_error=True)
        assert result.status == "partial"
        report = store.verify()
        assert report["failed_chunks"] == 1
        assert report["failed_chunk_details"][0]["start_date"] == "2024-01-03"
        engine.fetcher = FakeFetcher()
        retried = engine.retry_failed()
        assert len(retried) == 1 and retried[0].status == "success"
        assert store.verify()["pitch_rows"] == 4


def test_backfill_resume_skips_completed_chunks(tmp_path: Path):
    cfg = AppConfig(data_dir=str(tmp_path), backfill_chunk_days=2)
    fetcher = FakeFetcher()
    with StatcastStore(cfg.database_path) as store:
        engine = SyncEngine(cfg, store, fetcher, RawArchive(cfg.root))
        engine.backfill(date(2024,1,1), date(2024,1,4))
        before = len(fetcher.calls)
        result = engine.backfill(date(2024,1,1), date(2024,1,4), resume=True)
        assert result.skipped == 2 and result.chunks == 0
        assert len(fetcher.calls) == before


def test_update_requires_initial_backfill(tmp_path: Path):
    cfg = AppConfig(data_dir=str(tmp_path))
    with StatcastStore(cfg.database_path) as store:
        engine = SyncEngine(cfg, store, FakeFetcher(), RawArchive(cfg.root))
        try:
            engine.update(date(2024,1,5))
        except RuntimeError as exc:
            assert "backfill" in str(exc)
        else:
            raise AssertionError("update should require initial backfill")


def test_rebuild_from_raw_validates_and_restores_snapshot_metadata(tmp_path: Path):
    cfg = AppConfig(data_dir=str(tmp_path), backfill_chunk_days=2)
    archive = RawArchive(cfg.root)
    with StatcastStore(cfg.database_path) as store:
        engine = SyncEngine(cfg, store, FakeFetcher(), archive)
        engine.backfill(date(2024,1,1), date(2024,1,2))
    cfg.database_path.unlink()
    with StatcastStore(cfg.database_path) as rebuilt:
        engine = SyncEngine(cfg, rebuilt, FakeFetcher(), archive)
        assert engine.rebuild_from_raw() == 1
        report = rebuilt.verify()
        assert report["pitch_rows"] == 2
        assert report["raw_snapshots"] == 1
