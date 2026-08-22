from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date
from typing import Protocol

import requests

BASE_URL = "https://baseballsavant.mlb.com/statcast_search/csv"


class FetchError(RuntimeError):
    pass


class Fetcher(Protocol):
    def fetch(self, start: date, end: date) -> bytes: ...


@dataclass(slots=True)
class SavantClient:
    timeout_seconds: int = 90
    retries: int = 4
    backoff_seconds: float = 1.5
    pause_seconds: float = 0.25
    user_agent: str = "treepolo-mlb-data-analytics/0.1 (+https://github.com/treepolo/treepolo-mlb-data-analytics)"
    _session: requests.Session = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": self.user_agent, "Accept": "text/csv,*/*;q=0.8"})

    @staticmethod
    def params(start: date, end: date) -> dict[str, str | int]:
        return {
            "all": "true", "hfPT": "", "hfAB": "", "hfBBT": "", "hfPR": "",
            "hfZ": "", "stadium": "", "hfBBL": "", "hfNewZones": "",
            "hfGT": "R|PO|S|", "hfSea": "", "hfSit": "", "player_type": "pitcher",
            "hfOuts": "", "opponent": "", "pitcher_throws": "", "batter_stands": "",
            "hfSA": "", "game_date_gt": start.isoformat(), "game_date_lt": end.isoformat(),
            "team": "", "position": "", "hfRO": "", "home_road": "", "hfFlag": "",
            "metric_1": "", "hfInn": "", "min_pitches": 0, "min_results": 0,
            "group_by": "name", "sort_col": "pitches", "player_event_sort": "h_launch_speed",
            "sort_order": "desc", "min_abs": 0, "type": "details",
        }

    def fetch(self, start: date, end: date) -> bytes:
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                response = self._session.get(BASE_URL, params=self.params(start, end), timeout=self.timeout_seconds)
                response.raise_for_status()
                content = response.content
                self._validate(content)
                if self.pause_seconds:
                    time.sleep(self.pause_seconds)
                return content
            except (requests.RequestException, FetchError) as exc:
                last_error = exc
                if attempt >= self.retries:
                    break
                time.sleep(self.backoff_seconds * (2**attempt))
        raise FetchError(f"Savant request failed for {start}..{end}: {last_error}") from last_error

    @staticmethod
    def _validate(content: bytes) -> None:
        if not content:
            raise FetchError("Savant returned an empty response")
        prefix = content[:4096].decode("utf-8", errors="replace").lstrip("\ufeff \r\n\t")
        first_line = prefix.splitlines()[0] if prefix.splitlines() else ""
        if "game_date" not in first_line or "," not in first_line:
            raise FetchError(f"Unexpected Savant response, first line={first_line[:200]!r}")
