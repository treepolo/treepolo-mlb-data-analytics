from __future__ import annotations

import io
import json
import re
import uuid
import zipfile
from datetime import datetime, timezone
from typing import Any, Iterable

from .. import __version__
from ..analysis_state import canonical_json
from .config_schema import ConfigError
from .store import ResearchStore

BUNDLE_FORMAT = "treepolo-research-bundle-v1"
MAX_IMPORT_BYTES = 1_000_000_000
_MEMBER = re.compile(
    r"^(manifest\.json|studies/[0-9a-f]{32}\.json|runs/[0-9a-f]{64}\.json|blobs/[0-9a-f]{64}\.json\.gz)$"
)
_RUN_FIELDS = (
    "run_key", "kind", "method_version", "config", "scope", "data_fingerprint", "status", "error", "summary",
    "result_hash", "result_bytes", "duration_seconds", "code_version", "created_at", "finished_at",
)


def _study_runs(store: ResearchStore, study_id: int) -> list[dict[str, Any]]:
    runs: list[dict[str, Any]] = []
    offset = 0
    while True:
        page = store.list_runs(study_id=study_id, status="success", limit=1000, offset=offset)
        runs.extend(page)
        if len(page) < 1000:
            return runs
        offset += 1000


def export_bundle(
    store: ResearchStore, *, study_ids: Iterable[int] | None = None, run_ids: Iterable[int] | None = None,
    include_artifacts: bool = True, notes: str = "", app_version: str = "",
) -> bytes:
    study_ids = list(study_ids or [])
    run_ids = list(run_ids or [])
    if not study_ids and not run_ids:
        raise ConfigError("Choose studies or runs to export / 請選擇要匯出的研究專案或研究紀錄")

    studies: list[dict[str, Any]] = []
    selected: dict[str, dict[str, Any]] = {}
    study_run_keys: dict[str, list[str]] = {}
    for study_id in study_ids:
        study = store.get_study(int(study_id))
        if study is None:
            raise ConfigError(f"Study not found: {study_id} / 找不到研究專案: {study_id}")
        studies.append(study)
        keys = []
        for run in _study_runs(store, study["id"]):
            selected[run["run_key"]] = run
            keys.append(run["run_key"])
        study_run_keys[study["study_uid"]] = keys
    for run_id in run_ids:
        run = store.get_run(int(run_id))
        if run is None:
            raise ConfigError(f"Run not found: {run_id} / 找不到研究紀錄: {run_id}")
        if run["status"] != "success":
            raise ConfigError(f"Run {run_id} did not finish successfully and cannot be exported / 研究紀錄 {run_id} 未成功完成，無法匯出")
        selected[run["run_key"]] = run

    members: list[tuple[str, bytes, int]] = []
    blob_hashes: dict[str, int] = {}
    manifest_runs = []
    for run_key, run in sorted(selected.items()):
        artifacts = list(run["artifacts"]) if include_artifacts else []
        payload = {key: run[key] for key in _RUN_FIELDS}
        payload["artifacts"] = artifacts
        members.append((f"runs/{run_key}.json", canonical_json(payload).encode("utf-8"), zipfile.ZIP_DEFLATED))
        hashes = [run["result_hash"]] + [a["blob_hash"] for a in artifacts]
        for blob_hash in hashes:
            blob_hashes[blob_hash] = 0
        manifest_runs.append({"run_key": run_key, "kind": run["kind"], "result_hash": run["result_hash"],
                              "artifact_hashes": [a["blob_hash"] for a in artifacts]})
    for blob_hash in sorted(blob_hashes):
        gz = store.read_blob_gzip_bytes(blob_hash)
        blob_hashes[blob_hash] = len(gz)
        members.append((f"blobs/{blob_hash}.json.gz", gz, zipfile.ZIP_STORED))
    for study in studies:
        payload = {k: study[k] for k in ("study_uid", "name", "purpose", "insight_markdown", "created_at", "updated_at")}
        members.append((f"studies/{study['study_uid']}.json", canonical_json(payload).encode("utf-8"), zipfile.ZIP_DEFLATED))

    manifest = {
        "format": BUNDLE_FORMAT, "bundle_uid": uuid.uuid4().hex, "created_at": datetime.now(timezone.utc).isoformat(),
        "app_version": app_version or __version__,
        "code_versions": sorted({run["code_version"] for run in selected.values()}),
        "notes": notes,
        "studies": [{"study_uid": s["study_uid"], "name": s["name"], "run_keys": study_run_keys.get(s["study_uid"], [])} for s in studies],
        "runs": manifest_runs,
        "blobs": [{"hash": h, "gzip_bytes": size} for h, size in sorted(blob_hashes.items())],
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("manifest.json", canonical_json(manifest).encode("utf-8"), compress_type=zipfile.ZIP_DEFLATED)
        for name, data, method in members:
            archive.writestr(name, data, compress_type=method)
    return buffer.getvalue()


def _read_json(archive: zipfile.ZipFile, name: str) -> dict[str, Any]:
    try:
        value = json.loads(archive.read(name).decode("utf-8"))
    except (KeyError, ValueError) as exc:
        raise ConfigError(f"Bundle member is unreadable: {name} / 研究檔成員無法讀取: {name}") from exc
    if not isinstance(value, dict):
        raise ConfigError(f"Bundle member must be an object: {name} / 研究檔成員必須是物件: {name}")
    return value


def import_bundle(store: ResearchStore, data: bytes) -> dict[str, Any]:
    from .service import make_run_key  # local import: service does not depend on this module

    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ConfigError("Not a valid research bundle / 不是有效的研究檔") from exc
    with archive:
        infos = archive.infolist()
        for info in infos:
            if not _MEMBER.match(info.filename):
                raise ConfigError(f"Unexpected bundle member: {info.filename} / 研究檔含不允許的成員")
        if sum(info.file_size for info in infos) > MAX_IMPORT_BYTES:
            raise ConfigError("Bundle is too large to import / 研究檔太大，無法匯入")
        names = {info.filename for info in infos}
        if "manifest.json" not in names:
            raise ConfigError("Bundle has no manifest / 研究檔缺少 manifest")
        manifest = _read_json(archive, "manifest.json")
        if manifest.get("format") != BUNDLE_FORMAT:
            raise ConfigError(f"Unsupported bundle format: {manifest.get('format')!r} / 不支援的研究檔格式")
        bundle_uid = str(manifest.get("bundle_uid") or "")
        if not bundle_uid:
            raise ConfigError("Bundle has no bundle_uid / 研究檔缺少 bundle_uid")

        declared = {str(item["hash"]) for item in manifest.get("blobs", [])}
        for blob_hash in declared:
            if f"blobs/{blob_hash}.json.gz" not in names:
                raise ConfigError(f"Bundle is missing blob {blob_hash} / 研究檔缺少內容 {blob_hash}")

        runs: list[dict[str, Any]] = []
        for entry in manifest.get("runs", []):
            run_key = str(entry["run_key"])
            if f"runs/{run_key}.json" not in names:
                raise ConfigError(f"Bundle is missing run {run_key} / 研究檔缺少研究紀錄 {run_key}")
            run = _read_json(archive, f"runs/{run_key}.json")
            missing = [key for key in _RUN_FIELDS + ("artifacts",) if key not in run]
            if missing:
                raise ConfigError(f"Run {run_key[:12]} is missing fields {missing} / 研究紀錄缺少欄位 {missing}")
            if run["run_key"] != run_key or run["status"] != "success":
                raise ConfigError(f"Run {run_key[:12]} is inconsistent / 研究紀錄內容不一致")
            expected = make_run_key(run["kind"], run["method_version"], run["config"],
                                    run["data_fingerprint"].get("scope_fingerprint", ""))
            if expected != run_key:
                raise ConfigError(f"Run {run_key[:12]} does not match its settings / 研究紀錄與其設定不符")
            needed = {run["result_hash"]} | {a["blob_hash"] for a in run["artifacts"]}
            if not needed <= declared:
                raise ConfigError(f"Run {run_key[:12]} refers to a blob not in the bundle / 研究紀錄引用了研究檔中不存在的內容")
            runs.append(run)

        studies: list[dict[str, Any]] = []
        for entry in manifest.get("studies", []):
            uid = str(entry["study_uid"])
            if f"studies/{uid}.json" not in names:
                raise ConfigError(f"Bundle is missing study {uid} / 研究檔缺少研究專案 {uid}")
            study = _read_json(archive, f"studies/{uid}.json")
            study["run_keys"] = [str(k) for k in entry.get("run_keys", [])]
            studies.append(study)

        # Verify and store every blob before touching the database.
        for blob_hash in sorted(declared):
            store.write_blob_from_gzip(blob_hash, archive.read(f"blobs/{blob_hash}.json.gz"))

    return store.import_records(bundle_uid=bundle_uid, studies=studies, runs=runs)
