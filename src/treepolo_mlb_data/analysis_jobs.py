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
    expected_seconds: float | None = None
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
            "expected_seconds": self.expected_seconds,
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
    expected_seconds: float | None = None,
) -> None:
    with _LOCK:
        job = _JOBS.get(job_id)
        if job is None:
            return
        if stage is not None:
            job.stage = stage
        if not preserve_percentage:
            if percentage is None:
                # Keep an existing determinate percentage when a fallback can
                # only report indeterminate work; never make the bar go backward.
                if job.percentage is None:
                    job.percentage = None
            else:
                value = max(0.0, min(float(percentage), 100.0))
                job.percentage = value if job.percentage is None else max(job.percentage, value)
        if detail is not None:
            job.detail = detail
        if backend is not None:
            job.backend = backend
        if expected_seconds is not None:
            job.expected_seconds = float(expected_seconds)


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
        else:
            job.detail = "Analysis complete"


def get_analysis_job(job_id: str | None = None) -> dict[str, Any] | None:
    with _LOCK:
        if job_id is None:
            if not _ORDER:
                return None
            job_id = _ORDER[-1]
        job = _JOBS.get(job_id)
        return job.as_dict() if job is not None else None


def list_research_jobs(recent_seconds: float = 90.0) -> list[dict[str, Any]]:
    """The research jobs that are running or finished less than ``recent_seconds`` ago (newest last), each with the time still to go worked out from how far it is (``eta_seconds``; None
    while the fraction is too small to say) and whether the fraction is an estimate from earlier runs (``estimated``)."""

    out: list[dict[str, Any]] = []
    now = time.monotonic()
    with _LOCK:
        jobs = [_JOBS[j] for j in _ORDER if j in _JOBS and _JOBS[j].mode.startswith("research:")]
    for job in jobs:
        d = job.as_dict()
        if job.status != "running" and job._finished_monotonic is not None and now - job._finished_monotonic > recent_seconds:
            continue
        p = d["percentage"]
        d["eta_seconds"] = d["elapsed_seconds"] * (100.0 - p) / p if job.status == "running" and isinstance(p, (int, float)) and 2.0 <= p < 100.0 else None
        d["estimated"] = bool(job.expected_seconds) and (isinstance(d["detail"], str) and "估計" in d["detail"])
        out.append(d)
    return out
