from __future__ import annotations

import csv
import io
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_CACHE_TABLE = "app_status_cache"
_GAME_TABLE = "app_status_games"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(f"""
    CREATE TABLE IF NOT EXISTS {_CACHE_TABLE} (
        id INTEGER PRIMARY KEY CHECK(id=1),
        state TEXT NOT NULL,
        pitch_rows INTEGER,
        games INTEGER,
        latest_game_date TEXT,
        missing_natural_key INTEGER,
        integrity_stale INTEGER NOT NULL DEFAULT 0,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS {_GAME_TABLE} (
        game_pk INTEGER PRIMARY KEY
    );
    """)


def prepare_fast_status(path: Path) -> bool:
    """Prepare the cache and return True when an existing DB needs one background bootstrap."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _connect(path) as conn:
        _ensure_tables(conn)
        pitches = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='pitches'"
        ).fetchone()
        row = conn.execute(f"SELECT state FROM {_CACHE_TABLE} WHERE id=1").fetchone()
        if not pitches:
            conn.execute(
                f"INSERT INTO {_CACHE_TABLE}(id,state,pitch_rows,games,latest_game_date,missing_natural_key,integrity_stale,updated_at) "
                "VALUES(1,'ready',0,0,NULL,0,0,?) "
                "ON CONFLICT(id) DO UPDATE SET state='ready',pitch_rows=0,games=0,latest_game_date=NULL,missing_natural_key=0,integrity_stale=0,updated_at=excluded.updated_at",
                (_now(),),
            )
            conn.execute(f"DELETE FROM {_GAME_TABLE}")
            conn.commit()
            return False
        if row is None or row[0] != "ready":
            conn.execute(
                f"INSERT INTO {_CACHE_TABLE}(id,state,updated_at) VALUES(1,'pending',?) "
                "ON CONFLICT(id) DO UPDATE SET state='pending',updated_at=excluded.updated_at",
                (_now(),),
            )
            conn.commit()
            return True
        return False


def mark_bootstrap_running(path: Path) -> None:
    with _connect(Path(path)) as conn:
        _ensure_tables(conn)
        conn.execute(
            f"INSERT INTO {_CACHE_TABLE}(id,state,updated_at) VALUES(1,'rebuilding',?) "
            "ON CONFLICT(id) DO UPDATE SET state='rebuilding',updated_at=excluded.updated_at",
            (_now(),),
        )
        conn.commit()


def rebuild_fast_status(path: Path) -> dict[str, Any]:
    """One-time exact scan for databases created before the persistent cache existed."""
    path = Path(path)
    mark_bootstrap_running(path)
    try:
        with _connect(path) as conn:
            _ensure_tables(conn)
            # Serialize this one-time snapshot against writers. This is never on
            # the HTTP request path; UI startup remains immediate.
            conn.execute("BEGIN IMMEDIATE")
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='pitches'"
            ).fetchone()
            if not exists:
                conn.rollback()
                prepare_fast_status(path)
                return read_fast_status(path)
            cols = {row[1] for row in conn.execute("PRAGMA table_info(pitches)")}
            fields = ["COUNT(*)"]
            fields.append("MAX(game_date)" if "game_date" in cols else "NULL")
            fields.append("SUM(CASE WHEN pitch_uid LIKE 'fallback:%' THEN 1 ELSE 0 END)")
            totals = conn.execute(f"SELECT {', '.join(fields)} FROM pitches").fetchone()
            conn.execute(f"DELETE FROM {_GAME_TABLE}")
            if "game_pk" in cols:
                conn.execute(
                    f"INSERT OR IGNORE INTO {_GAME_TABLE}(game_pk) SELECT DISTINCT game_pk FROM pitches WHERE game_pk IS NOT NULL"
                )
            games = conn.execute(f"SELECT COUNT(*) FROM {_GAME_TABLE}").fetchone()[0]
            conn.execute(
                f"UPDATE {_CACHE_TABLE} SET state='ready',pitch_rows=?,games=?,latest_game_date=?,missing_natural_key=?,integrity_stale=0,updated_at=? WHERE id=1",
                (int(totals[0] or 0), int(games or 0), totals[1], int(totals[2] or 0), _now()),
            )
            conn.commit()
        return read_fast_status(path)
    except Exception:
        with _connect(path) as conn:
            _ensure_tables(conn)
            conn.execute(f"UPDATE {_CACHE_TABLE} SET state='error',updated_at=? WHERE id=1", (_now(),))
            conn.commit()
        raise


def update_fast_status_after_ingest(path: Path, payload: bytes, inserted: int) -> None:
    """Increment the persistent cache using the just-downloaded chunk, never the full pitches table."""
    path = Path(path)
    game_ids: set[int] = set()
    latest: str | None = None
    reader = csv.DictReader(io.StringIO(payload.decode("utf-8-sig")))
    for row in reader:
        raw_game = row.get("game_pk")
        if raw_game not in (None, ""):
            try:
                game_ids.add(int(float(raw_game)))
            except ValueError:
                pass
        game_date = row.get("game_date")
        if game_date and (latest is None or game_date > latest):
            latest = game_date

    with _connect(path) as conn:
        _ensure_tables(conn)
        cache = conn.execute(f"SELECT * FROM {_CACHE_TABLE} WHERE id=1").fetchone()
        if cache is None or cache["state"] != "ready":
            return
        if game_ids:
            conn.executemany(
                f"INSERT OR IGNORE INTO {_GAME_TABLE}(game_pk) VALUES(?)",
                ((value,) for value in game_ids),
            )
        games = conn.execute(f"SELECT COUNT(*) FROM {_GAME_TABLE}").fetchone()[0]
        old_latest = cache["latest_game_date"]
        new_latest = max(value for value in (old_latest, latest) if value is not None) if (old_latest or latest) else None
        conn.execute(
            f"UPDATE {_CACHE_TABLE} SET pitch_rows=COALESCE(pitch_rows,0)+?,games=?,latest_game_date=?,integrity_stale=1,updated_at=? WHERE id=1",
            (int(inserted), int(games), new_latest, _now()),
        )
        conn.commit()


def read_fast_status(path: Path) -> dict[str, Any]:
    path = Path(path)
    with _connect(path) as conn:
        _ensure_tables(conn)
        cache = conn.execute(f"SELECT * FROM {_CACHE_TABLE} WHERE id=1").fetchone()
        if cache is None:
            return {
                "summary_state": "pending",
                "pitch_rows": None,
                "games": None,
                "latest_game_date": None,
                "missing_natural_key": None,
                "integrity_stale": True,
            }
        return {
            "summary_state": cache["state"],
            "pitch_rows": cache["pitch_rows"],
            "games": cache["games"],
            "latest_game_date": cache["latest_game_date"],
            "missing_natural_key": cache["missing_natural_key"],
            "integrity_stale": bool(cache["integrity_stale"]),
            "summary_updated_at": cache["updated_at"],
        }
