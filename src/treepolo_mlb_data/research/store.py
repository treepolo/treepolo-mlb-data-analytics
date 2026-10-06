from __future__ import annotations

import gzip
import hashlib
import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from ..analysis_state import canonical_json
from .config_schema import ConfigError

FORMAT_VERSION = "research-v1"
MAX_LIST_LIMIT = 1000

_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_info (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS studies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    study_uid TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    purpose TEXT NOT NULL DEFAULT '',
    insight_markdown TEXT NOT NULL DEFAULT '',
    origin TEXT NOT NULL DEFAULT 'local',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_studies_origin_name ON studies(origin, name);

CREATE TABLE IF NOT EXISTS research_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_key TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL,
    method_version INTEGER NOT NULL,
    config_json TEXT NOT NULL,
    scope_json TEXT NOT NULL,
    data_fingerprint_json TEXT NOT NULL,
    status TEXT NOT NULL,
    error TEXT,
    summary_json TEXT NOT NULL DEFAULT '{}',
    result_hash TEXT,
    result_bytes INTEGER,
    duration_seconds REAL,
    code_version TEXT NOT NULL,
    origin TEXT NOT NULL DEFAULT 'local',
    imported_bundle_uid TEXT,
    parent_run_id INTEGER REFERENCES research_runs(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_kind_created ON research_runs(kind, created_at DESC);

CREATE TABLE IF NOT EXISTS study_runs (
    study_id INTEGER NOT NULL REFERENCES studies(id) ON DELETE CASCADE,
    run_id INTEGER NOT NULL REFERENCES research_runs(id) ON DELETE CASCADE,
    added_at TEXT NOT NULL,
    PRIMARY KEY (study_id, run_id)
);

CREATE TABLE IF NOT EXISTS run_artifacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES research_runs(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    columns_json TEXT NOT NULL,
    row_count INTEGER NOT NULL,
    blob_hash TEXT NOT NULL,
    blob_bytes INTEGER NOT NULL,
    UNIQUE (run_id, name)
);

CREATE TABLE IF NOT EXISTS blobs (
    blob_hash TEXT PRIMARY KEY,
    relpath TEXT NOT NULL,
    bytes INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ResearchStore:
    """Persistent research studies, runs, and content-addressed result blobs."""

    def __init__(self, path: Path, blob_dir: Path):
        self.path = Path(path)
        self.blob_dir = Path(blob_dir)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        (self.blob_dir / "blobs").mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(self.path, timeout=30.0, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._init_schema()
        self.recover_interrupted()

    # ------------------------------------------------------------------ setup
    def _init_schema(self) -> None:
        with self._lock:
            self.conn.executescript(_SCHEMA)
            row = self.conn.execute("SELECT value FROM schema_info WHERE key='format_version'").fetchone()
            if row is None:
                self.conn.execute("INSERT INTO schema_info(key,value) VALUES('format_version',?)", (FORMAT_VERSION,))
            elif row[0] != FORMAT_VERSION:
                raise RuntimeError(f"Unsupported research store format {row[0]!r}; expected {FORMAT_VERSION!r}")
            self.conn.commit()

    def close(self) -> None:
        with self._lock:
            self.conn.close()

    def __enter__(self) -> "ResearchStore":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def recover_interrupted(self) -> int:
        with self._lock:
            cur = self.conn.execute(
                "UPDATE research_runs SET status='failed', error=?, finished_at=? WHERE status='running'",
                ("interrupted (process ended before completion)", _now()),
            )
            self.conn.commit()
            return cur.rowcount

    # ------------------------------------------------------------------ blobs
    def _blob_path(self, blob_hash: str) -> Path:
        return self.blob_dir / "blobs" / blob_hash[:2] / f"{blob_hash}.json.gz"

    def _register_blob(self, blob_hash: str, size: int) -> None:
        relpath = f"blobs/{blob_hash[:2]}/{blob_hash}.json.gz"
        self.conn.execute(
            "INSERT OR IGNORE INTO blobs(blob_hash,relpath,bytes,created_at) VALUES(?,?,?,?)",
            (blob_hash, relpath, int(size), _now()),
        )

    def _write_gzip_file(self, path: Path, raw: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        with gzip.open(temp, "wb") as handle:
            handle.write(raw)
        temp.replace(path)

    def write_json_blob(self, value: Any) -> tuple[str, int]:
        raw = canonical_json(value).encode("utf-8")
        digest = hashlib.sha256(raw).hexdigest()
        path = self._blob_path(digest)
        with self._lock:
            if not path.is_file():
                self._write_gzip_file(path, raw)
            self._register_blob(digest, len(raw))
            self.conn.commit()
        return digest, len(raw)

    def read_json_blob(self, blob_hash: str) -> Any:
        path = self._blob_path(blob_hash)
        if not path.is_file():
            raise FileNotFoundError(f"Research blob is missing: {blob_hash}")
        with gzip.open(path, "rb") as handle:
            return json.loads(handle.read().decode("utf-8"))

    def read_blob_gzip_bytes(self, blob_hash: str) -> bytes:
        path = self._blob_path(blob_hash)
        if not path.is_file():
            raise FileNotFoundError(f"Research blob is missing: {blob_hash}")
        return path.read_bytes()

    def write_blob_from_gzip(self, blob_hash: str, gzip_bytes: bytes) -> int:
        """Store an already-gzipped blob after verifying its content hash."""

        try:
            raw = gzip.decompress(gzip_bytes)
        except (OSError, EOFError) as exc:
            raise ConfigError(f"Blob corrupted: {blob_hash} / 研究檔內容損毀") from exc
        if hashlib.sha256(raw).hexdigest() != blob_hash:
            raise ConfigError(f"Blob corrupted: {blob_hash} / 研究檔內容損毀")
        path = self._blob_path(blob_hash)
        with self._lock:
            if not path.is_file():
                self._write_gzip_file(path, raw)
            self._register_blob(blob_hash, len(raw))
            self.conn.commit()
        return len(raw)

    def _collect_garbage_locked(self) -> int:
        rows = self.conn.execute(
            """
            SELECT blob_hash, relpath FROM blobs
            WHERE blob_hash NOT IN (SELECT result_hash FROM research_runs WHERE result_hash IS NOT NULL)
              AND blob_hash NOT IN (SELECT blob_hash FROM run_artifacts)
            """
        ).fetchall()
        for row in rows:
            try:
                (self.blob_dir / row["relpath"]).unlink(missing_ok=True)
            except OSError:
                pass
            self.conn.execute("DELETE FROM blobs WHERE blob_hash=?", (row["blob_hash"],))
        return len(rows)

    def collect_garbage(self) -> int:
        with self._lock:
            removed = self._collect_garbage_locked()
            self.conn.commit()
            return removed

    # ---------------------------------------------------------------- studies
    @staticmethod
    def _study_row(row: sqlite3.Row, run_count: int | None = None) -> dict[str, Any]:
        item = {
            "id": row["id"], "study_uid": row["study_uid"], "name": row["name"], "purpose": row["purpose"],
            "insight_markdown": row["insight_markdown"], "origin": row["origin"],
            "created_at": row["created_at"], "updated_at": row["updated_at"],
        }
        if run_count is not None:
            item["run_count"] = run_count
        return item

    def _get_study_locked(self, study_id: int) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM studies WHERE id=?", (int(study_id),)).fetchone()
        if row is None:
            return None
        count = self.conn.execute("SELECT COUNT(*) FROM study_runs WHERE study_id=?", (row["id"],)).fetchone()[0]
        return self._study_row(row, count)

    def get_study(self, study_id: int) -> dict[str, Any] | None:
        with self._lock:
            return self._get_study_locked(study_id)

    def get_study_by_uid(self, study_uid: str) -> dict[str, Any] | None:
        with self._lock:
            row = self.conn.execute("SELECT id FROM studies WHERE study_uid=?", (study_uid,)).fetchone()
            return self._get_study_locked(row["id"]) if row else None

    def create_study(
        self, name: str, purpose: str = "", insight_markdown: str = "", origin: str = "local",
        study_uid: str | None = None, created_at: str | None = None, updated_at: str | None = None,
    ) -> dict[str, Any]:
        clean = str(name or "").strip()
        if not clean:
            raise ConfigError("Study name is required / 研究專案名稱不得為空")
        now = _now()
        with self._lock:
            try:
                cur = self.conn.execute(
                    "INSERT INTO studies(study_uid,name,purpose,insight_markdown,origin,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                    (study_uid or uuid.uuid4().hex, clean, str(purpose or ""), str(insight_markdown or ""), origin,
                     created_at or now, updated_at or now),
                )
            except sqlite3.IntegrityError as exc:
                self.conn.rollback()
                raise ConfigError(f"Study already exists: {clean} / 研究專案已存在: {clean}") from exc
            self.conn.commit()
            return self._get_study_locked(int(cur.lastrowid))  # type: ignore[return-value]

    def get_or_create_study(self, name: str) -> dict[str, Any]:
        clean = str(name or "").strip()
        with self._lock:
            row = self.conn.execute("SELECT id FROM studies WHERE origin='local' AND name=?", (clean,)).fetchone()
            if row is not None:
                return self._get_study_locked(row["id"])  # type: ignore[return-value]
            return self.create_study(clean)

    def update_study(
        self, study_id: int, *, name: str | None = None, purpose: str | None = None, insight_markdown: str | None = None
    ) -> dict[str, Any] | None:
        with self._lock:
            current = self._get_study_locked(study_id)
            if current is None:
                return None
            new_name = current["name"] if name is None else str(name).strip()
            if not new_name:
                raise ConfigError("Study name is required / 研究專案名稱不得為空")
            try:
                self.conn.execute(
                    "UPDATE studies SET name=?,purpose=?,insight_markdown=?,updated_at=? WHERE id=?",
                    (new_name, current["purpose"] if purpose is None else str(purpose),
                     current["insight_markdown"] if insight_markdown is None else str(insight_markdown),
                     _now(), int(study_id)),
                )
            except sqlite3.IntegrityError as exc:
                self.conn.rollback()
                raise ConfigError(f"Study already exists: {new_name} / 研究專案已存在: {new_name}") from exc
            self.conn.commit()
            return self._get_study_locked(study_id)

    def list_studies(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self.conn.execute(
                """
                SELECT s.*, (SELECT COUNT(*) FROM study_runs sr WHERE sr.study_id=s.id) AS run_count
                FROM studies s ORDER BY s.updated_at DESC, s.id DESC
                """
            ).fetchall()
            return [self._study_row(row, row["run_count"]) for row in rows]

    def delete_study(self, study_id: int, delete_runs: bool = False) -> bool:
        with self._lock:
            if self.conn.execute("SELECT 1 FROM studies WHERE id=?", (int(study_id),)).fetchone() is None:
                return False
            if delete_runs:
                exclusive = self.conn.execute(
                    """
                    SELECT run_id FROM study_runs WHERE study_id=?
                      AND run_id NOT IN (SELECT run_id FROM study_runs WHERE study_id<>?)
                    """,
                    (int(study_id), int(study_id)),
                ).fetchall()
                for row in exclusive:
                    self.conn.execute("DELETE FROM research_runs WHERE id=?", (row["run_id"],))
            self.conn.execute("DELETE FROM studies WHERE id=?", (int(study_id),))
            self._collect_garbage_locked()
            self.conn.commit()
            return True

    # ------------------------------------------------------------------- runs
    def _run_row(self, row: sqlite3.Row, *, detail: bool = True) -> dict[str, Any]:
        item = {
            "id": row["id"], "run_key": row["run_key"], "kind": row["kind"], "method_version": row["method_version"],
            "config": json.loads(row["config_json"]), "scope": json.loads(row["scope_json"]),
            "data_fingerprint": json.loads(row["data_fingerprint_json"]), "status": row["status"],
            "error": row["error"], "summary": json.loads(row["summary_json"] or "{}"),
            "result_hash": row["result_hash"], "result_bytes": row["result_bytes"],
            "duration_seconds": row["duration_seconds"], "code_version": row["code_version"],
            "origin": row["origin"], "imported_bundle_uid": row["imported_bundle_uid"],
            "parent_run_id": row["parent_run_id"], "created_at": row["created_at"], "finished_at": row["finished_at"],
        }
        if detail:
            item["studies"] = [
                {"id": s["id"], "name": s["name"]}
                for s in self.conn.execute(
                    "SELECT s.id,s.name FROM studies s JOIN study_runs sr ON sr.study_id=s.id WHERE sr.run_id=? ORDER BY s.id",
                    (row["id"],),
                )
            ]
            item["artifacts"] = [
                {"name": a["name"], "description": a["description"], "columns": json.loads(a["columns_json"]),
                 "row_count": a["row_count"], "blob_hash": a["blob_hash"], "blob_bytes": a["blob_bytes"]}
                for a in self.conn.execute("SELECT * FROM run_artifacts WHERE run_id=? ORDER BY id", (row["id"],))
            ]
        return item

    def find_run_by_key(self, run_key: str) -> dict[str, Any] | None:
        with self._lock:
            row = self.conn.execute("SELECT * FROM research_runs WHERE run_key=?", (run_key,)).fetchone()
            return self._run_row(row) if row else None

    def get_run(self, run_id: int) -> dict[str, Any] | None:
        with self._lock:
            row = self.conn.execute("SELECT * FROM research_runs WHERE id=?", (int(run_id),)).fetchone()
            return self._run_row(row) if row else None

    def insert_run(
        self, *, run_key: str, kind: str, method_version: int, config: Mapping[str, Any], scope: Mapping[str, Any],
        data_fingerprint: Mapping[str, Any], code_version: str, status: str = "running", origin: str = "local",
        imported_bundle_uid: str | None = None, parent_run_id: int | None = None, summary: Mapping[str, Any] | None = None,
        result_hash: str | None = None, result_bytes: int | None = None, duration_seconds: float | None = None,
        error: str | None = None, created_at: str | None = None, finished_at: str | None = None,
    ) -> int:
        with self._lock:
            cur = self.conn.execute(
                """
                INSERT INTO research_runs(run_key,kind,method_version,config_json,scope_json,data_fingerprint_json,status,error,
                    summary_json,result_hash,result_bytes,duration_seconds,code_version,origin,imported_bundle_uid,parent_run_id,
                    created_at,finished_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (run_key, kind, int(method_version), canonical_json(dict(config)), canonical_json(dict(scope)),
                 canonical_json(dict(data_fingerprint)), status, error, canonical_json(dict(summary or {})), result_hash,
                 result_bytes, duration_seconds, code_version, origin, imported_bundle_uid, parent_run_id,
                 created_at or _now(), finished_at),
            )
            self.conn.commit()
            return int(cur.lastrowid)

    def restart_run(self, run_id: int, *, code_version: str, data_fingerprint: Mapping[str, Any], parent_run_id: int | None = None) -> None:
        with self._lock:
            self.conn.execute(
                """
                UPDATE research_runs SET status='running', error=NULL, finished_at=NULL, code_version=?,
                    data_fingerprint_json=?, parent_run_id=COALESCE(?, parent_run_id)
                WHERE id=?
                """,
                (code_version, canonical_json(dict(data_fingerprint)), parent_run_id, int(run_id)),
            )
            self.conn.commit()

    def finish_run(
        self, run_id: int, *, result: Mapping[str, Any], artifacts: Sequence[Mapping[str, Any]] = (),
        summary: Mapping[str, Any] | None = None, duration_seconds: float | None = None,
    ) -> dict[str, Any]:
        result_hash, result_bytes = self.write_json_blob(dict(result))
        stored: list[tuple[str, str, list[str], int, str, int]] = []
        for artifact in artifacts:
            rows = list(artifact["rows"])
            columns = [str(c) for c in artifact["columns"]]
            blob_hash, blob_bytes = self.write_json_blob({"columns": columns, "rows": rows})
            stored.append((str(artifact["name"]), str(artifact.get("description", "")), columns, len(rows), blob_hash, blob_bytes))
        with self._lock:
            self.conn.execute("DELETE FROM run_artifacts WHERE run_id=?", (int(run_id),))
            for name, description, columns, count, blob_hash, blob_bytes in stored:
                self.conn.execute(
                    "INSERT INTO run_artifacts(run_id,name,description,columns_json,row_count,blob_hash,blob_bytes) VALUES(?,?,?,?,?,?,?)",
                    (int(run_id), name, description, canonical_json(columns), count, blob_hash, blob_bytes),
                )
            self.conn.execute(
                """
                UPDATE research_runs SET status='success', error=NULL, summary_json=?, result_hash=?, result_bytes=?,
                    duration_seconds=?, finished_at=? WHERE id=?
                """,
                (canonical_json(dict(summary or {})), result_hash, result_bytes, duration_seconds, _now(), int(run_id)),
            )
            self._collect_garbage_locked()
            self.conn.commit()
            return self.get_run(run_id)  # type: ignore[return-value]

    def add_artifact_record(
        self, run_id: int, *, name: str, description: str, columns: Sequence[str], row_count: int, blob_hash: str, blob_bytes: int
    ) -> None:
        with self._lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO run_artifacts(run_id,name,description,columns_json,row_count,blob_hash,blob_bytes) VALUES(?,?,?,?,?,?,?)",
                (int(run_id), name, description, canonical_json(list(columns)), int(row_count), blob_hash, int(blob_bytes)),
            )
            self.conn.commit()

    def fail_run(self, run_id: int, error: str, duration_seconds: float | None = None) -> None:
        with self._lock:
            self.conn.execute(
                "UPDATE research_runs SET status='failed', error=?, duration_seconds=?, finished_at=? WHERE id=?",
                (str(error), duration_seconds, _now(), int(run_id)),
            )
            self.conn.commit()

    def link_run(self, study_id: int, run_id: int) -> None:
        with self._lock:
            self.conn.execute(
                "INSERT OR IGNORE INTO study_runs(study_id,run_id,added_at) VALUES(?,?,?)", (int(study_id), int(run_id), _now())
            )
            self.conn.commit()

    def unlink_run(self, study_id: int, run_id: int) -> bool:
        with self._lock:
            cur = self.conn.execute("DELETE FROM study_runs WHERE study_id=? AND run_id=?", (int(study_id), int(run_id)))
            self.conn.commit()
            return cur.rowcount > 0

    def list_runs(
        self, *, study_id: int | None = None, kind: str | None = None, status: str | None = None,
        limit: int = 200, offset: int = 0,
    ) -> list[dict[str, Any]]:
        if int(limit) < 1 or int(limit) > MAX_LIST_LIMIT:
            raise ConfigError(f"limit must be between 1 and {MAX_LIST_LIMIT} / limit 必須介於 1 與 {MAX_LIST_LIMIT}")
        if int(offset) < 0:
            raise ConfigError("offset must be >= 0 / offset 不得為負")
        clauses: list[str] = []
        params: list[Any] = []
        sql = "SELECT r.* FROM research_runs r"
        if study_id is not None:
            sql += " JOIN study_runs sr ON sr.run_id=r.id"
            clauses.append("sr.study_id=?"); params.append(int(study_id))
        if kind:
            clauses.append("r.kind=?"); params.append(kind)
        if status:
            clauses.append("r.status=?"); params.append(status)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY r.id DESC LIMIT ? OFFSET ?"
        params.extend([int(limit), int(offset)])
        with self._lock:
            return [self._run_row(row) for row in self.conn.execute(sql, params).fetchall()]

    def load_result(self, run_id: int) -> dict[str, Any] | None:
        run = self.get_run(run_id)
        if run is None or not run["result_hash"]:
            return None
        return self.read_json_blob(run["result_hash"])

    def load_artifact(self, run_id: int, name: str) -> dict[str, Any] | None:
        with self._lock:
            row = self.conn.execute("SELECT blob_hash FROM run_artifacts WHERE run_id=? AND name=?", (int(run_id), name)).fetchone()
        return self.read_json_blob(row["blob_hash"]) if row else None

    def delete_run(self, run_id: int) -> bool:
        with self._lock:
            cur = self.conn.execute("DELETE FROM research_runs WHERE id=?", (int(run_id),))
            self._collect_garbage_locked()
            self.conn.commit()
            return cur.rowcount > 0

    # ----------------------------------------------------------------- import
    def import_records(self, *, bundle_uid: str, studies: Sequence[Mapping[str, Any]], runs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        """Insert imported studies and runs in ONE transaction (all or nothing).

        ``studies``: dicts with study_uid, name, purpose, insight_markdown, created_at, updated_at, run_keys.
        ``runs``: run dicts as written by the bundle exporter (with ``artifacts``).
        Blobs must already be stored.
        """

        report = {"bundle_uid": bundle_uid, "studies_added": 0, "studies_merged": 0, "runs_added": 0,
                  "runs_skipped_duplicate": 0, "warnings": []}
        with self._lock:
            try:
                run_ids: dict[str, int] = {}
                for run in runs:
                    existing = self.conn.execute(
                        "SELECT id, result_hash FROM research_runs WHERE run_key=?", (run["run_key"],)
                    ).fetchone()
                    if existing is not None:
                        run_ids[run["run_key"]] = existing["id"]
                        report["runs_skipped_duplicate"] += 1
                        if existing["result_hash"] != run["result_hash"]:
                            report["warnings"].append(
                                f"Run {run['run_key'][:12]} already exists locally with a different result; kept the local one"
                            )
                        continue
                    cur = self.conn.execute(
                        """
                        INSERT INTO research_runs(run_key,kind,method_version,config_json,scope_json,data_fingerprint_json,status,error,
                            summary_json,result_hash,result_bytes,duration_seconds,code_version,origin,imported_bundle_uid,parent_run_id,
                            created_at,finished_at)
                        VALUES(?,?,?,?,?,?,'success',NULL,?,?,?,?,?,'imported',?,NULL,?,?)
                        """,
                        (run["run_key"], run["kind"], int(run["method_version"]), canonical_json(run["config"]),
                         canonical_json(run["scope"]), canonical_json(run["data_fingerprint"]), canonical_json(run.get("summary", {})),
                         run["result_hash"], run.get("result_bytes"), run.get("duration_seconds"), run["code_version"],
                         bundle_uid, run["created_at"], run.get("finished_at")),
                    )
                    run_id = int(cur.lastrowid)
                    run_ids[run["run_key"]] = run_id
                    for artifact in run.get("artifacts", []):
                        self.conn.execute(
                            "INSERT INTO run_artifacts(run_id,name,description,columns_json,row_count,blob_hash,blob_bytes) VALUES(?,?,?,?,?,?,?)",
                            (run_id, artifact["name"], artifact.get("description", ""), canonical_json(artifact["columns"]),
                             int(artifact["row_count"]), artifact["blob_hash"], int(artifact["blob_bytes"])),
                        )
                    report["runs_added"] += 1
                for study in studies:
                    row = self.conn.execute("SELECT id FROM studies WHERE study_uid=?", (study["study_uid"],)).fetchone()
                    if row is not None:
                        study_id = row["id"]
                        report["studies_merged"] += 1
                    else:
                        name = str(study["name"]).strip() or "imported"
                        candidate, number = name, 1
                        while self.conn.execute("SELECT 1 FROM studies WHERE origin='imported' AND name=?", (candidate,)).fetchone():
                            number += 1
                            candidate = f"{name} (imported)" if number == 2 else f"{name} (imported {number - 1})"
                        cur = self.conn.execute(
                            "INSERT INTO studies(study_uid,name,purpose,insight_markdown,origin,created_at,updated_at) VALUES(?,?,?,?,'imported',?,?)",
                            (study["study_uid"], candidate, study.get("purpose", ""), study.get("insight_markdown", ""),
                             study.get("created_at") or _now(), study.get("updated_at") or _now()),
                        )
                        study_id = int(cur.lastrowid)
                        report["studies_added"] += 1
                    for run_key in study.get("run_keys", []):
                        if run_key in run_ids:
                            self.conn.execute(
                                "INSERT OR IGNORE INTO study_runs(study_id,run_id,added_at) VALUES(?,?,?)",
                                (study_id, run_ids[run_key], _now()),
                            )
                self.conn.commit()
            except BaseException:
                self.conn.rollback()
                raise
        return report

