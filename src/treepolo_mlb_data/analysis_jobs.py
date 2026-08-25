from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(slots=True)
class AnalysisJob:
    job_id: str
    mode: str
    status: str = "running"
    stage: str = "queued"
    percentage: float | None = 0.0
    detail: str | None = None
    backend: str | None = None
    error: str | None = None
    started_at: str = field(default_factory=_now_iso)
    finished_at: str | None = None
    _started_monotonic: float = field(default_factory=time.monotonic, repr=False)
    _finished_monotonic: float | None = field(default=None, repr=False)

    def as_dict(self) -> dict[str, Any]:
        end = self._finished_monotonic if self._finished_monotonic is not None else time.monotonic()
        return {
            "job_id": self.job_id,
            "mode": self.mode,
            "status": self.status,
            "stage": self.stage,
            "percentage": self.percentage,
            "detail": self.detail,
            "backend": self.backend,
            "error": self.error,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "elapsed_seconds": max(0.0, end - self._started_monotonic),
        }


_LOCK = threading.Lock()
_JOBS: dict[str, AnalysisJob] = {}
_ORDER: list[str] = []
_MAX_JOBS = 30


def start_analysis_job(mode: str) -> str:
    job = AnalysisJob(uuid.uuid4().hex, mode)
    with _LOCK:
        _JOBS[job.job_id] = job
        _ORDER.append(job.job_id)
        while len(_ORDER) > _MAX_JOBS:
            old = _ORDER.pop(0)
            _JOBS.pop(old, None)
    return job.job_id


def update_analysis_job(
    job_id: str,
    *,
    stage: str | None = None,
    percentage: float | None = None,
    detail: str | None = None,
    backend: str | None = None,
    preserve_percentage: bool = False,
) -> None:
    with _LOCK:
        job = _JOBS.get(job_id)
        if job is None:
            return
        if stage is not None:
            job.stage = stage
        if not preserve_percentage:
            job.percentage = None if percentage is None else max(0.0, min(float(percentage), 100.0))
        if detail is not None:
            job.detail = detail
        if backend is not None:
            job.backend = backend


def finish_analysis_job(job_id: str, *, backend: str | None = None, error: str | None = None) -> None:
    with _LOCK:
        job = _JOBS.get(job_id)
        if job is None:
            return
        job.status = "failed" if error else "success"
        job.stage = "failed" if error else "completed"
        job.percentage = 100.0 if not error else job.percentage
        job.error = error
        if backend is not None:
            job.backend = backend
        job.finished_at = _now_iso()
        job._finished_monotonic = time.monotonic()
        if error:
            job.detail = error
        elif not job.detail:
            job.detail = "Analysis complete"


def get_analysis_job(job_id: str | None = None) -> dict[str, Any] | None:
    with _LOCK:
        if job_id is None:
            if not _ORDER:
                return None
            job_id = _ORDER[-1]
        job = _JOBS.get(job_id)
        return job.as_dict() if job is not None else None
