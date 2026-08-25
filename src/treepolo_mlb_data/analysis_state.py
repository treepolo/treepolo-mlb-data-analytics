from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CACHE_FORMAT_VERSION = "stage4-v1"
DEFAULT_MAX_CACHE_ENTRIES = 200
DEFAULT_MAX_RESULT_BYTES = 8 * 1024 * 1024


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def read_data_revision(database_path: Path) -> str:
    path = Path(database_path)
    if not path.exists():
        return "missing"
    try:
        conn = sqlite3.connect(path)
        try:
            row = conn.execute("SELECT value FROM settings WHERE key='data_revision'").fetchone()
            if row and row[0]:
                return str(row[0])
        finally:
            conn.close()
    except sqlite3.Error:
        pass
    stat = path.stat()
    return f"legacy:{stat.st_size}:{stat.st_mtime_ns}"


def analysis_cache_key(*, payload: dict[str, Any], data_revision: str, backend: str) -> str:
    material = canonical_json({
        "format": CACHE_FORMAT_VERSION,
        "data_revision": data_revision,
        "backend": backend,
        "payload": payload,
    })
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


class AnalysisStateStore:
    """Persistent cache/history/saved-analysis state kept outside Statcast truth data."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, timeout=30.0, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self._init_schema()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()

    def _init_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS result_cache (
                cache_key TEXT PRIMARY KEY,
                data_revision TEXT NOT NULL,
                backend TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                result_json TEXT NOT NULL,
                result_bytes INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                last_accessed_at TEXT NOT NULL,
                hit_count INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_result_cache_last_accessed
                ON result_cache(last_accessed_at DESC);

            CREATE TABLE IF NOT EXISTS analysis_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                mode TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                cache_key TEXT,
                data_revision TEXT NOT NULL,
                backend TEXT,
                row_count INTEGER,
                status TEXT NOT NULL,
                error TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_analysis_history_created
                ON analysis_history(created_at DESC);

            CREATE TABLE IF NOT EXISTS saved_analyses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                notes TEXT NOT NULL DEFAULT '',
                payload_json TEXT NOT NULL,
                cache_key TEXT,
                data_revision TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_saved_analyses_updated
                ON saved_analyses(updated_at DESC);
            """
        )
        self.conn.commit()

    def get_cached_result(self, cache_key: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT result_json FROM result_cache WHERE cache_key=?", (cache_key,)
        ).fetchone()
        if row is None:
            return None
        self.conn.execute(
            "UPDATE result_cache SET last_accessed_at=?, hit_count=hit_count+1 WHERE cache_key=?",
            (_now(), cache_key),
        )
        self.conn.commit()
        return json.loads(row[0])

    def put_cached_result(
        self,
        cache_key: str,
        *,
        data_revision: str,
        backend: str,
        payload: dict[str, Any],
        result: dict[str, Any],
        max_result_bytes: int = DEFAULT_MAX_RESULT_BYTES,
        max_entries: int = DEFAULT_MAX_CACHE_ENTRIES,
    ) -> bool:
        result_json = canonical_json(result)
        encoded_size = len(result_json.encode("utf-8"))
        if encoded_size > max_result_bytes:
            return False
        now = _now()
        self.conn.execute(
            """
            INSERT INTO result_cache(
                cache_key,data_revision,backend,payload_json,result_json,result_bytes,
                created_at,last_accessed_at,hit_count
            ) VALUES(?,?,?,?,?,?,?,?,0)
            ON CONFLICT(cache_key) DO UPDATE SET
                result_json=excluded.result_json,
                result_bytes=excluded.result_bytes,
                last_accessed_at=excluded.last_accessed_at
            """,
            (
                cache_key,
                data_revision,
                backend,
                canonical_json(payload),
                result_json,
                encoded_size,
                now,
                now,
            ),
        )
        overflow = self.conn.execute(
            "SELECT cache_key FROM result_cache ORDER BY last_accessed_at DESC LIMIT -1 OFFSET ?",
            (max(1, int(max_entries)),),
        ).fetchall()
        if overflow:
            self.conn.executemany(
                "DELETE FROM result_cache WHERE cache_key=?",
                [(row[0],) for row in overflow],
            )
        self.conn.commit()
        return True

    def record_history(
        self,
        *,
        payload: dict[str, Any],
        data_revision: str,
        cache_key: str | None,
        backend: str | None,
        row_count: int | None,
        status: str,
        error: str | None = None,
    ) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO analysis_history(
                created_at,mode,payload_json,cache_key,data_revision,backend,row_count,status,error
            ) VALUES(?,?,?,?,?,?,?,?,?)
            """,
            (
                _now(),
                str(payload.get("mode", "basic")),
                canonical_json(payload),
                cache_key,
                data_revision,
                backend,
                row_count,
                status,
                error,
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def list_history(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT id,created_at,mode,payload_json,cache_key,data_revision,backend,row_count,status,error
            FROM analysis_history ORDER BY id DESC LIMIT ?
            """,
            (max(1, min(int(limit), 500)),),
        ).fetchall()
        return [self._history_row(row) for row in rows]

    def get_history(self, history_id: int) -> dict[str, Any] | None:
        row = self.conn.execute(
            """
            SELECT id,created_at,mode,payload_json,cache_key,data_revision,backend,row_count,status,error
            FROM analysis_history WHERE id=?
            """,
            (int(history_id),),
        ).fetchone()
        if row is None:
            return None
        item = self._history_row(row)
        item["result"] = self.get_cached_result(item["cache_key"]) if item.get("cache_key") else None
        item["result_available"] = item["result"] is not None
        return item

    @staticmethod
    def _history_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "created_at": row["created_at"],
            "mode": row["mode"],
            "payload": json.loads(row["payload_json"]),
            "cache_key": row["cache_key"],
            "data_revision": row["data_revision"],
            "backend": row["backend"],
            "row_count": row["row_count"],
            "status": row["status"],
            "error": row["error"],
        }

    def save_analysis(
        self,
        *,
        name: str,
        payload: dict[str, Any],
        notes: str = "",
        cache_key: str | None = None,
        data_revision: str | None = None,
    ) -> dict[str, Any]:
        clean_name = str(name).strip()
        if not clean_name:
            raise ValueError("Saved analysis name is required")
        now = _now()
        cur = self.conn.execute(
            """
            INSERT INTO saved_analyses(name,notes,payload_json,cache_key,data_revision,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?)
            """,
            (
                clean_name,
                str(notes or ""),
                canonical_json(payload),
                cache_key,
                data_revision,
                now,
                now,
            ),
        )
        self.conn.commit()
        return self.get_saved(int(cur.lastrowid))  # type: ignore[return-value]

    def update_saved(
        self,
        saved_id: int,
        *,
        name: str | None = None,
        notes: str | None = None,
        payload: dict[str, Any] | None = None,
        cache_key: str | None = None,
        data_revision: str | None = None,
    ) -> dict[str, Any] | None:
        current = self.get_saved(saved_id)
        if current is None:
            return None
        new_name = current["name"] if name is None else str(name).strip()
        if not new_name:
            raise ValueError("Saved analysis name is required")
        self.conn.execute(
            """
            UPDATE saved_analyses SET name=?,notes=?,payload_json=?,cache_key=?,data_revision=?,updated_at=?
            WHERE id=?
            """,
            (
                new_name,
                current["notes"] if notes is None else str(notes),
                canonical_json(current["payload"] if payload is None else payload),
                current["cache_key"] if cache_key is None else cache_key,
                current["data_revision"] if data_revision is None else data_revision,
                _now(),
                int(saved_id),
            ),
        )
        self.conn.commit()
        return self.get_saved(saved_id)

    def delete_saved(self, saved_id: int) -> bool:
        cur = self.conn.execute("DELETE FROM saved_analyses WHERE id=?", (int(saved_id),))
        self.conn.commit()
        return cur.rowcount > 0

    def list_saved(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT id,name,notes,payload_json,cache_key,data_revision,created_at,updated_at
            FROM saved_analyses ORDER BY updated_at DESC,id DESC
            """
        ).fetchall()
        return [self._saved_row(row, include_result=False) for row in rows]

    def get_saved(self, saved_id: int) -> dict[str, Any] | None:
        row = self.conn.execute(
            """
            SELECT id,name,notes,payload_json,cache_key,data_revision,created_at,updated_at
            FROM saved_analyses WHERE id=?
            """,
            (int(saved_id),),
        ).fetchone()
        if row is None:
            return None
        item = self._saved_row(row, include_result=True)
        return item

    def _saved_row(self, row: sqlite3.Row, *, include_result: bool) -> dict[str, Any]:
        item = {
            "id": row["id"],
            "name": row["name"],
            "notes": row["notes"],
            "payload": json.loads(row["payload_json"]),
            "cache_key": row["cache_key"],
            "data_revision": row["data_revision"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
        if include_result:
            item["result"] = self.get_cached_result(row["cache_key"]) if row["cache_key"] else None
            item["result_available"] = item["result"] is not None
        return item
