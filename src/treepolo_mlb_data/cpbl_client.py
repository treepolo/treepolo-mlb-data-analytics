from __future__ import annotations

import time
from datetime import date
from typing import Any

import requests

CPBL_API_BASE = "https://stats.cpbl.com.tw/api/proxy"


def _unwrap(value: Any) -> Any:
    if isinstance(value, dict):
        for key in ("Data", "data"):
            if key in value:
                return value[key]
    return value


class CPBLClient:
    """HTTP client for the CPBL advanced-statistics public API."""

    def __init__(
        self,
        timeout_seconds: int = 90,
        retries: int = 4,
        backoff_seconds: float = 1.5,
        pause_seconds: float = 0.25,
        *,
        base_url: str = CPBL_API_BASE,
        session: requests.Session | None = None,
    ):
        self.timeout_seconds = timeout_seconds
        self.retries = max(1, retries)
        self.backoff_seconds = max(0.0, backoff_seconds)
        self.pause_seconds = max(0.0, pause_seconds)
        self.base_url = base_url.rstrip("/")
        self.session = session or requests.Session()
        self.session.headers.setdefault("Accept", "application/json")
        self.session.headers.setdefault("User-Agent", "Mozilla/5.0 treepolo-cpbl-data-mirror/1.0")

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        cleaned = None
        if params is not None:
            cleaned = {key: value for key, value in params.items() if value not in (None, "", 0)}
        url = f"{self.base_url}{path}"
        last: Exception | None = None
        for attempt in range(self.retries):
            try:
                response = self.session.get(url, params=cleaned, timeout=self.timeout_seconds)
                response.raise_for_status()
                payload = response.json()
                if isinstance(payload, dict) and payload.get("success") is False:
                    raise RuntimeError(f"CPBL API returned failure: {payload}")
                if self.pause_seconds:
                    time.sleep(self.pause_seconds)
                return payload
            except Exception as exc:
                last = exc
                if attempt + 1 < self.retries:
                    time.sleep(self.backoff_seconds * (attempt + 1))
        raise RuntimeError(f"CPBL API request failed: {url}: {last}") from last

    def schedule(self, day: date | str) -> list[dict[str, Any]]:
        day_text = day.isoformat() if isinstance(day, date) else str(day)
        inner = _unwrap(self._get(f"/v1/games/schedule/{day_text}"))
        if isinstance(inner, dict):
            inner = inner.get("Games", inner.get("games", []))
        return [item for item in inner if isinstance(item, dict)] if isinstance(inner, list) else []

    def game(self, game_id: str) -> dict[str, Any]:
        inner = _unwrap(self._get(f"/v1/games/{game_id}"))
        if isinstance(inner, dict) and isinstance(inner.get("Game"), dict):
            inner = inner["Game"]
        return inner if isinstance(inner, dict) else {}
