from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import AppConfig


@dataclass(frozen=True, slots=True)
class DatasetSpec:
    dataset_id: str
    label: str
    source: str
    root: Path
    database_path: Path
    analytics_database_path: Path
    analysis_state_database_path: Path
    earliest_date: str
    recent_refresh_days: int
    speed_unit: str
    distance_unit: str

    @property
    def raw_root(self) -> Path:
        return self.root


class DatasetConfigView:
    """AppConfig-compatible view pinned to exactly one isolated dataset."""

    def __init__(self, base: AppConfig, spec: DatasetSpec):
        self.base = base
        self.spec = spec

    @property
    def root(self) -> Path:
        return self.spec.root

    @property
    def database_path(self) -> Path:
        return self.spec.database_path

    @property
    def analytics_database_path(self) -> Path:
        return self.spec.analytics_database_path

    @property
    def analysis_state_database_path(self) -> Path:
        return self.spec.analysis_state_database_path

    @property
    def earliest_date(self) -> str:
        return self.spec.earliest_date

    @property
    def recent_refresh_days(self) -> int:
        return self.spec.recent_refresh_days

    def __getattr__(self, name: str) -> Any:
        return getattr(self.base, name)


SUPPORTED_DATASETS = ("mlb", "cpbl")


def dataset_spec(config: AppConfig, dataset_id: str) -> DatasetSpec:
    dataset_id = str(dataset_id).strip().lower()
    if dataset_id == "mlb":
        return DatasetSpec(
            dataset_id="mlb",
            label="MLB / Baseball Savant",
            source="savant",
            root=config.root,
            database_path=config.database_path,
            analytics_database_path=config.analytics_database_path,
            analysis_state_database_path=config.analysis_state_database_path,
            earliest_date=config.earliest_date,
            recent_refresh_days=config.recent_refresh_days,
            speed_unit="mph",
            distance_unit="ft",
        )
    if dataset_id == "cpbl":
        root = config.root / "cpbl"
        return DatasetSpec(
            dataset_id="cpbl",
            label="CPBL / Trackman",
            source="cpbl",
            root=root,
            database_path=root / "cpbl.sqlite3",
            analytics_database_path=root / "cpbl.duckdb",
            analysis_state_database_path=root / "analysis_state.sqlite3",
            earliest_date="2026-01-01",
            recent_refresh_days=7,
            speed_unit="kph",
            distance_unit="m",
        )
    raise ValueError(f"Unsupported dataset: {dataset_id!r}; expected one of {SUPPORTED_DATASETS}")


def dataset_config(config: AppConfig, dataset_id: str) -> DatasetConfigView:
    return DatasetConfigView(config, dataset_spec(config, dataset_id))
