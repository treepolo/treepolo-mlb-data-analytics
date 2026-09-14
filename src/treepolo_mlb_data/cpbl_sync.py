from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from .cpbl_client import CPBLClient
from .cpbl_normalize import normalize_game, rows_to_csv
from .cpbl_raw import CPBLRawArchive
from .duckdb_mirror import refresh_existing_mirror
from .fast_status import update_fast_status_after_ingest
from .storage import IngestStats, StatcastStore


@dataclass(slots=True)
class CPBLSyncStats:
    days: int = 0
    games: int = 0
    tracked_games: int = 0
    pitches: int = 0
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    errors: int = 0


def _game_id(item: dict[str, Any]) -> str | None:
    value = item.get("GameId") or item.get("gameId")
    return str(value) if value not in (None, "") else None


def _has_trackman(rows: list[dict[str, Any]]) -> bool:
    return any(int(row.get("cpbl_has_trackman") or 0) == 1 for row in rows)


def _existing_game_date(store: StatcastStore, game_id: str) -> str | None:
    """Return a previously established canonical game date, if any.

    CPBL can move a suspended game's detail-level PreExeDate to a future resume
    date while retaining the pitches already thrown. Once a game has pitch rows,
    the first schedule date that established those rows must remain stable on
    refresh/rebuild instead of drifting every time the resume date changes.
    """
    exists = store.conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='pitches'"
    ).fetchone()
    if not exists:
        return None
    columns = {str(row[1]) for row in store.conn.execute("PRAGMA table_info(pitches)")}
    if "cpbl_game_id" not in columns or "game_date" not in columns:
        return None
    row = store.conn.execute(
        "SELECT MIN(game_date) FROM pitches WHERE cpbl_game_id=? AND game_date IS NOT NULL",
        (game_id,),
    ).fetchone()
    return str(row[0]) if row and row[0] not in (None, "") else None


class CPBLSyncEngine:
    """Date/game based CPBL sync. SQLite is source of truth; raw JSON is rebuildable."""

    def __init__(
        self,
        database_path: Path,
        raw_root: Path,
        client: CPBLClient,
        *,
        analytics_database_path: Path | None = None,
        recent_refresh_days: int = 7,
        auto_update_interval_hours: int = 24,
    ):
        self.database_path = Path(database_path)
        self.analytics_database_path = Path(analytics_database_path) if analytics_database_path else None
        self.client = client
        self.archive = CPBLRawArchive(raw_root)
        self.recent_refresh_days = max(1, int(recent_refresh_days))
        self.auto_update_interval_hours = max(1, int(auto_update_interval_hours))

    def backfill(
        self,
        start: date,
        end: date,
        *,
        continue_on_error: bool = True,
        resume: bool = True,
    ) -> CPBLSyncStats:
        if end < start:
            raise ValueError("end date must not be before start date")
        totals = CPBLSyncStats()
        with StatcastStore(self.database_path) as store:
            run_id = store.start_run("cpbl_backfill", start.isoformat(), end.isoformat())
            day = start
            try:
                while day <= end:
                    if resume and store.has_successful_chunk(day.isoformat(), day.isoformat()):
                        day += timedelta(days=1)
                        continue
                    day_stats = IngestStats()
                    try:
                        schedule = self.client.schedule(day)
                        schedule_record = self.archive.save_schedule(day, schedule)
                        store.record_snapshot(schedule_record.snapshot)
                        totals.days += 1
                        for summary in schedule:
                            game_id = _game_id(summary)
                            if not game_id:
                                continue
                            totals.games += 1
                            game = self.client.game(game_id)
                            record = self.archive.save_game(game_id, day, game)
                            store.record_snapshot(record.snapshot)
                            stable_date = _existing_game_date(store, game_id)
                            canonical_day = date.fromisoformat(stable_date) if stable_date else day
                            rows = normalize_game(game, fallback_date=canonical_day)
                            if not rows:
                                continue
                            payload = rows_to_csv(rows)
                            if _has_trackman(rows):
                                totals.tracked_games += 1
                            stats = store.ingest_csv(payload, record.snapshot.snapshot_id)
                            update_fast_status_after_ingest(self.database_path, payload, stats.inserted)
                            day_stats.received += stats.received
                            day_stats.inserted += stats.inserted
                            day_stats.updated += stats.updated
                            day_stats.unchanged += stats.unchanged
                            day_stats.missing_key += stats.missing_key
                            totals.pitches += stats.received
                            totals.inserted += stats.inserted
                            totals.updated += stats.updated
                            totals.unchanged += stats.unchanged
                        store.record_chunk(
                            run_id,
                            day.isoformat(),
                            day.isoformat(),
                            "success",
                            schedule_record.snapshot.snapshot_id,
                            day_stats,
                        )
                    except Exception as exc:
                        totals.errors += 1
                        store.record_chunk(
                            run_id,
                            day.isoformat(),
                            day.isoformat(),
                            "failed",
                            None,
                            day_stats,
                            str(exc),
                        )
                        if not continue_on_error:
                            raise
                    day += timedelta(days=1)
                store.finish_run(run_id, "success" if not totals.errors else "partial")
            except Exception as exc:
                store.finish_run(run_id, "failed", str(exc))
                raise
        if self.analytics_database_path is not None:
            refresh_existing_mirror(self.database_path, self.analytics_database_path)
        return totals

    def update(self, through: date | None = None, *, recent_days: int | None = None) -> CPBLSyncStats:
        through = through or date.today()
        days = self.recent_refresh_days if recent_days is None else max(1, int(recent_days))
        start = through - timedelta(days=days - 1)
        return self.backfill(start, through, continue_on_error=True, resume=False)

    def retry_failed(self) -> list[CPBLSyncStats]:
        with StatcastStore(self.database_path) as store:
            ranges = store.failed_chunk_ranges()
        results = []
        for start, end in ranges:
            results.append(
                self.backfill(
                    date.fromisoformat(start),
                    date.fromisoformat(end),
                    continue_on_error=True,
                    resume=False,
                )
            )
        return results

    def scheduler(self, stop_after_one: bool = False) -> None:
        while True:
            with StatcastStore(self.database_path) as store:
                enabled = store.get_setting("auto_update_enabled", "false") == "true"
            if enabled:
                self.update()
            if stop_after_one:
                return
            time.sleep(self.auto_update_interval_hours * 3600)

    def rebuild_from_raw(self) -> CPBLSyncStats:
        totals = CPBLSyncStats()
        with StatcastStore(self.database_path) as store:
            for path in self.archive.iter_games():
                snapshot, game = self.archive.read_verified(path)
                game_id = str(game.get("GameId") or game.get("gameId") or "") if isinstance(game, dict) else ""
                stable_date = _existing_game_date(store, game_id) if game_id else None
                canonical_day = date.fromisoformat(stable_date) if stable_date else date.fromisoformat(snapshot.start_date)
                rows = normalize_game(game, fallback_date=canonical_day)
                store.record_snapshot(snapshot)
                totals.games += 1
                if not rows:
                    continue
                payload = rows_to_csv(rows)
                stats = store.ingest_csv(payload, snapshot.snapshot_id)
                update_fast_status_after_ingest(self.database_path, payload, stats.inserted)
                if _has_trackman(rows):
                    totals.tracked_games += 1
                totals.pitches += stats.received
                totals.inserted += stats.inserted
                totals.updated += stats.updated
                totals.unchanged += stats.unchanged
        if self.analytics_database_path is not None:
            refresh_existing_mirror(self.database_path, self.analytics_database_path)
        return totals
