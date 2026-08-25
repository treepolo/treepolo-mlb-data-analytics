from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Callable

import duckdb

ProgressCallback = Callable[[str, float | None, str | None], None]

_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock_for(path: Path) -> threading.Lock:
    key = str(Path(path).resolve())
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.Lock())


def _notify(callback: ProgressCallback | None, stage: str, percentage: float | None = None, detail: str | None = None) -> None:
    if callback is not None:
        callback(stage, percentage, detail)


def _sql_string(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _source_state(sqlite_path: Path) -> tuple[str | None, tuple[tuple[str, str], ...]]:
    if not sqlite_path.exists():
        return None, ()
    with sqlite3.connect(sqlite_path) as conn:
        exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='pitches'").fetchone()
        if not exists:
            return None, ()
        schema = tuple((str(row[1]), str(row[2] or "TEXT").upper()) for row in conn.execute("PRAGMA table_info(pitches)"))
        token = None
        settings = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='settings'").fetchone()
        if settings:
            row = conn.execute("SELECT value,updated_at FROM settings WHERE key='data_revision'").fetchone()
            if row:
                token = f"revision:{row[0]}:{row[1]}"
        if token is None:
            cache = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='app_status_cache'").fetchone()
            if cache:
                row = conn.execute("SELECT updated_at FROM app_status_cache WHERE id=1").fetchone()
                if row and row[0]:
                    token = f"status:{row[0]}"
        if token is None:
            snapshots = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='raw_snapshots'").fetchone()
            if snapshots:
                row = conn.execute("SELECT MAX(fetched_at) FROM raw_snapshots").fetchone()
                if row and row[0]:
                    token = f"snapshot:{row[0]}"
        return token or "legacy", schema


def _duckdb_type(sqlite_type: str) -> str:
    typ = sqlite_type.upper()
    if "INT" in typ:
        return "BIGINT"
    if any(marker in typ for marker in ("REAL", "FLOA", "DOUB", "DEC", "NUM")):
        return "DOUBLE"
    if "BLOB" in typ:
        return "BLOB"
    return "VARCHAR"


def _execute_with_progress(
    conn,
    sql: str,
    params: list | None,
    progress: ProgressCallback | None,
    *,
    stage: str,
    base: float,
    span: float,
    detail: str,
) -> None:
    if progress is None:
        conn.execute(sql, params or [])
        return
    holder: dict[str, BaseException] = {}
    done = threading.Event()

    def worker() -> None:
        try:
            conn.execute(sql, params or [])
        except BaseException as exc:
            holder["error"] = exc
        finally:
            done.set()

    _notify(progress, stage, base, detail)
    thread = threading.Thread(target=worker, name=f"treepolo-{stage}", daemon=True)
    thread.start()
    while not done.wait(0.15):
        value: float | None = None
        try:
            raw = float(conn.query_progress())
            if raw >= 0:
                if raw <= 1.0:
                    raw *= 100.0
                value = base + max(0.0, min(raw, 100.0)) * span / 100.0
        except Exception:
            pass
        _notify(progress, stage, value, detail)
    thread.join()
    if "error" in holder:
        raise holder["error"]


class DuckDBMirror:
    """Persistent columnar analytical mirror of the normalized SQLite pitches table.

    SQLite remains the source of truth. DuckDB is rebuilt once for an existing
    installation and then refreshed incrementally from rows whose `_ingested_at`
    changed after the last mirror refresh.
    """

    def __init__(self, sqlite_path: Path, duckdb_path: Path):
        self.sqlite_path = Path(sqlite_path)
        self.duckdb_path = Path(duckdb_path)

    def _connect(self):
        self.duckdb_path.parent.mkdir(parents=True, exist_ok=True)
        conn = duckdb.connect(str(self.duckdb_path))
        conn.execute("CREATE TABLE IF NOT EXISTS analytics_meta(key VARCHAR PRIMARY KEY, value VARCHAR)")
        return conn

    @staticmethod
    def _meta(conn, key: str) -> str | None:
        row = conn.execute("SELECT value FROM analytics_meta WHERE key=?", [key]).fetchone()
        return str(row[0]) if row else None

    @staticmethod
    def _set_meta(conn, key: str, value: str | None) -> None:
        conn.execute("DELETE FROM analytics_meta WHERE key=?", [key])
        if value is not None:
            conn.execute("INSERT INTO analytics_meta VALUES (?,?)", [key, str(value)])

    @staticmethod
    def _has_pitches(conn) -> bool:
        return bool(conn.execute("SELECT 1 FROM information_schema.tables WHERE table_schema='main' AND table_name='pitches'").fetchone())

    @staticmethod
    def _mirror_schema(conn) -> tuple[tuple[str, str], ...]:
        if not DuckDBMirror._has_pitches(conn):
            return ()
        rows = conn.execute("DESCRIBE pitches").fetchall()
        normalized = []
        for row in rows:
            typ = str(row[1]).upper()
            if typ.startswith("VARCHAR"):
                typ = "VARCHAR"
            elif typ.startswith("BIGINT") or typ.startswith("INTEGER"):
                typ = "BIGINT"
            elif typ.startswith("DOUBLE") or typ.startswith("REAL") or typ.startswith("FLOAT"):
                typ = "DOUBLE"
            normalized.append((str(row[0]), typ))
        return tuple(normalized)

    @staticmethod
    def _load_sqlite(conn) -> None:
        try:
            conn.execute("LOAD sqlite")
        except Exception:
            conn.execute("INSTALL sqlite")
            conn.execute("LOAD sqlite")

    def _attach_source(self, conn) -> None:
        self._load_sqlite(conn)
        try:
            conn.execute("DETACH sqlite_src")
        except Exception:
            pass
        conn.execute(f"ATTACH {_sql_string(self.sqlite_path.resolve())} AS sqlite_src (TYPE sqlite, READ_ONLY)")

    def _rebuild(self, conn, source_token: str, progress: ProgressCallback | None) -> dict:
        self._attach_source(conn)
        conn.execute("DROP TABLE IF EXISTS pitches")
        _execute_with_progress(
            conn,
            "CREATE TABLE pitches AS SELECT * FROM sqlite_src.pitches",
            None,
            progress,
            stage="analytics_mirror_rebuild",
            base=4.0,
            span=15.0,
            detail="Building one-time DuckDB analytical mirror",
        )
        last = conn.execute('SELECT MAX("_ingested_at") FROM pitches').fetchone()[0]
        self._set_meta(conn, "source_token", source_token)
        self._set_meta(conn, "last_ingested_at", str(last) if last is not None else "")
        conn.execute("CHECKPOINT")
        try:
            conn.execute("DETACH sqlite_src")
        except Exception:
            pass
        _notify(progress, "analytics_mirror_ready", 20.0, "DuckDB analytical mirror ready")
        return {"state": "ready", "rebuilt": True, "changed_rows": None}

    def ensure(self, progress: ProgressCallback | None = None, *, force_rebuild: bool = False) -> dict:
        source_token, source_schema = _source_state(self.sqlite_path)
        if source_token is None or not source_schema:
            raise RuntimeError("Pitch data is not initialized yet")
        expected_schema = tuple((name, _duckdb_type(typ)) for name, typ in source_schema)

        _notify(progress, "analytics_mirror_wait", 3.0, "Checking DuckDB analytical mirror")
        with _lock_for(self.duckdb_path):
            conn = self._connect()
            try:
                if force_rebuild or not self._has_pitches(conn) or self._mirror_schema(conn) != expected_schema:
                    return self._rebuild(conn, source_token, progress)
                mirrored_token = self._meta(conn, "source_token")
                if mirrored_token == source_token:
                    _notify(progress, "analytics_mirror_ready", 20.0, "DuckDB analytical mirror already current")
                    return {"state": "ready", "rebuilt": False, "changed_rows": 0}

                last_ingested = self._meta(conn, "last_ingested_at")
                if last_ingested is None:
                    return self._rebuild(conn, source_token, progress)

                self._attach_source(conn)
                conn.execute("DROP TABLE IF EXISTS changed_pitches")
                _execute_with_progress(
                    conn,
                    'CREATE TEMP TABLE changed_pitches AS SELECT * FROM sqlite_src.pitches WHERE "_ingested_at" > ?',
                    [last_ingested],
                    progress,
                    stage="analytics_mirror_sync",
                    base=5.0,
                    span=10.0,
                    detail="Refreshing changed rows in DuckDB mirror",
                )
                changed = int(conn.execute("SELECT COUNT(*) FROM changed_pitches").fetchone()[0])
                if changed:
                    conn.execute('DELETE FROM pitches WHERE "pitch_uid" IN (SELECT "pitch_uid" FROM changed_pitches)')
                    conn.execute("INSERT INTO pitches SELECT * FROM changed_pitches")
                newest = conn.execute('SELECT MAX("_ingested_at") FROM pitches').fetchone()[0]
                self._set_meta(conn, "source_token", source_token)
                self._set_meta(conn, "last_ingested_at", str(newest) if newest is not None else last_ingested)
                conn.execute("CHECKPOINT")
                try:
                    conn.execute("DETACH sqlite_src")
                except Exception:
                    pass
                _notify(progress, "analytics_mirror_ready", 20.0, f"DuckDB mirror refreshed ({changed} changed rows)")
                return {"state": "ready", "rebuilt": False, "changed_rows": changed}
            finally:
                conn.close()


def refresh_existing_mirror(sqlite_path: Path, duckdb_path: Path) -> None:
    """Best-effort sync hook for data maintenance; do not create a mirror if none exists yet."""
    duckdb_path = Path(duckdb_path)
    if not duckdb_path.exists():
        return
    try:
        DuckDBMirror(sqlite_path, duckdb_path).ensure()
    except Exception as exc:
        # SQLite is authoritative: analytical acceleration must never make a
        # successful data ingest fail. The next analysis can rebuild/fallback.
        print(f"DuckDB mirror refresh failed: {exc}")
