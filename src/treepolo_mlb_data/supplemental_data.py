from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
import re
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import requests

from .config import AppConfig

BASE = "https://baseballsavant.mlb.com"
_PROGRESS_LOCK = threading.Lock()
_RUN_LOCK = threading.Lock()
_PROGRESS: dict[str, dict[str, Any]] = {}
_SAFE_NAME = re.compile(r"[^A-Za-z0-9_]+")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _progress_key(source: str, dataset: str) -> str:
    return f"{source}:{dataset}"


def _start_progress(source: str, dataset: str, mode: str, total: int) -> None:
    with _PROGRESS_LOCK:
        _PROGRESS[_progress_key(source, dataset)] = {
            "source": source,
            "dataset": dataset,
            "mode": mode,
            "status": "running",
            "total_units": total,
            "completed_units": 0,
            "success_units": 0,
            "failed_units": 0,
            "skipped_units": 0,
            "rows_received": 0,
            "current_unit": None,
            "started_at": _now_iso(),
            "finished_at": None,
            "error": None,
            "_started_monotonic": time.monotonic(),
            "_finished_monotonic": None,
        }


def _set_current(source: str, dataset: str, unit: str | None) -> None:
    with _PROGRESS_LOCK:
        item = _PROGRESS.get(_progress_key(source, dataset))
        if item is not None:
            item["current_unit"] = unit


def _complete_unit(source: str, dataset: str, status: str, rows: int = 0) -> None:
    with _PROGRESS_LOCK:
        item = _PROGRESS.get(_progress_key(source, dataset))
        if item is None:
            return
        item["completed_units"] += 1
        item["rows_received"] += int(rows)
        item[f"{status}_units"] += 1
        item["current_unit"] = None


def _finish_progress(source: str, dataset: str, status: str, error: str | None = None) -> None:
    with _PROGRESS_LOCK:
        item = _PROGRESS.get(_progress_key(source, dataset))
        if item is None:
            return
        item["status"] = status
        item["current_unit"] = None
        item["finished_at"] = _now_iso()
        item["error"] = error
        item["_finished_monotonic"] = time.monotonic()


def supplemental_progress(source: str, dataset: str) -> dict[str, Any] | None:
    with _PROGRESS_LOCK:
        item = _PROGRESS.get(_progress_key(source, dataset))
        if item is None:
            return None
        result = {k: v for k, v in item.items() if not k.startswith("_")}
        end = item["_finished_monotonic"] or time.monotonic()
        elapsed = max(0.0, end - item["_started_monotonic"])
        total = max(0, int(item["total_units"]))
        completed = max(0, int(item["completed_units"]))
        result["elapsed_seconds"] = round(elapsed, 1)
        result["percent"] = round((completed / total * 100.0) if total else 100.0, 1)
        if item["status"] == "running" and 0 < completed < total:
            result["eta_seconds"] = round((elapsed / completed) * (total - completed), 1)
        else:
            result["eta_seconds"] = 0.0 if total and completed >= total else None
        return result


class SupplementalClient:
    def __init__(self, config: AppConfig):
        self.timeout = config.request_timeout_seconds
        self.retries = config.request_retries
        self.backoff = config.request_backoff_seconds
        self.pause = config.request_pause_seconds
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "treepolo-mlb-data-analytics/0.1 supplemental-data"})

    def get(self, url: str, *, params: dict[str, Any] | None = None) -> requests.Response:
        last: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                response = self.session.get(url, params=params, timeout=self.timeout)
                response.raise_for_status()
                if self.pause > 0:
                    time.sleep(self.pause)
                return response
            except Exception as exc:  # network boundary
                last = exc
                if attempt >= self.retries:
                    break
                time.sleep(self.backoff * (2**attempt))
        assert last is not None
        raise last

    def pitch3d(self, pitcher: int, dataset: str) -> requests.Response:
        params = {"minors": "1"} if dataset == "milb" else None
        return self.get(f"{BASE}/app/pitch-data/{pitcher}", params=params)

    def spin_aggregate(self, pitcher: int) -> requests.Response:
        # Savant's numeric-only player route can silently render the batting
        # variant even when playerType=pitcher is requested.  Resolve the
        # canonical slug from that page, then fetch the slugged pitching page,
        # which is the page that actually embeds serverVals.spinAxis.
        first = self.get(f"{BASE}/savant-player/{pitcher}", params={"playerType": "pitcher"})
        first_text = first.content.decode("utf-8", errors="replace")
        if "image_spin_x" in first_text and re.search(r"\bspinAxis\s*:\s*\[", first_text):
            return first
        match = re.search(r"\bslug\s*:\s*['\"]([^'\"]+)['\"]", first_text)
        if not match:
            raise ValueError(f"Could not resolve Savant player slug for pitcher {pitcher}")
        slug = match.group(1)
        return self.get(f"{BASE}/savant-player/{slug}", params={"playerType": "pitcher"})


class SupplementalStore:
    def __init__(self, config: AppConfig):
        config.root.mkdir(parents=True, exist_ok=True)
        self.root = config.root
        self.path = config.database_path
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self._ensure_tables()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "SupplementalStore":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def _ensure_tables(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS supplemental_sync_runs (
                run_id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                dataset TEXT NOT NULL,
                mode TEXT NOT NULL,
                status TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                error TEXT
            );
            CREATE TABLE IF NOT EXISTS supplemental_sync_units (
                unit_id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL,
                source TEXT NOT NULL,
                dataset TEXT NOT NULL,
                unit_key TEXT NOT NULL,
                status TEXT NOT NULL,
                rows_received INTEGER NOT NULL DEFAULT 0,
                snapshot_id TEXT,
                error TEXT,
                started_at TEXT NOT NULL,
                finished_at TEXT NOT NULL,
                FOREIGN KEY(run_id) REFERENCES supplemental_sync_runs(run_id)
            );
            CREATE INDEX IF NOT EXISTS idx_supp_units_lookup
                ON supplemental_sync_units(source, dataset, unit_key, unit_id);
            CREATE TABLE IF NOT EXISTS supplemental_raw_snapshots (
                snapshot_id TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                dataset TEXT NOT NULL,
                unit_key TEXT NOT NULL,
                fetched_at TEXT NOT NULL,
                relative_path TEXT NOT NULL,
                sha256 TEXT NOT NULL,
                byte_size INTEGER NOT NULL,
                last_modified TEXT,
                content_type TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_supp_snapshots_lookup
                ON supplemental_raw_snapshots(source, dataset, unit_key, fetched_at);
            CREATE TABLE IF NOT EXISTS supplemental_schema (
                source TEXT NOT NULL,
                dataset TEXT NOT NULL,
                original_name TEXT NOT NULL,
                column_name TEXT NOT NULL,
                sqlite_type TEXT NOT NULL,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                PRIMARY KEY(source, dataset, original_name),
                UNIQUE(source, dataset, column_name)
            );
            CREATE TABLE IF NOT EXISTS pitch3d_pitches (
                dataset TEXT NOT NULL,
                row_key TEXT NOT NULL,
                _fetch_pitcher INTEGER NOT NULL,
                _snapshot_id TEXT NOT NULL,
                _fetched_at TEXT NOT NULL,
                PRIMARY KEY(dataset, row_key)
            );
            CREATE INDEX IF NOT EXISTS idx_pitch3d_fetch_pitcher
                ON pitch3d_pitches(dataset, _fetch_pitcher);
            CREATE TABLE IF NOT EXISTS spin_orientation_aggregates (
                row_key TEXT PRIMARY KEY,
                _fetch_pitcher INTEGER NOT NULL,
                _snapshot_id TEXT NOT NULL,
                _fetched_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_spin_aggregate_pitcher
                ON spin_orientation_aggregates(_fetch_pitcher);
            """
        )
        self.conn.commit()

    def known_pitchers(self) -> list[int]:
        try:
            rows = self.conn.execute(
                "SELECT DISTINCT pitcher FROM pitches WHERE pitcher IS NOT NULL ORDER BY pitcher"
            ).fetchall()
        except sqlite3.OperationalError:
            return []
        out: list[int] = []
        for row in rows:
            try:
                out.append(int(row[0]))
            except (TypeError, ValueError):
                continue
        return out

    def start_run(self, source: str, dataset: str, mode: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO supplemental_sync_runs(source,dataset,mode,status,started_at) VALUES(?,?,?,?,?)",
            (source, dataset, mode, "running", _now_iso()),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def finish_run(self, run_id: int, status: str, error: str | None = None) -> None:
        self.conn.execute(
            "UPDATE supplemental_sync_runs SET status=?, finished_at=?, error=? WHERE run_id=?",
            (status, _now_iso(), error, run_id),
        )
        self.conn.commit()

    def record_unit(
        self, run_id: int, source: str, dataset: str, unit: str, status: str,
        *, rows: int = 0, snapshot_id: str | None = None, error: str | None = None,
    ) -> None:
        now = _now_iso()
        self.conn.execute(
            """INSERT INTO supplemental_sync_units
               (run_id,source,dataset,unit_key,status,rows_received,snapshot_id,error,started_at,finished_at)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (run_id, source, dataset, unit, status, int(rows), snapshot_id, error, now, now),
        )
        self.conn.commit()

    def has_success(self, source: str, dataset: str, unit: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM supplemental_sync_units WHERE source=? AND dataset=? AND unit_key=? AND status='success' LIMIT 1",
            (source, dataset, unit),
        ).fetchone()
        return row is not None

    def failed_units(self, source: str, dataset: str) -> list[int]:
        rows = self.conn.execute(
            """
            SELECT u.unit_key
            FROM supplemental_sync_units u
            WHERE u.source=? AND u.dataset=?
              AND u.unit_id=(
                SELECT MAX(v.unit_id) FROM supplemental_sync_units v
                WHERE v.source=u.source AND v.dataset=u.dataset AND v.unit_key=u.unit_key
              )
              AND u.status='failed'
            ORDER BY CAST(u.unit_key AS INTEGER)
            """,
            (source, dataset),
        ).fetchall()
        out: list[int] = []
        for row in rows:
            try:
                out.append(int(row[0]))
            except (TypeError, ValueError):
                pass
        return out

    @staticmethod
    def _infer_type(values: Iterable[Any]) -> str:
        nonempty = [value for value in values if value not in (None, "")]
        if not nonempty:
            return "TEXT"
        integer = True
        real = True
        for value in nonempty:
            text = str(value).strip()
            try:
                parsed = float(text)
            except ValueError:
                integer = real = False
                break
            if not parsed.is_integer() or any(ch in text.lower() for ch in (".", "e")):
                integer = False
        if integer:
            return "INTEGER"
        if real:
            return "REAL"
        return "TEXT"

    @staticmethod
    def _schema_dataset(source: str, dataset: str) -> str:
        return "__shared__" if source == "pitch3d" else dataset

    def _column_map(self, source: str, dataset: str) -> dict[str, str]:
        schema_dataset = self._schema_dataset(source, dataset)
        return {
            str(row[0]): str(row[1])
            for row in self.conn.execute(
                "SELECT original_name,column_name FROM supplemental_schema WHERE source=? AND dataset=?",
                (source, schema_dataset),
            )
        }

    def _ensure_dynamic_columns(
        self, table: str, source: str, dataset: str, rows: list[dict[str, Any]], reserved: set[str]
    ) -> dict[str, str]:
        schema_dataset = self._schema_dataset(source, dataset)
        mapping = self._column_map(source, dataset)
        table_columns = {str(row[1]) for row in self.conn.execute(f"PRAGMA table_info({_quote(table)})")}
        keys = list(dict.fromkeys(key for row in rows for key in row.keys()))
        now = _now_iso()
        for original in keys:
            if original in mapping:
                self.conn.execute(
                    "UPDATE supplemental_schema SET last_seen_at=? WHERE source=? AND dataset=? AND original_name=?",
                    (now, source, schema_dataset, original),
                )
                continue
            base = _SAFE_NAME.sub("_", str(original)).strip("_") or "field"
            if base[0].isdigit():
                base = f"field_{base}"
            if base in reserved or base in table_columns:
                base = f"src_{base}"
            candidate = base
            suffix = 2
            while candidate in table_columns:
                candidate = f"{base}_{suffix}"
                suffix += 1
            sql_type = self._infer_type(row.get(original) for row in rows)
            self.conn.execute(f"ALTER TABLE {_quote(table)} ADD COLUMN {_quote(candidate)} {sql_type}")
            self.conn.execute(
                """INSERT INTO supplemental_schema
                   (source,dataset,original_name,column_name,sqlite_type,first_seen_at,last_seen_at)
                   VALUES(?,?,?,?,?,?,?)""",
                (source, schema_dataset, original, candidate, sql_type, now, now),
            )
            mapping[original] = candidate
            table_columns.add(candidate)
        self.conn.commit()
        return mapping

    def save_snapshot(
        self, source: str, dataset: str, unit: str, payload: bytes, response: requests.Response
    ) -> str:
        digest = hashlib.sha256(payload).hexdigest()
        snapshot_id = f"{source}:{dataset}:{unit}:{digest[:20]}:{_stamp()}"
        extension = "csv" if source == "pitch3d" else "html"
        rel = Path("supplemental_raw") / source / dataset / unit / f"{_stamp()}-{digest[:12]}.{extension}.gz"
        target = self.root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(target, "wb") as handle:
            handle.write(payload)
        self.conn.execute(
            """INSERT INTO supplemental_raw_snapshots
               (snapshot_id,source,dataset,unit_key,fetched_at,relative_path,sha256,byte_size,last_modified,content_type)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (
                snapshot_id, source, dataset, unit, _now_iso(), str(rel), digest, len(payload),
                response.headers.get("Last-Modified"), response.headers.get("Content-Type"),
            ),
        )
        self.conn.commit()
        return snapshot_id

    def latest_snapshots(self, source: str, dataset: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT s.* FROM supplemental_raw_snapshots s
            JOIN (
                SELECT unit_key, MAX(fetched_at) AS fetched_at
                FROM supplemental_raw_snapshots WHERE source=? AND dataset=? GROUP BY unit_key
            ) latest ON latest.unit_key=s.unit_key AND latest.fetched_at=s.fetched_at
            WHERE s.source=? AND s.dataset=? ORDER BY CAST(s.unit_key AS INTEGER)
            """,
            (source, dataset, source, dataset),
        ).fetchall()

    def read_snapshot(self, row: sqlite3.Row) -> bytes:
        path = self.root / str(row["relative_path"])
        with gzip.open(path, "rb") as handle:
            payload = handle.read()
        digest = hashlib.sha256(payload).hexdigest()
        if digest != row["sha256"]:
            raise ValueError(f"supplemental raw snapshot hash mismatch: {path}")
        return payload

    def replace_pitch3d(self, pitcher: int, dataset: str, payload: bytes, snapshot_id: str) -> int:
        text = payload.decode("utf-8-sig")
        rows = [dict(row) for row in csv.DictReader(io.StringIO(text))]
        if not rows and not text.strip():
            raise ValueError("Pitch3D returned an empty response")
        headers = list(rows[0].keys()) if rows else list(csv.DictReader(io.StringIO(text)).fieldnames or [])
        if "game_pk" not in headers or "play_id" not in headers:
            raise ValueError("Pitch3D response is missing game_pk/play_id")
        mapping = self._ensure_dynamic_columns(
            "pitch3d_pitches", "pitch3d", dataset, rows,
            {"dataset", "row_key", "_fetch_pitcher", "_snapshot_id", "_fetched_at"},
        )
        fetched = _now_iso()
        self.conn.execute("DELETE FROM pitch3d_pitches WHERE dataset=? AND _fetch_pitcher=?", (dataset, pitcher))
        for index, row in enumerate(rows):
            game_pk = str(row.get("game_pk") or "")
            play_id = str(row.get("play_id") or "")
            row_key = f"{game_pk}:{play_id}" if game_pk and play_id else hashlib.sha1(
                json.dumps(row, sort_keys=True, ensure_ascii=False).encode("utf-8")
            ).hexdigest()
            base = {
                "dataset": dataset, "row_key": row_key, "_fetch_pitcher": pitcher,
                "_snapshot_id": snapshot_id, "_fetched_at": fetched,
            }
            for original, column in mapping.items():
                base[column] = row.get(original)
            columns = list(base)
            self.conn.execute(
                f"INSERT OR REPLACE INTO pitch3d_pitches ({','.join(_quote(c) for c in columns)}) VALUES ({','.join('?' for _ in columns)})",
                [base[c] for c in columns],
            )
        self.conn.commit()
        return len(rows)

    @staticmethod
    def parse_spin_rows(payload: bytes) -> list[dict[str, Any]]:
        text = payload.decode("utf-8", errors="replace")
        patterns = (r'"spinAxis"\s*:\s*\[', r'\bspinAxis\s*:\s*\[', r'\bspinAxis\s*=\s*\[')
        for pattern in patterns:
            match = re.search(pattern, text)
            if not match:
                continue
            start = text.find("[", match.start())
            depth = 0
            in_string = False
            escaped = False
            for index in range(start, len(text)):
                char = text[index]
                if in_string:
                    if escaped:
                        escaped = False
                    elif char == "\\":
                        escaped = True
                    elif char == '"':
                        in_string = False
                    continue
                if char == '"':
                    in_string = True
                elif char == "[":
                    depth += 1
                elif char == "]":
                    depth -= 1
                    if depth == 0:
                        try:
                            value = json.loads(text[start:index + 1])
                        except json.JSONDecodeError:
                            break
                        if isinstance(value, list):
                            return [dict(row) for row in value if isinstance(row, dict)]
                        break
        raise ValueError("Could not parse serverVals.spinAxis from Savant player page")

    def replace_spin(self, pitcher: int, payload: bytes, snapshot_id: str) -> int:
        rows = self.parse_spin_rows(payload)
        mapping = self._ensure_dynamic_columns(
            "spin_orientation_aggregates", "spin_aggregate", "mlb", rows,
            {"row_key", "_fetch_pitcher", "_snapshot_id", "_fetched_at"},
        )
        fetched = _now_iso()
        self.conn.execute("DELETE FROM spin_orientation_aggregates WHERE _fetch_pitcher=?", (pitcher,))
        seen: dict[str, int] = {}
        for row in rows:
            base_key = f"{pitcher}:{row.get('season','')}:{row.get('api_pitch_type','')}"
            seen[base_key] = seen.get(base_key, 0) + 1
            row_key = f"{base_key}:{seen[base_key]}"
            base: dict[str, Any] = {
                "row_key": row_key, "_fetch_pitcher": pitcher,
                "_snapshot_id": snapshot_id, "_fetched_at": fetched,
            }
            for original, column in mapping.items():
                base[column] = row.get(original)
            columns = list(base)
            self.conn.execute(
                f"INSERT OR REPLACE INTO spin_orientation_aggregates ({','.join(_quote(c) for c in columns)}) VALUES ({','.join('?' for _ in columns)})",
                [base[c] for c in columns],
            )
        self.conn.commit()
        return len(rows)

    def status(self) -> dict[str, Any]:
        def count(table: str, where: str = "", args: tuple[Any, ...] = ()) -> int:
            return int(self.conn.execute(f"SELECT COUNT(*) FROM {table} {where}", args).fetchone()[0])

        return {
            "pitch3d_mlb_rows": count("pitch3d_pitches", "WHERE dataset='mlb'"),
            "pitch3d_milb_rows": count("pitch3d_pitches", "WHERE dataset='milb'"),
            "pitch3d_mlb_pitchers": count("(SELECT DISTINCT _fetch_pitcher FROM pitch3d_pitches WHERE dataset='mlb')"),
            "pitch3d_milb_pitchers": count("(SELECT DISTINCT _fetch_pitcher FROM pitch3d_pitches WHERE dataset='milb')"),
            "spin_aggregate_rows": count("spin_orientation_aggregates"),
            "spin_aggregate_pitchers": count("(SELECT DISTINCT _fetch_pitcher FROM spin_orientation_aggregates)"),
            "raw_snapshots": count("supplemental_raw_snapshots"),
            "known_statcast_pitchers": len(self.known_pitchers()),
        }

    def verify(self, source: str, dataset: str) -> dict[str, Any]:
        if source == "pitch3d":
            table = "pitch3d_pitches"
            where = "WHERE dataset=?"
            args: tuple[Any, ...] = (dataset,)
            duplicate = self.conn.execute(
                "SELECT COUNT(*) FROM (SELECT row_key,COUNT(*) c FROM pitch3d_pitches WHERE dataset=? GROUP BY row_key HAVING c>1)",
                args,
            ).fetchone()[0]
        else:
            table = "spin_orientation_aggregates"
            where = ""
            args = ()
            duplicate = self.conn.execute(
                "SELECT COUNT(*) FROM (SELECT row_key,COUNT(*) c FROM spin_orientation_aggregates GROUP BY row_key HAVING c>1)"
            ).fetchone()[0]
        rows = int(self.conn.execute(f"SELECT COUNT(*) FROM {table} {where}", args).fetchone()[0])
        snapshots = self.conn.execute(
            "SELECT relative_path,sha256 FROM supplemental_raw_snapshots WHERE source=? AND dataset=?",
            (source, dataset),
        ).fetchall()
        missing = 0
        mismatched = 0
        for snapshot in snapshots:
            path = self.root / str(snapshot[0])
            if not path.exists():
                missing += 1
                continue
            with gzip.open(path, "rb") as handle:
                digest = hashlib.sha256(handle.read()).hexdigest()
            mismatched += int(digest != snapshot[1])
        return {
            "source": source, "dataset": dataset, "rows": rows,
            "duplicate_row_keys": int(duplicate), "raw_snapshots": len(snapshots),
            "missing_snapshot_files": missing, "hash_mismatches": mismatched,
            "ok": duplicate == 0 and missing == 0 and mismatched == 0,
        }


def _normalize_source(payload: dict[str, Any]) -> tuple[str, str]:
    source = str(payload.get("source") or "").strip()
    dataset = str(payload.get("dataset") or "mlb").strip().lower()
    if source not in {"pitch3d", "spin_aggregate"}:
        raise ValueError("supplemental source must be pitch3d or spin_aggregate")
    if source == "spin_aggregate":
        dataset = "mlb"
    if dataset not in {"mlb", "milb"}:
        raise ValueError("supplemental dataset must be mlb or milb")
    return source, dataset


def _pitcher_ids(store: SupplementalStore, payload: dict[str, Any]) -> list[int]:
    raw = payload.get("pitcher_ids")
    if raw in (None, [], ""):
        return store.known_pitchers()
    if isinstance(raw, str):
        raw = [item.strip() for item in raw.split(",") if item.strip()]
    if not isinstance(raw, list):
        raise ValueError("pitcher_ids must be a list or comma-separated IDs")
    return sorted({int(value) for value in raw})


def _run_sync(config: AppConfig, source: str, dataset: str, mode: str, payload: dict[str, Any]) -> dict[str, Any]:
    if not _RUN_LOCK.acquire(blocking=False):
        raise RuntimeError("另一個補充資料同步正在執行 Another supplemental data sync is already running")
    try:
        with SupplementalStore(config) as store:
            pitchers = _pitcher_ids(store, payload)
            if mode == "retry_failed":
                pitchers = store.failed_units(source, dataset)
            if not pitchers:
                raise ValueError("No pitcher IDs are available for supplemental data sync")
            resume = bool(payload.get("resume", True)) and mode == "backfill"
            run_id = store.start_run(source, dataset, mode)
            _start_progress(source, dataset, mode, len(pitchers))
            client = SupplementalClient(config)
            overall = "success"
            total_rows = 0
            try:
                for pitcher in pitchers:
                    unit = str(pitcher)
                    _set_current(source, dataset, unit)
                    if resume and store.has_success(source, dataset, unit):
                        store.record_unit(run_id, source, dataset, unit, "skipped")
                        _complete_unit(source, dataset, "skipped")
                        continue
                    try:
                        if source == "pitch3d":
                            response = client.pitch3d(pitcher, dataset)
                        else:
                            response = client.spin_aggregate(pitcher)
                        snapshot_id = store.save_snapshot(source, dataset, unit, response.content, response)
                        if source == "pitch3d":
                            rows = store.replace_pitch3d(pitcher, dataset, response.content, snapshot_id)
                        else:
                            rows = store.replace_spin(pitcher, response.content, snapshot_id)
                        total_rows += rows
                        store.record_unit(
                            run_id, source, dataset, unit, "success", rows=rows, snapshot_id=snapshot_id
                        )
                        _complete_unit(source, dataset, "success", rows)
                    except Exception as exc:
                        overall = "partial"
                        store.record_unit(run_id, source, dataset, unit, "failed", error=str(exc))
                        _complete_unit(source, dataset, "failed")
                        if bool(payload.get("fail_fast", False)):
                            raise
                store.finish_run(run_id, overall)
                _finish_progress(source, dataset, overall)
                return {
                    "run_id": run_id, "source": source, "dataset": dataset, "mode": mode,
                    "status": overall, "units": len(pitchers), "rows_received": total_rows,
                }
            except Exception as exc:
                store.finish_run(run_id, "failed", str(exc))
                _finish_progress(source, dataset, "failed", str(exc))
                raise
    finally:
        _RUN_LOCK.release()


def _rebuild(config: AppConfig, source: str, dataset: str, payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("confirmation") != "REBUILD":
        raise ValueError("Supplemental rebuild requires confirmation=REBUILD")
    rebuilt = rows = 0
    with SupplementalStore(config) as store:
        for snapshot in store.latest_snapshots(source, dataset):
            raw = store.read_snapshot(snapshot)
            pitcher = int(snapshot["unit_key"])
            if source == "pitch3d":
                rows += store.replace_pitch3d(pitcher, dataset, raw, str(snapshot["snapshot_id"]))
            else:
                rows += store.replace_spin(pitcher, raw, str(snapshot["snapshot_id"]))
            rebuilt += 1
    return {"source": source, "dataset": dataset, "snapshots_rebuilt": rebuilt, "rows": rows}


def handle_supplemental_action(config: AppConfig, action: str, payload: dict[str, Any]) -> Any:
    source, dataset = _normalize_source(payload)
    if action == "supplemental-progress":
        return {"progress": supplemental_progress(source, dataset)}
    if action == "supplemental-status":
        with SupplementalStore(config) as store:
            return store.status()
    if action == "supplemental-verify":
        with SupplementalStore(config) as store:
            return store.verify(source, dataset)
    if action == "supplemental-rebuild":
        return _rebuild(config, source, dataset, payload)
    if action == "supplemental-run":
        mode = str(payload.get("mode") or "backfill")
        if mode not in {"backfill", "update", "retry_failed"}:
            raise ValueError("supplemental sync mode must be backfill, update, or retry_failed")
        return _run_sync(config, source, dataset, mode, payload)
    raise ValueError(f"Unknown supplemental action: {action}")
