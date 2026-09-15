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


_NON_PLAY_STATUSES = {"POSTPONED", "SCHEDULED", "CANCELLED", "CANCELED"}


def _game_id(item: dict[str, Any]) -> str | None:
    value = item.get("GameId") or item.get("gameId")
    return str(value) if value not in (None, "") else None


def _schedule_status(item: dict[str, Any]) -> str:
    value = item.get("GameStatus") or item.get("Status") or item.get("GameStatusName") or ""
    return str(value).strip().upper()


def _schedule_indicates_play(item: dict[str, Any]) -> bool:
    """Whether this schedule occurrence can establish the baseball game date.

    Historical CPBL schedule queries retain the same GameId across reschedules.
    POSTPONED means that occurrence never started and therefore must not receive
    the later completed game's LiveLog. RESERVED is different: it is CPBL's
    suspended/reserved-game state and means play occurred, so its first date is
    the stable game date even when the detail endpoint later moves PreExeDate to
    another resume date.
    """
    status = _schedule_status(item)
    return bool(status and status not in _NON_PLAY_STATUSES)


def _has_trackman(rows: list[dict[str, Any]]) -> bool:
    return any(int(row.get("cpbl_has_trackman") or 0) == 1 for row in rows)


def _existing_game_date(store: StatcastStore, game_id: str) -> str | None:
    """Return a previously established canonical game date, if any."""
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


def _raw_canonical_game_dates(archive: CPBLRawArchive) -> dict[str, str]:
    """Recover stable game dates from archived schedule state.

    Prefer the earliest occurrence whose schedule state proves that play took
    place (e.g. RESERVED or FINISHED). This deliberately skips POSTPONED dates.
    If a provider version has no status at all, keep the earliest schedule date
    only as a fallback so old archives remain rebuildable.
    """
    played: dict[str, str] = {}
    fallback: dict[str, str] = {}
    for path in archive.iter_schedules():
        snapshot, payload = archive.read_verified(path)
        if not isinstance(payload, list):
            continue
        for item in payload:
            if not isinstance(item, dict):
                continue
            game_id = _game_id(item)
            if not game_id:
                continue
            fallback.setdefault(game_id, snapshot.start_date)
            if _schedule_indicates_play(item):
                old = played.get(game_id)
                if old is None or snapshot.start_date < old:
                    played[game_id] = snapshot.start_date
    return {game_id: played.get(game_id, day) for game_id, day in fallback.items()}


def _raw_non_play_only_game_ids(archive: CPBLRawArchive) -> set[str]:
    """GameIds whose archived schedule history contains only explicit non-play states.

    This is used by raw rebuild so it follows the same source contract as live
    ingestion. If a GameId was POSTPONED/SCHEDULED/CANCELLED on every explicit
    schedule occurrence, an archived detail response must not create pitches.
    A later RESERVED/START/FINISHED occurrence makes the game ingestible. Raw
    archives with no explicit schedule status remain rebuildable for backwards
    compatibility.
    """
    statuses: dict[str, set[str]] = {}
    for path in archive.iter_schedules():
        _, payload = archive.read_verified(path)
        if not isinstance(payload, list):
            continue
        for item in payload:
            if not isinstance(item, dict):
                continue
            game_id = _game_id(item)
            status = _schedule_status(item)
            if not game_id or not status:
                continue
            statuses.setdefault(game_id, set()).add(status)
    return {
        game_id
        for game_id, values in statuses.items()
        if values and all(value in _NON_PLAY_STATUSES for value in values)
    }


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

                            # Explicit non-play schedule states can still point at
                            # a populated detail payload. Archive that response,
                            # but never ingest its LiveLog for an occurrence on
                            # which no baseball was played.
                            if _schedule_status(summary) in _NON_PLAY_STATUSES:
                                continue

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
        canonical_dates = _raw_canonical_game_dates(self.archive)
        non_play_only = _raw_non_play_only_game_ids(self.archive)
        with StatcastStore(self.database_path) as store:
            for path in self.archive.iter_games():
                snapshot, game = self.archive.read_verified(path)
                game_id = str(game.get("GameId") or game.get("gameId") or "") if isinstance(game, dict) else ""
                store.record_snapshot(snapshot)
                totals.games += 1
                if game_id and game_id in non_play_only:
                    continue
                stable_date = _existing_game_date(store, game_id) if game_id else None
                chosen_date = stable_date or canonical_dates.get(game_id) or snapshot.start_date
                rows = normalize_game(game, fallback_date=date.fromisoformat(chosen_date))
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
