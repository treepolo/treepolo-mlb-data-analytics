from __future__ import annotations

import threading
import webbrowser
from pathlib import Path
from http.server import ThreadingHTTPServer
from typing import Any

from .analysis_state import AnalysisStateStore
from .config import AppConfig
from .cpbl_client import CPBLClient
from .cpbl_sync import CPBLSyncEngine
from .datasets import DatasetConfigView, dataset_config, dataset_spec
from .fast_status import prepare_fast_status
from .storage import StatcastStore
from .web_analysis import AnalysisFacade, RequestError, _jsonable
from .webapp import AppServices, STATIC_DIR, _Handler


class DatasetAppServices(AppServices):
    """The existing app service surface, pinned to one physical dataset."""

    def __init__(self, base_config: AppConfig, dataset_id: str):
        self.base_config = base_config
        self.spec = dataset_spec(base_config, dataset_id)
        view = dataset_config(base_config, dataset_id)
        super().__init__(view)  # type: ignore[arg-type]
        self.config: DatasetConfigView = view
        # Replace the generic facade constructed by AppServices so metadata also
        # carries the dataset contract and units.
        self.analysis = AnalysisFacade(
            self.spec.database_path,
            self.spec.analytics_database_path,
            backend=base_config.analysis_backend,
            dataset_id=self.spec.dataset_id,
            dataset_label=self.spec.label,
            speed_unit=self.spec.speed_unit,
            distance_unit=self.spec.distance_unit,
        )

    def _sync_engine(self):
        if self.spec.dataset_id == "mlb":
            return super()._sync_engine()
        store = StatcastStore(self.spec.database_path)
        client = CPBLClient(
            self.base_config.request_timeout_seconds,
            self.base_config.request_retries,
            self.base_config.request_backoff_seconds,
            self.base_config.request_pause_seconds,
        )
        engine = CPBLSyncEngine(
            self.spec.database_path,
            self.spec.raw_root,
            client,
            analytics_database_path=self.spec.analytics_database_path,
            recent_refresh_days=self.spec.recent_refresh_days,
            auto_update_interval_hours=self.base_config.auto_update_interval_hours,
        )
        return store, engine

    def status(self) -> dict[str, Any]:
        result = super().status()
        result["dataset"] = {
            "id": self.spec.dataset_id,
            "label": self.spec.label,
            "source": self.spec.source,
            "speed_unit": self.spec.speed_unit,
            "distance_unit": self.spec.distance_unit,
        }
        return result

    def data_action(self, action: str, payload: dict[str, Any]) -> Any:
        if self.spec.dataset_id != "cpbl":
            return super().data_action(action, payload)
        if action.startswith("supplemental-"):
            raise RequestError("MLB supplemental-data actions are not available in the CPBL workspace")
        if action != "rebuild":
            return super().data_action(action, payload)
        if payload.get("confirmation") != "REBUILD":
            raise RequestError("Rebuild requires explicit confirmation")
        with self.sync_lock:
            # Raw JSON is deliberately retained; only normalized/cache mirrors are rebuilt.
            for suffix in ("", "-wal", "-shm"):
                path = Path(str(self.spec.database_path) + suffix)
                if path.exists():
                    path.unlink()
            for suffix in ("", ".wal"):
                path = Path(str(self.spec.analytics_database_path) + suffix)
                if path.exists():
                    path.unlink()
            with StatcastStore(self.spec.database_path):
                pass
            prepare_fast_status(self.spec.database_path)
            _, engine = self._sync_engine()
            result = engine.rebuild_from_raw()
            return {"dataset": "cpbl", "rebuild": _jsonable(result)}


def serve_dataset(
    config: AppConfig,
    *,
    dataset_id: str = "mlb",
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = True,
) -> None:
    if not STATIC_DIR.exists():
        raise RuntimeError(f"Frontend assets are missing: {STATIC_DIR}")
    services = DatasetAppServices(config, dataset_id)
    services.start_summary_bootstrap()
    services.start_analytics_bootstrap()
    services.start_scheduler()
    server = ThreadingHTTPServer((host, port), _Handler)
    server.services = services  # type: ignore[attr-defined]
    url = f"http://{host}:{port}/"
    print(f"treepolo {services.spec.label} Data Analytics UI: {url}")
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
