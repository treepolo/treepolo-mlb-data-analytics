from __future__ import annotations

import json
import mimetypes
import sqlite3
import threading
import webbrowser
from datetime import date
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .analysis_jobs import finish_analysis_job, get_analysis_job, start_analysis_job, update_analysis_job
from .analysis_state import AnalysisStateStore, analysis_cache_key, read_data_revision
from .config import AppConfig
from .duckdb_mirror import DuckDBMirror
from .fast_status import prepare_fast_status, read_fast_status, rebuild_fast_status
from .raw import RawArchive
from .savant import SavantClient
from .storage import StatcastStore
from .sync import SyncEngine
from .sync_progress import get_sync_progress
from .web_analysis import AnalysisFacade, RequestError, _jsonable

STATIC_DIR = Path(__file__).with_name("web_static")


class AppServices:
    def __init__(self, config: AppConfig):
        self.config = config
        self.analysis = AnalysisFacade(
            config.database_path,
            config.analytics_database_path,
            backend=config.analysis_backend,
        )
        self.analysis_state = AnalysisStateStore(config.analysis_state_database_path)
        self.sync_lock = threading.Lock()
        self._scheduler_started = False
        self._summary_bootstrap_started = False
        self._analytics_bootstrap_started = False

    def _sync_engine(self):
        store = StatcastStore(self.config.database_path)
        archive = RawArchive(self.config.root)
        client = SavantClient(
            self.config.request_timeout_seconds,
            self.config.request_retries,
            self.config.request_backoff_seconds,
            self.config.request_pause_seconds,
        )
        return store, SyncEngine(self.config, store, client, archive)

    def start_summary_bootstrap(self) -> None:
        if self._summary_bootstrap_started:
            return
        self._summary_bootstrap_started = True
        if not prepare_fast_status(self.config.database_path):
            return

        def worker() -> None:
            # One-time migration for databases created before fast status existed.
            # Keep it off the request path so the UI opens immediately.
            with self.sync_lock:
                try:
                    rebuild_fast_status(self.config.database_path)
                except Exception as exc:
                    print(f"fast status bootstrap failed: {exc}")

        threading.Thread(target=worker, name="treepolo-status-bootstrap", daemon=True).start()

    def start_analytics_bootstrap(self) -> None:
        if self._analytics_bootstrap_started or self.config.analysis_backend not in {"duckdb", "auto"}:
            return
        self._analytics_bootstrap_started = True
        # Never make first-time mirror construction surprise the user at UI startup.
        # Existing mirrors may refresh opportunistically; a missing mirror is built by
        # the first explicit analysis so its progress remains visible.
        if not self.config.database_path.exists() or not self.config.analytics_database_path.exists():
            return

        def worker() -> None:
            try:
                DuckDBMirror(self.config.database_path, self.config.analytics_database_path).ensure()
            except Exception as exc:
                # UI remains usable; the first analysis can retry or fall back.
                print(f"DuckDB analytical mirror bootstrap failed: {exc}")

        threading.Thread(target=worker, name="treepolo-duckdb-bootstrap", daemon=True).start()

    def start_scheduler(self) -> None:
        if self._scheduler_started:
            return
        self._scheduler_started = True

        def worker() -> None:
            store, engine = self._sync_engine()
            try:
                engine.scheduler()
            finally:
                store.close()

        threading.Thread(target=worker, name="treepolo-auto-update", daemon=True).start()

    @staticmethod
    def _result_backend(result: dict[str, Any]) -> str | None:
        backend = result.get("backend")
        if backend is None and result.get("sections"):
            backends = {section.get("backend") for section in result["sections"] if section.get("backend")}
            backend = "+".join(sorted(backends)) if backends else None
        return str(backend) if backend else None

    @staticmethod
    def _result_row_count(result: dict[str, Any]) -> int | None:
        if isinstance(result.get("row_count"), int):
            return int(result["row_count"])
        if result.get("sections"):
            return sum(int(section.get("row_count") or 0) for section in result["sections"])
        return None

    def analyze(self, payload: dict[str, Any]) -> dict[str, Any]:
        mode = str(payload.get("mode", "basic"))
        job_id = start_analysis_job(mode)
        data_revision = read_data_revision(self.config.database_path)
        cache_key = analysis_cache_key(
            payload=payload,
            data_revision=data_revision,
            backend=self.config.analysis_backend,
        )

        def progress(stage: str, percentage: float | None, detail: str | None) -> None:
            update_analysis_job(job_id, stage=stage, percentage=percentage, detail=detail)

        cached = self.analysis_state.get_cached_result(cache_key)
        if cached is not None:
            progress("cache_hit", 100.0, "Loaded identical analysis from persistent result cache")
            backend = self._result_backend(cached)
            history_id = self.analysis_state.record_history(
                payload=payload,
                data_revision=data_revision,
                cache_key=cache_key,
                backend=backend,
                row_count=self._result_row_count(cached),
                status="success",
            )
            finish_analysis_job(job_id, backend=backend)
            cached["cache"] = {"hit": True, "stored": True, "key": cache_key, "data_revision": data_revision}
            cached["history_id"] = history_id
            cached["job_id"] = job_id
            return cached

        try:
            result = self.analysis.analyze(payload, progress=progress)
            backend = self._result_backend(result)
            cache_result = dict(result)
            stored = self.analysis_state.put_cached_result(
                cache_key,
                data_revision=data_revision,
                backend=backend or self.config.analysis_backend,
                payload=payload,
                result=cache_result,
            )
            history_id = self.analysis_state.record_history(
                payload=payload,
                data_revision=data_revision,
                cache_key=cache_key if stored else None,
                backend=backend,
                row_count=self._result_row_count(result),
                status="success",
            )
            finish_analysis_job(job_id, backend=backend)
            result["cache"] = {"hit": False, "stored": stored, "key": cache_key, "data_revision": data_revision}
            result["history_id"] = history_id
            result["job_id"] = job_id
            return result
        except Exception as exc:
            self.analysis_state.record_history(
                payload=payload,
                data_revision=data_revision,
                cache_key=None,
                backend=None,
                row_count=None,
                status="failed",
                error=str(exc),
            )
            finish_analysis_job(job_id, error=str(exc))
            raise

    def history(self, limit: int = 50) -> list[dict[str, Any]]:
        return self.analysis_state.list_history(limit)

    def history_item(self, history_id: int) -> dict[str, Any] | None:
        return self.analysis_state.get_history(history_id)

    def saved_analyses(self) -> list[dict[str, Any]]:
        return self.analysis_state.list_saved()

    def saved_analysis(self, saved_id: int) -> dict[str, Any] | None:
        return self.analysis_state.get_saved(saved_id)

    def save_analysis(self, payload: dict[str, Any]) -> dict[str, Any]:
        analysis_payload = payload.get("analysis_payload")
        if not isinstance(analysis_payload, dict):
            raise RequestError("analysis_payload must be an object")
        return self.analysis_state.save_analysis(
            name=str(payload.get("name", "")),
            notes=str(payload.get("notes", "")),
            payload=analysis_payload,
            cache_key=str(payload.get("cache_key")) if payload.get("cache_key") else None,
            data_revision=str(payload.get("data_revision")) if payload.get("data_revision") else None,
        )

    def update_saved_analysis(self, saved_id: int, payload: dict[str, Any]) -> dict[str, Any] | None:
        analysis_payload = payload.get("analysis_payload")
        if analysis_payload is not None and not isinstance(analysis_payload, dict):
            raise RequestError("analysis_payload must be an object")
        return self.analysis_state.update_saved(
            saved_id,
            name=payload.get("name"),
            notes=payload.get("notes"),
            payload=analysis_payload,
            cache_key=str(payload.get("cache_key")) if payload.get("cache_key") else None,
            data_revision=str(payload.get("data_revision")) if payload.get("data_revision") else None,
        )

    def status(self) -> dict[str, Any]:
        # Deliberately avoid StatcastStore.verify(): ordinary UI status must not
        # scan the multi-million-row pitches table.
        status = read_fast_status(self.config.database_path)
        with StatcastStore(self.config.database_path) as store:
            enabled = store.get_setting(
                "auto_update_enabled", str(self.config.auto_update_enabled).lower()
            ) == "true"
            status["failed_chunks"] = store.conn.execute(
                "SELECT COUNT(*) FROM sync_chunks WHERE status='failed'"
            ).fetchone()[0]
            status["schema_columns"] = store.conn.execute(
                "SELECT COUNT(*) FROM schema_registry"
            ).fetchone()[0]
            status["raw_snapshots"] = store.conn.execute(
                "SELECT COUNT(*) FROM raw_snapshots"
            ).fetchone()[0]
        # pitch_uid is a primary key, so duplicate pitch_uid rows cannot exist.
        status["duplicate_pitch_uid"] = 0
        status["auto_update_enabled"] = enabled
        status["database_path"] = str(self.config.database_path)
        status["analysis_backend"] = self.config.analysis_backend
        status["analytics_database_path"] = str(self.config.analytics_database_path)
        status["analytics_database_exists"] = self.config.analytics_database_path.exists()
        status["analysis_state_database_path"] = str(self.config.analysis_state_database_path)
        status["backfill_progress"] = get_sync_progress("backfill")
        return status

    def data_action(self, action: str, payload: dict[str, Any]) -> Any:
        if action == "status":
            return self.status()
        with self.sync_lock:
            store, engine = self._sync_engine()
            try:
                if action == "update":
                    through = date.fromisoformat(payload["through"]) if payload.get("through") else None
                    return _jsonable(engine.update(through))
                if action == "backfill":
                    start = date.fromisoformat(str(payload.get("start") or self.config.earliest_date))
                    end = date.fromisoformat(str(payload.get("end") or date.today().isoformat()))
                    return _jsonable(
                        engine.backfill(
                            start,
                            end,
                            continue_on_error=not bool(payload.get("fail_fast", False)),
                            resume=bool(payload.get("resume", True)),
                        )
                    )
                if action == "retry-failed":
                    return _jsonable(engine.retry_failed())
                if action == "auto-update":
                    enabled = bool(payload.get("enabled", False))
                    store.set_setting("auto_update_enabled", "true" if enabled else "false")
                    return {"auto_update_enabled": enabled}
                if action == "verify":
                    # Explicitly requested deep verification is allowed to scan
                    # the full pitch table. Refresh the persistent summary too.
                    result = store.verify()
                    store.close()
                    rebuild_fast_status(self.config.database_path)
                    return result
                if action == "rebuild":
                    if payload.get("confirmation") != "REBUILD":
                        raise RequestError("Rebuild requires explicit confirmation")
                    store.close()
                    for suffix in ("", "-wal", "-shm"):
                        path = Path(str(self.config.database_path) + suffix)
                        if path.exists():
                            path.unlink()
                    for suffix in ("", ".wal"):
                        path = Path(str(self.config.analytics_database_path) + suffix)
                        if path.exists():
                            path.unlink()
                    store = StatcastStore(self.config.database_path)
                    prepare_fast_status(self.config.database_path)
                    engine = SyncEngine(self.config, store, engine.fetcher, engine.archive)
                    return {"snapshots_reingested": engine.rebuild_from_raw()}
                raise RequestError(f"Unknown data action: {action}")
            finally:
                try:
                    store.close()
                except Exception:
                    pass


class _Handler(BaseHTTPRequestHandler):
    server_version = "treepolo-mlb-ui/0.1"

    @property
    def services(self) -> AppServices:
        return self.server.services  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[ui] {self.address_string()} - {fmt % args}")

    def _json(self, status: int, payload: Any) -> None:
        body = json.dumps(_jsonable(payload), ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length > 2_000_000:
            raise RequestError("Request body is too large")
        raw = self.rfile.read(length) if length else b"{}"
        value = json.loads(raw.decode("utf-8"))
        if not isinstance(value, dict):
            raise RequestError("JSON request must be an object")
        return value

    @staticmethod
    def _path_id(path: str, prefix: str) -> int | None:
        suffix = path.removeprefix(prefix).strip("/")
        if not suffix:
            return None
        try:
            return int(suffix)
        except ValueError as exc:
            raise RequestError("Invalid analysis item id") from exc

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            if path == "/api/meta":
                self._json(HTTPStatus.OK, self.services.analysis.meta())
                return
            if path == "/api/analysis/progress":
                job_id = (parse_qs(parsed.query).get("job_id") or [None])[0]
                self._json(HTTPStatus.OK, {"progress": get_analysis_job(job_id)})
                return
            if path == "/api/analysis/history":
                limit_raw = (parse_qs(parsed.query).get("limit") or ["50"])[0]
                self._json(HTTPStatus.OK, {"history": self.services.history(int(limit_raw))})
                return
            if path.startswith("/api/analysis/history/"):
                history_id = self._path_id(path, "/api/analysis/history/")
                item = self.services.history_item(int(history_id)) if history_id is not None else None
                self._json(HTTPStatus.OK if item is not None else HTTPStatus.NOT_FOUND, {"item": item})
                return
            if path == "/api/analysis/saved":
                self._json(HTTPStatus.OK, {"saved": self.services.saved_analyses()})
                return
            if path.startswith("/api/analysis/saved/"):
                saved_id = self._path_id(path, "/api/analysis/saved/")
                item = self.services.saved_analysis(int(saved_id)) if saved_id is not None else None
                self._json(HTTPStatus.OK if item is not None else HTTPStatus.NOT_FOUND, {"item": item})
                return
            if path == "/api/data/backfill-progress":
                self._json(HTTPStatus.OK, {"progress": get_sync_progress("backfill")})
                return
            if path == "/api/data/status":
                self._json(HTTPStatus.OK, self.services.status())
                return
            self._static(path)
        except Exception as exc:
            self._error(exc)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            payload = self._read_json()
            if path == "/api/analyze":
                self._json(HTTPStatus.OK, self.services.analyze(payload))
                return
            if path == "/api/analysis/saved":
                self._json(HTTPStatus.OK, {"item": self.services.save_analysis(payload)})
                return
            if path.startswith("/api/analysis/saved/"):
                saved_id = self._path_id(path, "/api/analysis/saved/")
                item = self.services.update_saved_analysis(int(saved_id), payload) if saved_id is not None else None
                self._json(HTTPStatus.OK if item is not None else HTTPStatus.NOT_FOUND, {"item": item})
                return
            if path.startswith("/api/data/"):
                action = path.removeprefix("/api/data/")
                self._json(HTTPStatus.OK, self.services.data_action(action, payload))
                return
            self._json(HTTPStatus.NOT_FOUND, {"error": "Unknown API endpoint"})
        except Exception as exc:
            self._error(exc)

    def do_DELETE(self) -> None:
        path = urlparse(self.path).path
        try:
            if path.startswith("/api/analysis/saved/"):
                saved_id = self._path_id(path, "/api/analysis/saved/")
                deleted = self.services.analysis_state.delete_saved(int(saved_id)) if saved_id is not None else False
                self._json(HTTPStatus.OK if deleted else HTTPStatus.NOT_FOUND, {"deleted": deleted})
                return
            self._json(HTTPStatus.NOT_FOUND, {"error": "Unknown API endpoint"})
        except Exception as exc:
            self._error(exc)

    def _error(self, exc: Exception) -> None:
        if isinstance(exc, (RequestError, ValueError, KeyError, json.JSONDecodeError)):
            status = HTTPStatus.BAD_REQUEST
        else:
            status = HTTPStatus.INTERNAL_SERVER_ERROR
        self._json(status, {"error": str(exc), "type": type(exc).__name__})

    def _static(self, request_path: str) -> None:
        relative = "index.html" if request_path in {"", "/"} else request_path.lstrip("/")
        candidate = (STATIC_DIR / relative).resolve()
        root = STATIC_DIR.resolve()
        if root not in candidate.parents and candidate != root:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        if not candidate.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        body = candidate.read_bytes()
        if relative == "index.html":
            body = body.replace(
                b"</body>",
                b'<script src="/field-checklists.js"></script>\n'
                b'<script src="/analysis-controls.js"></script>\n'
                b'<script src="/analysis-progress.js"></script>\n'
                b'<script src="/stage4-analysis-pages.js"></script>\n'
                b'<script src="/stage4-controls.js"></script>\n'
                b'<script src="/backfill-progress.js"></script>\n'
                b'<script src="/fast-status.js"></script>\n</body>',
            )
        content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type + ("; charset=utf-8" if content_type.startswith("text/") else ""))
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def serve(config: AppConfig, host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True) -> None:
    if not STATIC_DIR.exists():
        raise RuntimeError(f"Frontend assets are missing: {STATIC_DIR}")
    services = AppServices(config)
    services.start_summary_bootstrap()
    services.start_analytics_bootstrap()
    services.start_scheduler()
    server = ThreadingHTTPServer((host, port), _Handler)
    server.services = services  # type: ignore[attr-defined]
    url = f"http://{host}:{port}/"
    print(f"treepolo MLB Data Analytics UI: {url}")
    print("Press Ctrl+C to stop.")
    if open_browser:
        threading.Timer(0.25, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        services.analysis_state.close()
