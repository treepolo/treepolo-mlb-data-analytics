from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import parse_qs

from .bundle import MAX_IMPORT_BYTES, export_bundle, import_bundle
from .config_schema import ConfigError
from .service import ResearchService

PREFIX = "/api/research/"
_RUN = re.compile(r"^runs/(\d+)$")
_RUN_RESULT = re.compile(r"^runs/(\d+)/result$")
_RUN_RERUN = re.compile(r"^runs/(\d+)/rerun$")
_RUN_STUDIES = re.compile(r"^runs/(\d+)/studies$")
_STUDY = re.compile(r"^studies/(\d+)$")


@dataclass(frozen=True, slots=True)
class BinaryResponse:
    body: bytes
    content_type: str
    filename: str | None = None


Response = tuple[int, Any]
NOT_FOUND: Response = (404, {"error": "Unknown API endpoint"})


def _one(query: dict[str, list[str]], key: str, default: str | None = None) -> str | None:
    values = query.get(key)
    return values[0] if values else default


def _int(value: Any, name: str, default: int | None = None) -> int | None:
    if value in (None, ""):
        return default
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{name} must be an integer / {name} 必須是整數") from exc


def _strip(path: str) -> str | None:
    return path[len(PREFIX):].strip("/") if path.startswith(PREFIX) else None


def handle_get(service: ResearchService, path: str, query_string: str = "") -> Response:
    route = _strip(path)
    query = parse_qs(query_string)
    if route == "methods":
        return 200, {"methods": service.methods()}
    if route == "studies":
        return 200, {"studies": service.list_studies()}
    if route == "runs":
        runs = service.list_runs(
            study_id=_int(_one(query, "study_id"), "study_id"), kind=_one(query, "kind"), status=_one(query, "status"),
            limit=_int(_one(query, "limit"), "limit", 200), offset=_int(_one(query, "offset"), "offset", 0),
        )
        return 200, {"runs": runs}
    if route is not None:
        match = _RUN_RESULT.match(route)
        if match:
            page = service.result_page(
                int(match.group(1)), _int(_one(query, "section"), "section", 0),
                _int(_one(query, "offset"), "offset", 0), _int(_one(query, "limit"), "limit", 200),
            )
            return 200, page
        match = _RUN.match(route)
        if match:
            item = service.run_detail(int(match.group(1)))
            return (200, {"item": item}) if item is not None else (404, {"item": None})
    return NOT_FOUND


def handle_post(service: ResearchService, path: str, payload: dict[str, Any]) -> Response:
    route = _strip(path)
    if route == "studies":
        study = service.create_study(
            payload.get("name", ""), str(payload.get("purpose", "")), str(payload.get("insight_markdown", ""))
        )
        return 200, {"item": study}
    if route == "run":
        outcome = service.run(
            str(payload.get("kind", "")), payload.get("config") or {}, study_id=_int(payload.get("study_id"), "study_id"),
            study_name=payload.get("study_name"), force=bool(payload.get("force", False)),
        )
        return 200, outcome
    if route == "export":
        data = export_bundle(
            service.store, study_ids=[_int(v, "study_ids") for v in payload.get("study_ids") or []],
            run_ids=[_int(v, "run_ids") for v in payload.get("run_ids") or []],
            include_artifacts=bool(payload.get("include_artifacts", True)), notes=str(payload.get("notes", "")),
        )
        name = f"research-{datetime.now().strftime('%Y%m%d-%H%M%S')}.treepolo-research.zip"
        return 200, BinaryResponse(data, "application/zip", name)
    if route is not None:
        match = _STUDY.match(route)
        if match:
            fields = {key: payload[key] for key in ("name", "purpose", "insight_markdown") if key in payload}
            item = service.update_study(int(match.group(1)), **fields)
            return (200, {"item": item}) if item is not None else (404, {"item": None})
        match = _RUN_RERUN.match(route)
        if match:
            return 200, service.rerun(int(match.group(1)), study_id=_int(payload.get("study_id"), "study_id"))
        match = _RUN_STUDIES.match(route)
        if match:
            changed = service.link_run(int(match.group(1)), _int(payload.get("study_id"), "study_id"), str(payload.get("action", "")))
            return 200, {"changed": changed}
    return NOT_FOUND


def handle_import(service: ResearchService, data: bytes) -> Response:
    return 200, import_bundle(service.store, data)


def handle_delete(service: ResearchService, path: str, query_string: str = "") -> Response:
    route = _strip(path)
    query = parse_qs(query_string)
    if route is not None:
        match = _STUDY.match(route)
        if match:
            delete_runs = _one(query, "delete_runs", "0") in {"1", "true", "yes"}
            deleted = service.delete_study(int(match.group(1)), delete_runs)
            return (200 if deleted else 404), {"deleted": deleted}
        match = _RUN.match(route)
        if match:
            deleted = service.delete_run(int(match.group(1)))
            return (200 if deleted else 404), {"deleted": deleted}
    return NOT_FOUND


__all__ = ["BinaryResponse", "MAX_IMPORT_BYTES", "PREFIX", "handle_delete", "handle_get", "handle_import", "handle_post"]
