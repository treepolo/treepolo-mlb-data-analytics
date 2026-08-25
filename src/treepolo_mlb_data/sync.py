from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, timedelta

from .config import AppConfig
from .fast_status import update_fast_status_after_ingest
from .raw import RawArchive
from .storage import StatcastStore
from .sync_progress import complete_chunk, finish_sync, set_current_chunk, start_sync


@dataclass(slots=True)
class SyncResult:
    run_id: int
    status: str
    chunks: int
    received: int
    inserted: int
    updated: int
    skipped: int = 0


def chunk_ranges(start: date, end: date, chunk_days: int):
    if chunk_days < 1:
        raise ValueError("chunk_days must be >= 1")
    cur = start
    while cur <= end:
        chunk_end = min(end, cur + timedelta(days=chunk_days - 1))
        yield cur, chunk_end
        cur = chunk_end + timedelta(days=1)


class SyncEngine:
    def __init__(self, config: AppConfig, store: StatcastStore, fetcher, archive: RawArchive):
        self.config, self.store, self.fetcher, self.archive = config, store, fetcher, archive

    def sync(self, kind: str, start: date, end: date, *, chunk_days: int | None = None, continue_on_error: bool = False, resume: bool = False) -> SyncResult:
        if end < start:
            raise ValueError("end date must be >= start date")
        size = chunk_days or self.config.backfill_chunk_days
        ranges = list(chunk_ranges(start, end, size))
        run_id = self.store.start_run(kind, start.isoformat(), end.isoformat())
        start_sync(kind, run_id, start.isoformat(), end.isoformat(), len(ranges))
        total_received = total_inserted = total_updated = chunks = skipped = 0
        overall_status = "success"
        try:
            for cstart, cend in ranges:
                set_current_chunk(kind, cstart.isoformat(), cend.isoformat())
                if resume and self.store.has_successful_chunk(cstart.isoformat(), cend.isoformat()):
                    skipped += 1
                    complete_chunk(kind, status="skipped")
                    continue
                chunks += 1
                try:
                    payload = self.fetcher.fetch(cstart, cend)
                    snapshot = self.archive.save(cstart, cend, payload)
                    self.store.record_snapshot(snapshot)
                    stats = self.store.ingest_csv(payload, snapshot.snapshot_id)
                    update_fast_status_after_ingest(self.config.database_path, payload, stats.inserted)
                    self.store.record_chunk(run_id, cstart.isoformat(), cend.isoformat(), "success", snapshot.snapshot_id, stats)
                    total_received += stats.received
                    total_inserted += stats.inserted
                    total_updated += stats.updated
                    complete_chunk(kind, status="success", rows_received=stats.received)
                except Exception as exc:
                    overall_status = "partial" if continue_on_error else "failed"
                    self.store.record_chunk(run_id, cstart.isoformat(), cend.isoformat(), "failed", None, error=str(exc))
                    complete_chunk(kind, status="failed")
                    if not continue_on_error:
                        raise
            self.store.finish_run(run_id, overall_status)
            finish_sync(kind, overall_status)
        except Exception as exc:
            self.store.finish_run(run_id, "failed", str(exc))
            finish_sync(kind, "failed")
            raise
        return SyncResult(run_id, overall_status, chunks, total_received, total_inserted, total_updated, skipped)

    def backfill(self, start: date, end: date, *, continue_on_error: bool = True, resume: bool = False) -> SyncResult:
        return self.sync("backfill", start, end, continue_on_error=continue_on_error, resume=resume)

    def retry_failed(self) -> list[SyncResult]:
        results = []
        for start, end in self.store.failed_chunk_ranges():
            results.append(self.sync("retry_failed", date.fromisoformat(start), date.fromisoformat(end), chunk_days=(date.fromisoformat(end) - date.fromisoformat(start)).days + 1, continue_on_error=False))
        return results

    def update(self, today: date | None = None) -> SyncResult:
        today = today or date.today()
        latest = self.store.latest_game_date()
        if latest is None:
            raise RuntimeError("No local Statcast data found; run backfill before incremental update")
        correction_start = today - timedelta(days=max(0, self.config.recent_refresh_days - 1))
        next_unseen = date.fromisoformat(latest) + timedelta(days=1)
        return self.sync("update", min(correction_start, next_unseen), today, continue_on_error=False)

    def rebuild_from_raw(self) -> int:
        count = 0
        for path in self.archive.iter_snapshots():
            snapshot, payload = self.archive.read_verified(path)
            self.store.record_snapshot(snapshot)
            stats = self.store.ingest_csv(payload, snapshot.snapshot_id)
            update_fast_status_after_ingest(self.config.database_path, payload, stats.inserted)
            count += 1
        return count

    def scheduler(self, stop_after_one: bool = False) -> None:
        interval = max(1, self.config.auto_update_interval_hours) * 3600
        while True:
            enabled = self.store.get_setting("auto_update_enabled", str(self.config.auto_update_enabled).lower()) == "true"
            if enabled:
                if stop_after_one:
                    self.update()
                else:
                    try:
                        self.update()
                    except Exception as exc:
                        print(f"scheduled update failed: {exc}")
            if stop_after_one:
                return
            time.sleep(interval)
