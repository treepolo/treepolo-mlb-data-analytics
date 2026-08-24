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
from urllib.parse import urlparse

from .config import AppConfig
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
        self.analysis = AnalysisFacade(config.database_path)
        self.sync_lock = threading.Lock()
        self._scheduler_started = False

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

    def status(self) -> dict[str, Any]:
        with StatcastStore(self.config.database_path) as store:
            try:
                verify = store.verify()
            except sqlite3.OperationalError:
                verify = {
                    "pitch_rows": 0,
                    "games": 0,
                    "latest_game_date": None,
                    "failed_chunks": 0,
                }
            enabled = store.get_setting(
                "auto_update_enabled", str(self.config.auto_update_enabled).lower()
            ) == "true"
        verify["auto_update_enabled"] = enabled
        verify["database_path"] = str(self.config.database_path)
        verify["backfill_progress"] = get_sync_progress("backfill")
        return verify

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
                if action == "rebuild":
                    if payload.get("confirmation") != "REBUILD":
                        raise RequestError("Rebuild requires explicit confirmation")
                    store.close()
                    for suffix in ("", "-wal", "-shm"):
                        path = Path(str(self.config.database_path) + suffix)
                        if path.exists():
                            path.unlink()
                    store = StatcastStore(self.config.database_path)
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

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        try:
            if path == "/api/meta":
                self._json(HTTPStatus.OK, self.services.analysis.meta())
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
                self._json(HTTPStatus.OK, self.services.analysis.analyze(payload))
                return
            if path.startswith("/api/data/"):
                action = path.removeprefix("/api/data/")
                self._json(HTTPStatus.OK, self.services.data_action(action, payload))
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
                b'<script src="/backfill-progress.js"></script>\n</body>',
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
