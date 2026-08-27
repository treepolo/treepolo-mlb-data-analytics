from __future__ import annotations

import importlib
from typing import Any


class _LazyDuckDB:
    """Load DuckDB's native extension only when analytical execution needs it."""

    def __init__(self):
        self._module = None

    def _load(self):
        if self._module is None:
            self._module = importlib.import_module("duckdb")
        return self._module

    def __getattr__(self, name: str) -> Any:
        return getattr(self._load(), name)


duckdb = _LazyDuckDB()
