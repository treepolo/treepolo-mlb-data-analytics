from __future__ import annotations

import threading
import time
from datetime import datetime, timezone

_lock = threading.Lock()
_progress: dict[str, dict] = {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def start_sync(kind: str, run_id: int, start_date: str, end_date: str, total_chunks: int) -> None:
    with _lock:
        _progress[kind] = {
            "kind": kind,
            "run_id": run_id,
            "status": "running",
            "start_date": start_date,
            "end_date": end_date,
            "total_chunks": total_chunks,
            "completed_chunks": 0,
            "success_chunks": 0,
            "failed_chunks": 0,
            "skipped_chunks": 0,
            "rows_received": 0,
            "current_start": None,
            "current_end": None,
            "started_at": _now_iso(),
            "finished_at": None,
            "_started_monotonic": time.monotonic(),
        }


def set_current_chunk(kind: str, start_date: str, end_date: str) -> None:
    with _lock:
        item = _progress.get(kind)
        if item is None:
            return
        item["current_start"] = start_date
        item["current_end"] = end_date


def complete_chunk(kind: str, *, status: str, rows_received: int = 0) -> None:
    with _lock:
        item = _progress.get(kind)
        if item is None:
            return
        item["completed_chunks"] += 1
        item["rows_received"] += int(rows_received)
        if status == "success":
            item["success_chunks"] += 1
        elif status == "failed":
            item["failed_chunks"] += 1
        elif status == "skipped":
            item["skipped_chunks"] += 1
        item["current_start"] = None
        item["current_end"] = None


def finish_sync(kind: str, status: str) -> None:
    with _lock:
        item = _progress.get(kind)
        if item is None:
            return
        item["status"] = status
        item["current_start"] = None
        item["current_end"] = None
        item["finished_at"] = _now_iso()


def get_sync_progress(kind: str) -> dict | None:
    with _lock:
        item = _progress.get(kind)
        if item is None:
            return None
        result = {key: value for key, value in item.items() if not key.startswith("_")}
        elapsed = max(0.0, time.monotonic() - item["_started_monotonic"])
        total = max(0, int(item["total_chunks"]))
        completed = max(0, int(item["completed_chunks"]))
        result["elapsed_seconds"] = round(elapsed, 1)
        result["percent"] = round((completed / total * 100.0) if total else 100.0, 1)
        if item["status"] == "running" and completed > 0 and completed < total:
            result["eta_seconds"] = round((elapsed / completed) * (total - completed), 1)
        else:
            result["eta_seconds"] = 0.0 if completed >= total and total else None
        return result
