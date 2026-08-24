from datetime import date
from pathlib import Path

from treepolo_mlb_data.config import AppConfig
from treepolo_mlb_data.raw import RawArchive
from treepolo_mlb_data.storage import StatcastStore
from treepolo_mlb_data.sync import SyncEngine
from treepolo_mlb_data.sync_progress import get_sync_progress
from treepolo_mlb_data.webapp import AppServices, STATIC_DIR


class FakeFetcher:
    def fetch(self, start, end):
        rows = ["pitch_type,game_date,game_pk,at_bat_number,pitch_number,pitcher,batter,description"]
        cur = start
        from datetime import timedelta
        while cur <= end:
            game_pk = int(cur.strftime("%Y%m%d"))
            rows.append(f"FF,{cur.isoformat()},{game_pk},1,1,10,20,called_strike")
            cur += timedelta(days=1)
        return ("\n".join(rows) + "\n").encode()


class FailingFetcher(FakeFetcher):
    def fetch(self, start, end):
        if start == date(2024, 1, 3):
            raise RuntimeError("boom")
        return super().fetch(start, end)


def test_backfill_progress_reaches_completion(tmp_path: Path):
    cfg = AppConfig(data_dir=str(tmp_path), backfill_chunk_days=2)
    with StatcastStore(cfg.database_path) as store:
        engine = SyncEngine(cfg, store, FakeFetcher(), RawArchive(cfg.root))
        result = engine.backfill(date(2024, 1, 1), date(2024, 1, 5))
    progress = get_sync_progress("backfill")
    assert result.status == "success"
    assert progress is not None
    assert progress["status"] == "success"
    assert progress["total_chunks"] == 3
    assert progress["completed_chunks"] == 3
    assert progress["success_chunks"] == 3
    assert progress["rows_received"] == 5
    assert progress["percent"] == 100.0
    assert progress["eta_seconds"] == 0.0


def test_backfill_progress_counts_resumed_chunks(tmp_path: Path):
    cfg = AppConfig(data_dir=str(tmp_path), backfill_chunk_days=2)
    fetcher = FakeFetcher()
    with StatcastStore(cfg.database_path) as store:
        engine = SyncEngine(cfg, store, fetcher, RawArchive(cfg.root))
        engine.backfill(date(2024, 1, 1), date(2024, 1, 4))
        result = engine.backfill(date(2024, 1, 1), date(2024, 1, 4), resume=True)
    progress = get_sync_progress("backfill")
    assert result.skipped == 2
    assert progress is not None
    assert progress["completed_chunks"] == 2
    assert progress["skipped_chunks"] == 2
    assert progress["rows_received"] == 0
    assert progress["percent"] == 100.0


def test_backfill_progress_reports_partial_failure(tmp_path: Path):
    cfg = AppConfig(data_dir=str(tmp_path), backfill_chunk_days=2)
    with StatcastStore(cfg.database_path) as store:
        engine = SyncEngine(cfg, store, FailingFetcher(), RawArchive(cfg.root))
        result = engine.backfill(date(2024, 1, 1), date(2024, 1, 4), continue_on_error=True)
    progress = get_sync_progress("backfill")
    assert result.status == "partial"
    assert progress is not None
    assert progress["status"] == "partial"
    assert progress["completed_chunks"] == 2
    assert progress["failed_chunks"] == 1
    assert progress["percent"] == 100.0


def test_status_exposes_backfill_progress(tmp_path: Path):
    cfg = AppConfig(data_dir=str(tmp_path), backfill_chunk_days=2)
    with StatcastStore(cfg.database_path) as store:
        SyncEngine(cfg, store, FakeFetcher(), RawArchive(cfg.root)).backfill(
            date(2024, 1, 1), date(2024, 1, 2)
        )
    status = AppServices(cfg).status()
    assert status["backfill_progress"]["status"] == "success"
    assert status["backfill_progress"]["percent"] == 100.0


def test_progress_frontend_is_packaged_and_bilingual():
    js = (STATIC_DIR / "backfill-progress.js").read_text(encoding="utf-8")
    assert "下載進度 Backfill Progress" in js
    assert "預估剩餘 ETA" in js
    assert "/api/data/backfill-progress" in js
