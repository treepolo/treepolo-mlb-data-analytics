"""Imports a heavy module on first attribute access, so that importing the web app (which registers every research method) stays light."""
from __future__ import annotations


class Lazy:
    def __init__(self, name: str, package: str = "treepolo_mlb_data.research"):
        self._name, self._package = name, package

    def __getattr__(self, item: str):
        import importlib

        return getattr(importlib.import_module(self._name, self._package), item)
