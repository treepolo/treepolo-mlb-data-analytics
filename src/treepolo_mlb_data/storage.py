from __future__ import annotations

import csv
import hashlib
import io
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .schema import CURRENT_DOCUMENTED_COLUMNS, SAFE_COLUMN, quote_ident, sqlite_type

META_COLUMNS = {
    "pitch_uid": "TEXT PRIMARY KEY",
    "_row_hash": "TEXT NOT NULL",
    "_source_snapshot_id": "TEXT NOT NULL",
    "_ingested_at": "TEXT NOT NULL",
    "_invalid_headers_json": "TEXT",
}


@dataclass(slots=True)
class IngestStats:
    received: int = 0
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    missing_key: int = 0
    new_columns: tuple[str, ...] = ()


def _to_int(value: str | None):
    if value is None or value == "": return None
    try: return int(float(value))
    except ValueError: return None


def _stable_uid(row: dict[str, str]) -> tuple[str, bool]:
    key = [row.get("game_pk"), row.get("at_bat_number"), row.get("pitch_number")]
    if all(v not in (None, "") for v in key):
        return ":".join(str(v) for v in key), False
    fallback_fields = [
        row.get("game_date", ""), row.get("game_pk", ""), row.get("inning", ""),
        row.get("inning_topbot", ""), row.get("pitcher", ""), row.get("batter", ""),
        row.get("at_bat_number", ""), row.get("pitch_number", ""), row.get("description", ""),
    ]
    return "fallback:" + hashlib.sha256("|".join(fallback_fields).encode()).hexdigest(), True


def _row_hash(row: dict[str, str]) -> str:
    canonical = json.dumps(row, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class StatcastStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._init_meta()

    def close(self):
        self.conn.close()

    def __enter__(self): return self
    def __exit__(self, exc_type, exc, tb): self.close()

    def _init_meta(self) -> None:
        self.conn.executescript("""
        CREATE TABLE IF NOT EXISTS sync_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL, start_date TEXT, end_date TEXT,
            started_at TEXT NOT NULL, finished_at TEXT, status TEXT NOT NULL,
            rows_received INTEGER NOT NULL DEFAULT 0,
            rows_inserted INTEGER NOT NULL DEFAULT 0,
            rows_updated INTEGER NOT NULL DEFAULT 0,
            error TEXT
        );
        CREATE TABLE IF NOT EXISTS sync_chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL REFERENCES sync_runs(id) ON DELETE CASCADE,
            start_date TEXT NOT NULL, end_date TEXT NOT NULL,
            status TEXT NOT NULL, snapshot_id TEXT,
            rows_received INTEGER NOT NULL DEFAULT 0,
            rows_inserted INTEGER NOT NULL DEFAULT 0,
            rows_updated INTEGER NOT NULL DEFAULT 0,
            error TEXT, UNIQUE(run_id, start_date, end_date)
        );
        CREATE TABLE IF NOT EXISTS schema_registry (
            column_name TEXT PRIMARY KEY, sqlite_type TEXT NOT NULL,
            first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
            first_snapshot_id TEXT NOT NULL, last_snapshot_id TEXT NOT NULL,
            is_documented INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS schema_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            seen_at TEXT NOT NULL, snapshot_id TEXT NOT NULL,
            event_type TEXT NOT NULL, column_name TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS raw_snapshots (
            snapshot_id TEXT PRIMARY KEY, start_date TEXT NOT NULL, end_date TEXT NOT NULL,
            fetched_at TEXT NOT NULL, sha256 TEXT NOT NULL, bytes_uncompressed INTEGER NOT NULL,
            path TEXT NOT NULL
        );
        """)
        self.conn.commit()

    def ensure_pitch_table(self, headers: Iterable[str], snapshot_id: str) -> list[str]:
        valid = [h for h in headers if h and SAFE_COLUMN.fullmatch(h)]
        exists = self.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='pitches'").fetchone()
        now = datetime.now(timezone.utc).isoformat()
        if not exists:
            definitions = [f'{quote_ident(k)} {v}' for k, v in META_COLUMNS.items()]
            definitions += [f'{quote_ident(h)} {sqlite_type(h)}' for h in valid]
            self.conn.execute(f"CREATE TABLE pitches ({', '.join(definitions)})")
            new = list(valid)
        else:
            current = {row[1] for row in self.conn.execute("PRAGMA table_info(pitches)")}
            new = [h for h in valid if h not in current]
            for h in new:
                self.conn.execute(f"ALTER TABLE pitches ADD COLUMN {quote_ident(h)} {sqlite_type(h)}")
        for h in valid:
            old = self.conn.execute("SELECT column_name FROM schema_registry WHERE column_name=?", (h,)).fetchone()
            self.conn.execute("""
                INSERT INTO schema_registry(column_name, sqlite_type, first_seen_at, last_seen_at,
                  first_snapshot_id, last_snapshot_id, is_documented)
                VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(column_name) DO UPDATE SET last_seen_at=excluded.last_seen_at,
                  last_snapshot_id=excluded.last_snapshot_id
            """, (h, sqlite_type(h), now, now, snapshot_id, snapshot_id, int(h in CURRENT_DOCUMENTED_COLUMNS)))
            if old is None:
                self.conn.execute(
                    "INSERT INTO schema_events(seen_at,snapshot_id,event_type,column_name) VALUES(?,?,?,?)",
                    (now, snapshot_id, "new_column", h),
                )
        self.conn.commit()
        return new

    def ingest_csv(self, payload: bytes, snapshot_id: str, batch_size: int = 2000) -> IngestStats:
        text = payload.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text))
        headers = reader.fieldnames or []
        new_columns = self.ensure_pitch_table(headers, snapshot_id)
        table_columns = {row[1] for row in self.conn.execute("PRAGMA table_info(pitches)")}
        valid_headers = [h for h in headers if h in table_columns]
        invalid_headers = [h for h in headers if h not in table_columns]
        meta = list(META_COLUMNS)
        columns = meta + valid_headers
        quoted = ",".join(quote_ident(c) for c in columns)
        placeholders = ",".join("?" for _ in columns)
        mutable = [c for c in columns if c != "pitch_uid"]
        updates = ",".join(f"{quote_ident(c)}=excluded.{quote_ident(c)}" for c in mutable)
        sql = f"INSERT INTO pitches ({quoted}) VALUES ({placeholders}) ON CONFLICT(pitch_uid) DO UPDATE SET {updates}"
        stats = IngestStats(new_columns=tuple(new_columns))
        now = datetime.now(timezone.utc).isoformat()

        def flush(batch_rows: list[tuple[str, str, list]]) -> None:
            if not batch_rows:
                return
            existing: dict[str, str] = {}
            uids = [item[0] for item in batch_rows]
            for offset in range(0, len(uids), 400):
                part = uids[offset:offset + 400]
                marks = ",".join("?" for _ in part)
                for found in self.conn.execute(f"SELECT pitch_uid,_row_hash FROM pitches WHERE pitch_uid IN ({marks})", part):
                    existing[found[0]] = found[1]
            changed = []
            for uid, digest, values in batch_rows:
                old_hash = existing.get(uid)
                if old_hash is None:
                    stats.inserted += 1
                    changed.append(values)
                elif old_hash == digest:
                    stats.unchanged += 1
                else:
                    stats.updated += 1
                    changed.append(values)
            if changed:
                self.conn.executemany(sql, changed)

        batch: list[tuple[str, str, list]] = []
        for row in reader:
            if not any(v not in (None, "") for v in row.values()):
                continue
            stats.received += 1
            uid, missing = _stable_uid(row)
            stats.missing_key += int(missing)
            digest = _row_hash(row)
            invalid_json = json.dumps({h: row.get(h) for h in invalid_headers}, ensure_ascii=False) if invalid_headers else None
            values = [uid, digest, snapshot_id, now, invalid_json]
            for h in valid_headers:
                value = row.get(h)
                if value == "":
                    value = None
                typ = sqlite_type(h)
                if typ == "INTEGER" and value is not None:
                    value = _to_int(value)
                elif typ == "REAL" and value is not None:
                    try:
                        value = float(value)
                    except ValueError:
                        value = None
                values.append(value)
            batch.append((uid, digest, values))
            if len(batch) >= batch_size:
                flush(batch)
                batch.clear()
        flush(batch)
        self._ensure_indexes()
        self.conn.commit()
        return stats

    def _ensure_indexes(self) -> None:
        cols = {row[1] for row in self.conn.execute("PRAGMA table_info(pitches)")}
        for name, column in [
            ("idx_pitches_game_date", "game_date"), ("idx_pitches_game_pk", "game_pk"),
            ("idx_pitches_pitcher", "pitcher"), ("idx_pitches_batter", "batter"),
            ("idx_pitches_pitch_type", "pitch_type"),
        ]:
            if column in cols:
                self.conn.execute(f"CREATE INDEX IF NOT EXISTS {name} ON pitches({quote_ident(column)})")

    def record_snapshot(self, snapshot) -> None:
        self.conn.execute("""
            INSERT OR IGNORE INTO raw_snapshots(snapshot_id,start_date,end_date,fetched_at,sha256,bytes_uncompressed,path)
            VALUES(?,?,?,?,?,?,?)
        """, (snapshot.snapshot_id, snapshot.start_date, snapshot.end_date, snapshot.fetched_at,
              snapshot.sha256, snapshot.bytes_uncompressed, snapshot.path))
        self.conn.commit()

    def start_run(self, kind: str, start: str, end: str) -> int:
        now = datetime.now(timezone.utc).isoformat()
        cur = self.conn.execute(
            "INSERT INTO sync_runs(kind,start_date,end_date,started_at,status) VALUES(?,?,?,?,?)",
            (kind, start, end, now, "running"),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def record_chunk(self, run_id: int, start: str, end: str, status: str, snapshot_id: str | None,
                     stats: IngestStats | None = None, error: str | None = None) -> None:
        stats = stats or IngestStats()
        self.conn.execute("""
            INSERT INTO sync_chunks(run_id,start_date,end_date,status,snapshot_id,rows_received,rows_inserted,rows_updated,error)
            VALUES(?,?,?,?,?,?,?,?,?)
        """, (run_id, start, end, status, snapshot_id, stats.received, stats.inserted, stats.updated, error))
        self.conn.commit()

    def finish_run(self, run_id: int, status: str, error: str | None = None) -> None:
        now = datetime.now(timezone.utc).isoformat()
        totals = self.conn.execute("""
            SELECT COALESCE(SUM(rows_received),0), COALESCE(SUM(rows_inserted),0), COALESCE(SUM(rows_updated),0)
            FROM sync_chunks WHERE run_id=?
        """, (run_id,)).fetchone()
        self.conn.execute("""
            UPDATE sync_runs SET finished_at=?, status=?, rows_received=?, rows_inserted=?, rows_updated=?, error=?
            WHERE id=?
        """, (now, status, totals[0], totals[1], totals[2], error, run_id))
        self.conn.commit()

    def latest_game_date(self) -> str | None:
        exists = self.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='pitches'").fetchone()
        if not exists: return None
        cols = {row[1] for row in self.conn.execute("PRAGMA table_info(pitches)")}
        if "game_date" not in cols: return None
        row = self.conn.execute("SELECT MAX(game_date) FROM pitches").fetchone()
        return row[0] if row else None

    def set_setting(self, key: str, value: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute("""
            INSERT INTO settings(key,value,updated_at) VALUES(?,?,?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
        """, (key, value, now))
        self.conn.commit()

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        row = self.conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def has_successful_chunk(self, start: str, end: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM sync_chunks WHERE start_date=? AND end_date=? AND status='success' LIMIT 1",
            (start, end),
        ).fetchone()
        return row is not None

    def failed_chunk_ranges(self) -> list[tuple[str, str]]:
        return [(r[0], r[1]) for r in self.conn.execute(
            "SELECT start_date,end_date FROM sync_chunks WHERE status='failed' GROUP BY start_date,end_date ORDER BY start_date"
        )]

    def verify(self) -> dict:
        exists = self.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='pitches'").fetchone()
        failed_details = [dict(r) for r in self.conn.execute(
            "SELECT start_date,end_date,error FROM sync_chunks WHERE status='failed' ORDER BY id DESC LIMIT 20"
        )]
        if not exists:
            return {"pitch_rows": 0, "games": 0, "duplicate_pitch_uid": 0, "missing_natural_key": 0,
                    "missing_required_ids": {}, "latest_game_date": None, "failed_chunks": len(failed_details),
                    "failed_chunk_details": failed_details, "schema_columns": 0, "new_or_undocumented_columns": [],
                    "columns_missing_from_latest_snapshot": [], "raw_snapshots": 0}
        cols = {row[1] for row in self.conn.execute("PRAGMA table_info(pitches)")}
        games = self.conn.execute("SELECT COUNT(DISTINCT game_pk) FROM pitches").fetchone()[0] if "game_pk" in cols else 0
        missing = self.conn.execute("SELECT COUNT(*) FROM pitches WHERE pitch_uid LIKE 'fallback:%'").fetchone()[0]
        duplicate = self.conn.execute("SELECT COUNT(*) FROM (SELECT pitch_uid FROM pitches GROUP BY pitch_uid HAVING COUNT(*)>1)").fetchone()[0]
        failed = self.conn.execute("SELECT COUNT(*) FROM sync_chunks WHERE status='failed'").fetchone()[0]
        undocumented = [r[0] for r in self.conn.execute("SELECT column_name FROM schema_registry WHERE is_documented=0 ORDER BY column_name")]
        latest_snapshot = self.conn.execute("SELECT snapshot_id FROM raw_snapshots ORDER BY fetched_at DESC LIMIT 1").fetchone()
        missing_latest = []
        if latest_snapshot:
            missing_latest = [r[0] for r in self.conn.execute(
                "SELECT column_name FROM schema_registry WHERE last_snapshot_id<>? ORDER BY column_name",
                (latest_snapshot[0],),
            )]
        missing_ids = {}
        for column in ("game_pk", "at_bat_number", "pitch_number", "pitcher", "batter"):
            missing_ids[column] = self.conn.execute(
                f"SELECT COUNT(*) FROM pitches WHERE {quote_ident(column)} IS NULL"
            ).fetchone()[0] if column in cols else None
        return {
            "pitch_rows": self.conn.execute("SELECT COUNT(*) FROM pitches").fetchone()[0],
            "games": games,
            "duplicate_pitch_uid": duplicate,
            "missing_natural_key": missing,
            "missing_required_ids": missing_ids,
            "latest_game_date": self.latest_game_date(),
            "failed_chunks": failed,
            "failed_chunk_details": failed_details,
            "schema_columns": self.conn.execute("SELECT COUNT(*) FROM schema_registry").fetchone()[0],
            "new_or_undocumented_columns": undocumented,
            "columns_missing_from_latest_snapshot": missing_latest,
            "raw_snapshots": self.conn.execute("SELECT COUNT(*) FROM raw_snapshots").fetchone()[0],
        }

