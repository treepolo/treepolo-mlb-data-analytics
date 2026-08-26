from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(slots=True)
class AppConfig:
    data_dir: str = "data"
    database_name: str = "statcast.sqlite3"
    analytics_database_name: str = "statcast.duckdb"
    analysis_state_database_name: str = "analysis_state.sqlite3"
    analysis_backend: str = "duckdb"
    earliest_date: str = "2015-01-01"
    backfill_chunk_days: int = 5
    recent_refresh_days: int = 7
    request_timeout_seconds: int = 90
    request_retries: int = 4
    request_backoff_seconds: float = 1.5
    request_pause_seconds: float = 0.25
    auto_update_enabled: bool = False
    auto_update_interval_hours: int = 24

    @property
    def root(self) -> Path:
        return Path(self.data_dir)

    @property
    def database_path(self) -> Path:
        return self.root / self.database_name

    @property
    def analytics_database_path(self) -> Path:
        return self.root / self.analytics_database_name

    @property
    def analysis_state_database_path(self) -> Path:
        return self.root / self.analysis_state_database_name


def load_config(path: Path) -> AppConfig:
    if not path.exists():
        return AppConfig()
    values = json.loads(path.read_text(encoding="utf-8"))
    allowed = AppConfig.__dataclass_fields__.keys()
    unknown = set(values) - set(allowed)
    if unknown:
        raise ValueError(f"Unknown configuration keys: {sorted(unknown)}")
    return AppConfig(**values)


def save_config(path: Path, config: AppConfig) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(asdict(config), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
