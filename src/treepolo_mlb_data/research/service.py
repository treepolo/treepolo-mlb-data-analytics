from __future__ import annotations

import hashlib
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable, Mapping

from .. import __version__
from ..analysis_jobs import finish_analysis_job, start_analysis_job, update_analysis_job
from ..analysis_state import canonical_json
from ..config import AppConfig
from . import builtin_methods  # noqa: F401  (registers built-in methods)
from .config_schema import ConfigError, normalize_config
from .methods import ResearchContext, get_method, list_methods
from .scope import compute_scope_fingerprint, normalize_scope
from .store import ResearchStore

RUN_KEY_FORMAT = "research-run-v1"
RESULT_FORMAT = "research-result-v1"
DEFAULT_STUDY_NAME = "未分類 Unsorted"
MAX_PAGE_LIMIT = 1000

_CODE_VERSION: str | None = None
_CODE_VERSION_LOCK = threading.Lock()


def code_version() -> str:
    """Package version, plus the short git commit when available (informational only)."""

    global _CODE_VERSION
    with _CODE_VERSION_LOCK:
        if _CODE_VERSION is None:
            version = __version__
            try:
                sha = subprocess.run(
                    ["git", "rev-parse", "--short=7", "HEAD"], cwd=Path(__file__).resolve().parent,
                    capture_output=True, text=True, timeout=2, check=False,
                ).stdout.strip()
                if sha:
                    version = f"{__version__}+g{sha}"
            except (OSError, subprocess.SubprocessError):
                pass
            _CODE_VERSION = version
        return _CODE_VERSION


def make_run_key(kind: str, method_version: int, config: Mapping[str, Any], scope_fingerprint: str) -> str:
    material = canonical_json({
        "format": RUN_KEY_FORMAT, "kind": kind, "method_version": int(method_version),
        "config": dict(config), "scope_fingerprint": scope_fingerprint,
    })
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


class ResearchService:
    def __init__(self, config: AppConfig, facade: Any, store: ResearchStore | None = None):
        self.config = config
        self.facade = facade
        self.store = store or ResearchStore(config.research_state_database_path, config.research_blob_dir)
        self._lock = threading.Lock()
        self._inflight: set[str] = set()

    # ------------------------------------------------------------------ meta
    def methods(self) -> list[dict[str, Any]]:
        return [method.describe() for method in list_methods()]

    def close(self) -> None:
        self.store.close()

    # ------------------------------------------------------------------- run
    def _resolve_study(self, study_id: int | None, study_name: str | None) -> dict[str, Any]:
        if study_id is not None:
            study = self.store.get_study(int(study_id))
            if study is None:
                raise ConfigError(f"Study not found: {study_id} / 找不到研究專案: {study_id}")
            return study
        return self.store.get_or_create_study(study_name or DEFAULT_STUDY_NAME)

    def run(
        self, kind: str, config: Mapping[str, Any], *, study_id: int | None = None, study_name: str | None = None,
        force: bool = False, parent_run_id: int | None = None,
        progress: Callable[[str, float | None, str | None], None] | None = None,
    ) -> dict[str, Any]:
        method = get_method(kind)
        if not isinstance(config, Mapping):
            raise ConfigError("Settings must be an object / 設定必須是物件")
        raw = dict(config)
        raw_scope = raw.pop("scope", None)
        resolved = normalize_config(method.fields, raw)
        scope = normalize_scope(raw_scope, required=method.requires_scope)
        resolved["scope"] = scope
        ctx = ResearchContext(config=self.config, facade=self.facade, scope=scope, progress=None)
        method.validate(resolved, ctx)

        fingerprint = compute_scope_fingerprint(self.config.database_path, scope)
        if scope and not fingerprint["total_rows"]:
            raise ConfigError("Scope contains no pitches / 研究範圍內沒有資料")
        run_key = make_run_key(kind, method.version, resolved, fingerprint["scope_fingerprint"])
        study = self._resolve_study(study_id, study_name)

        with self._lock:
            existing = self.store.find_run_by_key(run_key)
            if run_key in self._inflight:
                raise ConfigError("The same run is already in progress / 相同的研究正在執行")
            if existing is not None and existing["status"] == "success" and not force:
                self.store.link_run(study["id"], existing["id"])
                return {"run": self.store.get_run(existing["id"]), "reused": True, "reproduced": None}
            self._inflight.add(run_key)
        try:
            return self._execute(method, kind, resolved, scope, fingerprint, run_key, existing, study, parent_run_id, progress)
        finally:
            with self._lock:
                self._inflight.discard(run_key)

    def _execute(
        self, method, kind, resolved, scope, fingerprint, run_key, existing, study, parent_run_id, progress,
    ) -> dict[str, Any]:
        version = code_version()
        if existing is not None and parent_run_id == existing["id"]:
            parent_run_id = None
        previous_hash = existing["result_hash"] if existing is not None and existing["status"] == "success" else None
        if existing is None:
            run_id = self.store.insert_run(
                run_key=run_key, kind=kind, method_version=method.version, config=resolved, scope=scope,
                data_fingerprint=fingerprint, code_version=version, parent_run_id=parent_run_id,
            )
        else:
            run_id = existing["id"]
            self.store.restart_run(run_id, code_version=version, data_fingerprint=fingerprint, parent_run_id=parent_run_id)

        job_id = start_analysis_job(f"research:{kind}")

        def report(stage: str, percentage: float | None, detail: str | None) -> None:
            update_analysis_job(job_id, stage=stage, percentage=percentage, detail=detail)
            if progress is not None:
                progress(stage, percentage, detail)

        ctx = ResearchContext(config=self.config, facade=self.facade, scope=scope, progress=report)
        started = time.monotonic()
        try:
            output = method.run(ctx, resolved)
            sections = [dict(section) for section in output.sections]
            result = {"version": RESULT_FORMAT, "kind": kind, "sections": sections, "extras": dict(output.extras)}
            artifacts = [
                {"name": a.name, "description": a.description, "columns": list(a.columns), "rows": [dict(r) for r in a.rows]}
                for a in output.artifacts
            ]
            summary: dict[str, Any] = {
                "section_titles": [str(s.get("title", "")) for s in sections],
                "section_row_counts": [int(s.get("row_count", len(s.get("rows", [])))) for s in sections],
                "total_rows": sum(int(s.get("row_count", len(s.get("rows", [])))) for s in sections),
                "artifact_names": [a["name"] for a in artifacts],
            }
            reproduced: bool | None = None
            if previous_hash is not None:
                new_hash = hashlib.sha256(canonical_json(result).encode("utf-8")).hexdigest()
                reproduced = new_hash == previous_hash
                if not reproduced:
                    summary["previous_result_hash"] = previous_hash
                    summary["reproduced"] = False
            run = self.store.finish_run(
                run_id, result=result, artifacts=artifacts, summary=summary, duration_seconds=time.monotonic() - started
            )
        except BaseException as exc:
            self.store.fail_run(run_id, f"{type(exc).__name__}: {exc}", time.monotonic() - started)
            finish_analysis_job(job_id, error=str(exc))
            raise
        finish_analysis_job(job_id)
        self.store.link_run(study["id"], run_id)
        return {"run": self.store.get_run(run_id), "reused": False, "reproduced": reproduced}

    def rerun(self, run_id: int, *, study_id: int | None = None) -> dict[str, Any]:
        run = self.store.get_run(run_id)
        if run is None:
            raise ConfigError(f"Run not found: {run_id} / 找不到研究紀錄: {run_id}")
        return self.run(run["kind"], run["config"], study_id=study_id, force=True, parent_run_id=run["id"])

    # --------------------------------------------------------------- reading
    def run_detail(self, run_id: int) -> dict[str, Any] | None:
        run = self.store.get_run(run_id)
        if run is None:
            return None
        match: bool | None = None
        if run["scope"]:
            try:
                local = compute_scope_fingerprint(self.config.database_path, run["scope"])
                match = local["scope_fingerprint"] == run["data_fingerprint"].get("scope_fingerprint")
            except ConfigError:
                match = None
        run["local_data_match"] = match
        return run

    def result_page(self, run_id: int, section_index: int = 0, offset: int = 0, limit: int = 200) -> dict[str, Any]:
        if int(limit) < 1 or int(limit) > MAX_PAGE_LIMIT:
            raise ConfigError(f"limit must be between 1 and {MAX_PAGE_LIMIT} / limit 必須介於 1 與 {MAX_PAGE_LIMIT}")
        if int(offset) < 0:
            raise ConfigError("offset must be >= 0 / offset 不得為負")
        result = self.store.load_result(run_id)
        if result is None:
            raise ConfigError(f"Run has no result: {run_id} / 該研究紀錄沒有結果")
        sections = result.get("sections", [])
        if not 0 <= int(section_index) < len(sections):
            raise ConfigError(f"Section out of range: {section_index} / 結果節超出範圍")
        section = sections[int(section_index)]
        rows = section.get("rows", [])
        meta = {key: value for key, value in section.items() if key != "rows"}
        return {
            "section": meta, "section_count": len(sections), "offset": int(offset), "limit": int(limit),
            "total": len(rows), "rows": rows[int(offset): int(offset) + int(limit)],
        }

    # -------------------------------------------------- thin store wrappers
    def list_studies(self): return self.store.list_studies()
    def create_study(self, name, purpose="", insight_markdown=""): return self.store.create_study(name, purpose, insight_markdown)
    def update_study(self, study_id, **fields): return self.store.update_study(study_id, **fields)
    def delete_study(self, study_id, delete_runs=False): return self.store.delete_study(study_id, delete_runs)
    def list_runs(self, **filters): return self.store.list_runs(**filters)
    def delete_run(self, run_id): return self.store.delete_run(run_id)

    def link_run(self, run_id: int, study_id: int, action: str) -> bool:
        if self.store.get_run(run_id) is None or self.store.get_study(study_id) is None:
            raise ConfigError("Run or study not found / 找不到研究紀錄或研究專案")
        if action == "add":
            self.store.link_run(study_id, run_id)
            return True
        if action == "remove":
            return self.store.unlink_run(study_id, run_id)
        raise ConfigError("action must be add or remove / action 必須是 add 或 remove")
