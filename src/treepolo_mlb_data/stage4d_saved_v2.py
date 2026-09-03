from __future__ import annotations

import gzip
import hashlib
import json
import uuid
from pathlib import Path
from typing import Any

from . import stage4d as s4

SNAPSHOT_VERSION = "stage4d-frozen-result-v2"


def _row_value(row: Any, key: str, default: Any = None) -> Any:
    try:
        return row[key] if key in row.keys() else default
    except Exception:
        return default


def _snapshot_metadata(result: dict[str, Any], provenance: dict[str, Any], digest: str) -> dict[str, Any]:
    sections = s4._sections(result)
    return {
        "version": SNAPSHOT_VERSION,
        "sha256": digest,
        "section_count": len(sections),
        "row_count": sum(int(section.get("row_count") or len(section.get("rows") or [])) for section in sections),
        "sections": [
            {
                "index": index,
                "title": str(section.get("title") or f"Section {index + 1}"),
                "row_count": int(section.get("row_count") or len(section.get("rows") or [])),
                "columns": [str(value) for value in section.get("columns") or []],
                "grain": section.get("grain"),
            }
            for index, section in enumerate(sections)
        ],
        "data_revision": provenance.get("data_revision"),
        "backend": provenance.get("backend"),
    }


def install() -> None:
    if getattr(s4, "_saved_visualization_v2_installed", False):
        return
    s4._saved_visualization_v2_installed = True

    original_store_init = s4.PresentationStore.__init__
    original_resolve_source = s4.Stage4DService.resolve_source

    def ensure_schema(store: Any) -> None:
        with store.state._lock:
            columns = {str(row[1]) for row in store.state.conn.execute("PRAGMA table_info(visualizations)").fetchall()}
            if "snapshot_hash" not in columns:
                store.state.conn.execute("ALTER TABLE visualizations ADD COLUMN snapshot_hash TEXT")
            if "snapshot_meta_json" not in columns:
                store.state.conn.execute("ALTER TABLE visualizations ADD COLUMN snapshot_meta_json TEXT NOT NULL DEFAULT '{}'")
            store.state.conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS visualization_snapshots (
                    snapshot_hash TEXT PRIMARY KEY,
                    path TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_visualizations_snapshot_hash
                    ON visualizations(snapshot_hash);
                """
            )
            store.state.conn.commit()

    def store_init(self: Any, *args: Any, **kwargs: Any) -> None:
        original_store_init(self, *args, **kwargs)
        ensure_schema(self)

    def snapshot_path(store: Any, digest: str) -> Path:
        return store.snapshot_dir / f"snapshot-{digest}.json.gz"

    def write_snapshot(store: Any, frozen_data: dict[str, Any], provenance: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
        body = {"version": SNAPSHOT_VERSION, "result": frozen_data["result"]}
        encoded = s4.canonical_json(body).encode("utf-8")
        digest = hashlib.sha256(encoded).hexdigest()
        path = snapshot_path(store, digest)
        metadata = _snapshot_metadata(body["result"], provenance, digest)
        if not path.is_file():
            temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
            with gzip.open(temp, "wt", encoding="utf-8") as handle:
                json.dump(body, handle, ensure_ascii=False, separators=(",", ":"), allow_nan=True)
            temp.replace(path)
        with store.state._lock:
            store.state.conn.execute(
                "INSERT OR IGNORE INTO visualization_snapshots(snapshot_hash,path,metadata_json,created_at) VALUES(?,?,?,?)",
                (digest, str(path), s4.canonical_json(metadata), s4._now()),
            )
            store.state.conn.commit()
        return digest, str(path), metadata

    def release_snapshot(store: Any, digest: str | None) -> None:
        if not digest:
            return
        with store.state._lock:
            remaining = store.state.conn.execute(
                "SELECT COUNT(*) FROM visualizations WHERE snapshot_hash=?", (digest,)
            ).fetchone()[0]
            if remaining:
                return
            row = store.state.conn.execute(
                "SELECT path FROM visualization_snapshots WHERE snapshot_hash=?", (digest,)
            ).fetchone()
            store.state.conn.execute("DELETE FROM visualization_snapshots WHERE snapshot_hash=?", (digest,))
            store.state.conn.commit()
        if row and row[0]:
            try:
                Path(row[0]).unlink(missing_ok=True)
            except OSError:
                pass

    def visualization_row(self: Any, row: Any, *, include_frozen: bool) -> dict[str, Any]:
        snapshot_hash = _row_value(row, "snapshot_hash")
        snapshot_meta_raw = _row_value(row, "snapshot_meta_json", "{}") or "{}"
        item = {
            "id": int(row["id"]),
            "name": row["name"],
            "notes": row["notes"],
            "save_mode": row["save_mode"],
            "source": json.loads(row["source_json"]),
            "section_index": int(row["section_index"]),
            "spec": json.loads(row["spec_json"]),
            "provenance": json.loads(row["provenance_json"] or "{}"),
            "frozen_path": row["frozen_path"],
            "snapshot_hash": snapshot_hash,
            "snapshot_meta": json.loads(snapshot_meta_raw),
            "snapshot_version": SNAPSHOT_VERSION if snapshot_hash else ("legacy-section-v1" if row["save_mode"] == "frozen" else None),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
        if include_frozen and item["save_mode"] == "frozen":
            path: Path | None = None
            if snapshot_hash:
                with self.state._lock:
                    snapshot = self.state.conn.execute(
                        "SELECT path,metadata_json FROM visualization_snapshots WHERE snapshot_hash=?", (snapshot_hash,)
                    ).fetchone()
                if snapshot:
                    path = Path(snapshot[0])
                    item["snapshot_meta"] = json.loads(snapshot[1] or "{}")
            elif item["frozen_path"]:
                path = Path(item["frozen_path"])
            if path and path.is_file():
                with gzip.open(path, "rt", encoding="utf-8") as handle:
                    item["frozen_data"] = json.load(handle)
            else:
                item["frozen_data"] = None
                item["frozen_missing"] = True
        return item

    def save_store_visualization(
        self: Any,
        *,
        name: str,
        notes: str,
        save_mode: str,
        source: dict[str, Any],
        section_index: int,
        spec: dict[str, Any],
        provenance: dict[str, Any],
        frozen_data: dict[str, Any] | None = None,
        visualization_id: int | None = None,
    ) -> dict[str, Any]:
        clean_name = str(name).strip()
        if not clean_name:
            raise s4.RequestError("Visualization name is required")
        if save_mode not in {"live", "frozen"}:
            raise s4.RequestError("Visualization save mode must be live or frozen")

        now = s4._now()
        new_hash: str | None = None
        new_path: str | None = None
        snapshot_meta: dict[str, Any] = {}
        if save_mode == "frozen":
            if not isinstance(frozen_data, dict) or not isinstance(frozen_data.get("result"), dict):
                raise s4.RequestError("Frozen visualization requires a complete result snapshot")
            new_hash, new_path, snapshot_meta = write_snapshot(self, frozen_data, provenance)

        old_hash: str | None = None
        old_path: str | None = None
        with self.state._lock:
            if visualization_id is not None:
                old = self.state.conn.execute(
                    "SELECT snapshot_hash,frozen_path FROM visualizations WHERE id=?", (int(visualization_id),)
                ).fetchone()
                if old is None:
                    raise s4.RequestError("Saved visualization was not found")
                old_hash, old_path = old[0], old[1]
                self.state.conn.execute(
                    """
                    UPDATE visualizations SET name=?,notes=?,save_mode=?,source_json=?,section_index=?,
                    spec_json=?,provenance_json=?,frozen_path=?,snapshot_hash=?,snapshot_meta_json=?,updated_at=? WHERE id=?
                    """,
                    (
                        clean_name, str(notes or ""), save_mode, s4.canonical_json(source), int(section_index),
                        s4.canonical_json(spec), s4.canonical_json(provenance), new_path, new_hash,
                        s4.canonical_json(snapshot_meta), now, int(visualization_id),
                    ),
                )
                saved_id = int(visualization_id)
            else:
                cur = self.state.conn.execute(
                    """
                    INSERT INTO visualizations(name,notes,save_mode,source_json,section_index,spec_json,provenance_json,
                    frozen_path,created_at,updated_at,snapshot_hash,snapshot_meta_json)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        clean_name, str(notes or ""), save_mode, s4.canonical_json(source), int(section_index),
                        s4.canonical_json(spec), s4.canonical_json(provenance), new_path, now, now,
                        new_hash, s4.canonical_json(snapshot_meta),
                    ),
                )
                saved_id = int(cur.lastrowid)
            self.state.conn.commit()

        if old_hash and old_hash != new_hash:
            release_snapshot(self, old_hash)
        elif old_path and not old_hash and old_path != new_path:
            try:
                Path(old_path).unlink(missing_ok=True)
            except OSError:
                pass
        return self.get_visualization(saved_id, include_frozen=False)

    def delete_visualization(self: Any, visualization_id: int) -> bool:
        with self.state._lock:
            row = self.state.conn.execute(
                "SELECT snapshot_hash,frozen_path FROM visualizations WHERE id=?", (int(visualization_id),)
            ).fetchone()
            if row is None:
                return False
            digest, legacy_path = row[0], row[1]
            self.state.conn.execute("DELETE FROM visualizations WHERE id=?", (int(visualization_id),))
            self.state.conn.commit()
        if digest:
            release_snapshot(self, digest)
        elif legacy_path:
            try:
                Path(legacy_path).unlink(missing_ok=True)
            except OSError:
                pass
        return True

    def resolve_source(
        self: Any,
        source: dict[str, Any],
        *,
        allow_rerun: bool = False,
        client_result: dict[str, Any] | None = None,
        _depth: int = 0,
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, Any]]:
        kind = str(source.get("kind") or "") if isinstance(source, dict) else ""
        if kind in {"visualization", "frozen_visualization"}:
            if _depth > 4:
                raise s4.RequestError("Visualization source chain is too deep")
            item = self.store.get_visualization(int(source.get("id")), include_frozen=True)
            if item is None:
                raise s4.RequestError("Saved visualization was not found")
            visualization_provenance = dict(item.get("provenance") or {})
            visualization_provenance.update({
                "visualization_id": item["id"],
                "visualization_name": item["name"],
                "save_mode": item["save_mode"],
                "snapshot_hash": item.get("snapshot_hash"),
                "snapshot_version": item.get("snapshot_version"),
            })
            if item["save_mode"] == "frozen":
                frozen = item.get("frozen_data")
                if not isinstance(frozen, dict):
                    raise s4.RequestError("Frozen visualization snapshot is unavailable")
                if frozen.get("version") == SNAPSHOT_VERSION and isinstance(frozen.get("result"), dict):
                    return None, frozen["result"], visualization_provenance | {"frozen": True, "legacy_frozen": False}
                if isinstance(frozen.get("section"), dict):
                    return None, {"sections": [frozen["section"]]}, visualization_provenance | {"frozen": True, "legacy_frozen": True}
                raise s4.RequestError("Frozen visualization snapshot format is unsupported")
            payload, result, underlying = self.resolve_source(
                item["source"], allow_rerun=allow_rerun, client_result=client_result, _depth=_depth + 1
            )
            return payload, result, underlying | visualization_provenance
        return original_resolve_source(
            self, source, allow_rerun=allow_rerun, client_result=client_result, _depth=_depth
        )

    def save_service_visualization(self: Any, request: dict[str, Any], visualization_id: int | None = None) -> dict[str, Any]:
        source = request.get("source")
        spec = request.get("spec")
        if not isinstance(source, dict) or not isinstance(spec, dict):
            raise s4.RequestError("source and spec are required")
        spec = s4.normalize_spec(spec)
        save_mode = str(request.get("save_mode") or "live")
        section_index = int(request.get("section", 0))

        if visualization_id is not None and source.get("kind") == "visualization" and int(source.get("id") or -1) == int(visualization_id):
            existing = self.store.get_visualization(int(visualization_id), include_frozen=False)
            if existing is not None:
                source = existing["source"]

        frozen_data = None
        provenance: dict[str, Any] = {}
        if save_mode == "frozen":
            _, result, provenance = self.resolve_source(source, allow_rerun=True)
            if result is None:
                raise s4.RequestError("Frozen visualization source result is unavailable")
            if self._result_row_count(result) > s4.MAX_EXPORT_ROWS:
                raise s4.RequestError(
                    f"Frozen visualization exceeds the {s4.MAX_EXPORT_ROWS:,}-row safety limit; narrow the analysis first"
                )
            frozen_data = {"version": SNAPSHOT_VERSION, "result": s4._copy_json(result)}
        else:
            _, _, provenance = self.resolve_source(source, allow_rerun=False)

        return self.store.save_visualization(
            name=str(request.get("name") or ""),
            notes=str(request.get("notes") or ""),
            save_mode=save_mode,
            source=source,
            section_index=section_index,
            spec=spec,
            provenance=provenance,
            frozen_data=frozen_data,
            visualization_id=visualization_id,
        )

    s4.PresentationStore.__init__ = store_init
    s4.PresentationStore._visualization_row = visualization_row
    s4.PresentationStore.save_visualization = save_store_visualization
    s4.PresentationStore.delete_visualization = delete_visualization
    s4.Stage4DService.resolve_source = resolve_source
    s4.Stage4DService.save_visualization = save_service_visualization
