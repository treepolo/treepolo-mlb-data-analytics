from __future__ import annotations

import csv
import gzip
import html
import io
import json
import math
import mimetypes
import random
import re
import tempfile
import threading
import uuid
import zipfile
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from xml.sax.saxutils import escape as xml_escape

from ._lazy_duckdb import duckdb
from .analysis_state import canonical_json, read_data_revision
from .web_analysis import RequestError

PRESENTATION_SPEC_VERSION = "stage4d-v1"
AUTO_SAMPLE_ROWS = 5_000
MAX_MANUAL_SAMPLE_ROWS = 50_000
MAX_FULL_VISUALIZATION_ROWS = 100_000
MAX_EXPORT_ROWS = 500_000
REPORT_TABLE_ROWS = 80


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _copy_json(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=True))


def _scalar(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return canonical_json(value)


def _sections(result: dict[str, Any]) -> list[dict[str, Any]]:
    raw = result.get("sections")
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    return [result]


def _section_complete(section: dict[str, Any]) -> bool:
    rows = section.get("rows")
    if not isinstance(rows, list):
        return False
    total = section.get("row_count")
    if isinstance(total, int):
        return len(rows) >= total
    return True


def _result_complete(result: dict[str, Any]) -> bool:
    return all(_section_complete(section) for section in _sections(result))


def _selected_section(result: dict[str, Any], selector: Any = 0) -> tuple[int, dict[str, Any]]:
    sections = _sections(result)
    if not sections:
        raise RequestError("Analysis result has no result section")
    if isinstance(selector, str) and not selector.strip().isdigit():
        for index, section in enumerate(sections):
            if str(section.get("title") or "") == selector:
                return index, section
        raise RequestError(f"Unknown result section: {selector}")
    try:
        index = int(selector or 0)
    except (TypeError, ValueError) as exc:
        raise RequestError("Invalid result section") from exc
    if index < 0 or index >= len(sections):
        raise RequestError("Result section is out of range")
    return index, sections[index]


KNOWN_FIELDS: dict[str, dict[str, Any]] = {
    "pitch_uid": {"label": "逐球識別碼 Pitch ID", "role": "identifier"},
    "game_pk": {"label": "比賽識別碼 Game ID", "role": "identifier"},
    "play_id": {"label": "Play ID", "role": "identifier"},
    "pitcher": {"label": "投手 Pitcher", "role": "identifier"},
    "batter": {"label": "打者 Batter", "role": "identifier"},
    "game_date": {"label": "比賽日期 Game Date", "role": "temporal_dimension"},
    "game_year": {"label": "球季 Season", "role": "temporal_dimension"},
    "pitch_type": {"label": "球種 Pitch Type", "role": "category"},
    "release_speed": {"label": "出手球速 Release Speed", "unit": "mph", "role": "measure"},
    "release_spin_rate": {"label": "旋轉速率 Spin Rate", "unit": "rpm", "role": "measure"},
    "spin_axis": {"label": "旋轉軸 Spin Axis", "unit": "deg", "role": "circular_measure"},
    "pfx_x": {"label": "水平位移 Horizontal Movement", "unit": "ft", "role": "measure"},
    "pfx_z": {"label": "垂直位移 Vertical Movement", "unit": "ft", "role": "measure"},
    "release_pos_x": {"label": "出手點水平 Release X", "unit": "ft", "role": "measure"},
    "release_pos_z": {"label": "出手點高度 Release Z", "unit": "ft", "role": "measure"},
    "plate_x": {"label": "本壘板水平位置 Plate X", "unit": "ft", "role": "measure"},
    "plate_z": {"label": "本壘板高度 Plate Z", "unit": "ft", "role": "measure"},
    "sz_top": {"label": "好球帶上緣 Strike Zone Top", "unit": "ft", "role": "measure"},
    "sz_bot": {"label": "好球帶下緣 Strike Zone Bottom", "unit": "ft", "role": "measure"},
    "launch_speed": {"label": "擊球初速 Exit Velocity", "unit": "mph", "role": "measure"},
    "launch_angle": {"label": "擊球仰角 Launch Angle", "unit": "deg", "role": "measure"},
    "usage_rate": {"label": "使用率 Usage Rate", "unit": "ratio", "role": "percentage"},
    "percentile": {"label": "百分位 Percentile", "unit": "ratio", "role": "percentage"},
    "cluster": {"label": "分群 Cluster", "role": "category"},
    "cluster_probability": {"label": "分群機率 Cluster Probability", "unit": "ratio", "role": "percentage"},
    "candidate_k": {"label": "候選群數 Candidate K", "role": "ordered_dimension"},
    "score": {"label": "選模分數 Criterion Score", "role": "measure"},
    "sample_size": {"label": "樣本數 Sample Size", "role": "sample_size"},
    "row_count": {"label": "資料筆數 Row Count", "role": "sample_size"},
    "n_pitches": {"label": "球數 Pitch Count", "role": "sample_size"},
    "coefficient": {"label": "迴歸係數 Coefficient", "role": "estimate"},
    "estimate": {"label": "估計值 Estimate", "role": "estimate"},
    "ci_low": {"label": "信賴區間下界 CI Low", "role": "interval_lower", "paired_with": "estimate"},
    "ci_high": {"label": "信賴區間上界 CI High", "role": "interval_upper", "paired_with": "estimate"},
    "current_value": {"label": "目前數值 Current Value", "role": "estimate"},
    "reference_value": {"label": "參考數值 Reference Value", "role": "baseline"},
    "unit_value": {"label": "分析單位數值 Unit Value", "role": "estimate"},
    "baseline_value": {"label": "基準數值 Baseline Value", "role": "baseline"},
    "difference": {"label": "差值 Difference", "role": "difference"},
}


def _infer_value_type(values: list[Any]) -> str:
    non_null = [value for value in values if value is not None]
    if not non_null:
        return "unknown"
    if all(isinstance(value, bool) for value in non_null):
        return "boolean"
    if all(isinstance(value, int) and not isinstance(value, bool) for value in non_null):
        return "integer"
    if all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in non_null):
        return "number"
    return "string"


def field_metadata(section: dict[str, Any]) -> list[dict[str, Any]]:
    columns = [str(value) for value in section.get("columns") or []]
    rows = section.get("rows") if isinstance(section.get("rows"), list) else []
    output: list[dict[str, Any]] = []
    for column in columns:
        known = dict(KNOWN_FIELDS.get(column, {}))
        values = [row.get(column) for row in rows[:200] if isinstance(row, dict)]
        value_type = _infer_value_type(values)
        lower = column.lower()
        role = known.get("role")
        if role is None:
            if lower.endswith("_id") or lower in {"id", "at_bat_number", "pitch_number"}:
                role = "identifier"
            elif "date" in lower or lower in {"year", "season", "period"}:
                role = "temporal_dimension"
            elif lower in {"n", "count"} or lower.endswith("_count") or lower.endswith("_size"):
                role = "sample_size"
            elif lower.startswith("ci_low") or lower.endswith("_low") or "lower" in lower:
                role = "interval_lower"
            elif lower.startswith("ci_high") or lower.endswith("_high") or "upper" in lower:
                role = "interval_upper"
            elif "percent" in lower or "rate" in lower or "probability" in lower or lower.endswith("_pct"):
                role = "percentage"
            elif value_type in {"integer", "number"}:
                role = "measure"
            else:
                role = "category"
        unit = known.get("unit")
        if unit is None and role == "percentage":
            unit = "ratio"
        output.append({
            "name": column,
            "label": known.get("label", column),
            "type": value_type,
            "role": role,
            "unit": unit,
            "paired_with": known.get("paired_with"),
            "is_numeric": value_type in {"integer", "number"},
            "is_identifier": role == "identifier",
            "is_temporal": role == "temporal_dimension",
            "is_categorical": role in {"identifier", "category"},
        })
    return output


BUILTIN_PRESETS: list[dict[str, Any]] = [
    {"id": "pitch_movement", "name": "球路位移 Pitch Movement", "type": "scatter", "required_fields": ["pfx_x", "pfx_z"], "mapping": {"x": "pfx_x", "y": "pfx_z", "series": "pitch_type"}, "display": {"equal_axes": True, "reference_x": 0, "reference_y": 0}},
    {"id": "pitch_location", "name": "進壘位置 Pitch Location", "type": "scatter", "required_fields": ["plate_x", "plate_z"], "mapping": {"x": "plate_x", "y": "plate_z", "series": "pitch_type"}, "display": {"equal_axes": True, "baseball_overlay": "strike_zone"}},
    {"id": "release_point", "name": "出手點 Release Point", "type": "scatter", "required_fields": ["release_pos_x", "release_pos_z"], "mapping": {"x": "release_pos_x", "y": "release_pos_z", "series": "pitch_type"}, "display": {"equal_axes": True}},
    {"id": "usage_trend", "name": "球種使用率趨勢 Pitch Usage Trend", "type": "line", "required_fields": ["usage_rate"], "mapping": {"y": "usage_rate", "series": "pitch_type"}, "display": {}},
    {"id": "cluster_map", "name": "分群圖 Cluster Map", "type": "scatter", "required_fields": ["cluster"], "mapping": {"series": "cluster"}, "display": {}},
    {"id": "auto_k", "name": "Auto-K 診斷 Auto-K Diagnostics", "type": "line", "required_fields": ["candidate_k", "score"], "mapping": {"x": "candidate_k", "y": "score"}, "display": {"highlight_field": "selected"}},
    {"id": "regression_coefficients", "name": "迴歸係數 Regression Coefficients", "type": "range", "required_fields": ["coefficient"], "mapping": {"y": "coefficient", "lower": "ci_low", "upper": "ci_high"}, "display": {"reference_y": 0}},
    {"id": "confidence_interval", "name": "信賴區間 Confidence Interval", "type": "range", "required_fields": ["estimate", "ci_low", "ci_high"], "mapping": {"y": "estimate", "lower": "ci_low", "upper": "ci_high"}, "display": {}},
    {"id": "cross_level", "name": "層級比較 Cross-Level Comparison", "type": "dumbbell", "required_fields": ["unit_value", "baseline_value"], "mapping": {"y": "unit_value", "lower": "baseline_value"}, "display": {}},
    {"id": "difference", "name": "差值排名 Difference Ranking", "type": "difference", "required_fields": ["difference"], "mapping": {"y": "difference"}, "display": {"reference_y": 0}},
    {"id": "generic_time", "name": "時間趨勢 Generic Time Trend", "type": "line", "required_fields": [], "mapping": {}, "display": {}},
    {"id": "category_comparison", "name": "類別比較 Category Comparison", "type": "bar", "required_fields": [], "mapping": {}, "display": {}},
]


def recommended_presentations(section: dict[str, Any], mode: str | None = None) -> list[str]:
    columns = set(str(value) for value in section.get("columns") or [])
    metadata = field_metadata(section)
    numeric = [item["name"] for item in metadata if item["is_numeric"] and not item["is_identifier"]]
    temporal = [item["name"] for item in metadata if item["is_temporal"]]
    categorical = [item["name"] for item in metadata if item["role"] == "category"]
    recommendations: list[str] = []
    for preset in BUILTIN_PRESETS:
        required = set(preset.get("required_fields") or [])
        if required and required.issubset(columns):
            recommendations.append(str(preset["id"]))
    if mode == "clustering" and "cluster" in columns and "cluster_map" not in recommendations:
        recommendations.append("cluster_map")
    if temporal and numeric:
        recommendations.append("generic_time")
    if categorical and numeric:
        recommendations.append("category_comparison")
    if len(numeric) >= 2:
        recommendations.append("scatter")
    seen: set[str] = set()
    return [item for item in recommendations if not (item in seen or seen.add(item))]


def _sampling_spec(value: Any) -> dict[str, Any]:
    value = value if isinstance(value, dict) else {}
    mode = str(value.get("mode") or "automatic")
    if mode not in {"full", "automatic", "manual"}:
        raise RequestError("Sampling mode must be full, automatic, or manual")
    method = str(value.get("method") or "random")
    if method not in {"random", "every_nth"}:
        raise RequestError("Sampling method must be random or every_nth")
    default_size = AUTO_SAMPLE_ROWS if mode == "automatic" else min(MAX_MANUAL_SAMPLE_ROWS, int(value.get("size") or AUTO_SAMPLE_ROWS))
    size = max(1, min(int(value.get("size") or default_size), MAX_MANUAL_SAMPLE_ROWS))
    seed = int(value.get("seed") or 42)
    return {"mode": mode, "method": method, "size": size, "seed": seed}


def sample_rows(rows: list[dict[str, Any]], spec: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    resolved = _sampling_spec(spec)
    total = len(rows)
    mode = resolved["mode"]
    if mode == "full":
        if total > MAX_FULL_VISUALIZATION_ROWS:
            raise RequestError(
                f"Full visualization contains {total:,} rows; use Automatic or Manual Sampling (limit {MAX_FULL_VISUALIZATION_ROWS:,})"
            )
        return rows, {**resolved, "sampled": False, "source_rows": total, "returned_rows": total}
    target = min(total, resolved["size"] if mode == "manual" else AUTO_SAMPLE_ROWS)
    if total <= target:
        return rows, {**resolved, "sampled": False, "source_rows": total, "returned_rows": total}
    if resolved["method"] == "every_nth":
        step = total / target
        indices = [min(total - 1, int(index * step)) for index in range(target)]
    else:
        rng = random.Random(resolved["seed"])
        indices = sorted(rng.sample(range(total), target))
    sampled = [rows[index] for index in indices]
    return sampled, {**resolved, "sampled": True, "source_rows": total, "returned_rows": len(sampled)}


class PresentationStore:
    def __init__(self, analysis_state: Any, root: Path):
        self.state = analysis_state
        self.root = Path(root)
        self.snapshot_dir = self.root / "data" / "visualization_snapshots"
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _init_schema(self) -> None:
        with self.state._lock:
            self.state.conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS visualizations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    notes TEXT NOT NULL DEFAULT '',
                    save_mode TEXT NOT NULL,
                    source_json TEXT NOT NULL,
                    section_index INTEGER NOT NULL DEFAULT 0,
                    spec_json TEXT NOT NULL,
                    provenance_json TEXT NOT NULL DEFAULT '{}',
                    frozen_path TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_visualizations_updated
                    ON visualizations(updated_at DESC, id DESC);
                CREATE TABLE IF NOT EXISTS visualization_presets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    spec_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_visualization_presets_updated
                    ON visualization_presets(updated_at DESC, id DESC);
                """
            )
            self.state.conn.commit()

    def list_visualizations(self) -> list[dict[str, Any]]:
        with self.state._lock:
            rows = self.state.conn.execute(
                "SELECT * FROM visualizations ORDER BY updated_at DESC,id DESC"
            ).fetchall()
        return [self._visualization_row(row, include_frozen=False) for row in rows]

    def get_visualization(self, visualization_id: int, *, include_frozen: bool = True) -> dict[str, Any] | None:
        with self.state._lock:
            row = self.state.conn.execute(
                "SELECT * FROM visualizations WHERE id=?", (int(visualization_id),)
            ).fetchone()
        if row is None:
            return None
        return self._visualization_row(row, include_frozen=include_frozen)

    def _visualization_row(self, row: Any, *, include_frozen: bool) -> dict[str, Any]:
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
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
        if include_frozen and item["save_mode"] == "frozen" and item["frozen_path"]:
            path = Path(item["frozen_path"])
            if path.is_file():
                with gzip.open(path, "rt", encoding="utf-8") as handle:
                    item["frozen_data"] = json.load(handle)
            else:
                item["frozen_data"] = None
                item["frozen_missing"] = True
        return item

    def save_visualization(
        self,
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
            raise RequestError("Visualization name is required")
        if save_mode not in {"live", "frozen"}:
            raise RequestError("Visualization save mode must be live or frozen")
        now = _now()
        frozen_path: str | None = None
        if save_mode == "frozen":
            if frozen_data is None:
                raise RequestError("Frozen visualization requires prepared result data")
            token = uuid.uuid4().hex
            path = self.snapshot_dir / f"visualization-{token}.json.gz"
            with gzip.open(path, "wt", encoding="utf-8") as handle:
                json.dump(frozen_data, handle, ensure_ascii=False, separators=(",", ":"))
            frozen_path = str(path)
        with self.state._lock:
            old_path: str | None = None
            if visualization_id is not None:
                old = self.state.conn.execute("SELECT frozen_path FROM visualizations WHERE id=?", (int(visualization_id),)).fetchone()
                if old is None:
                    raise RequestError("Saved visualization was not found")
                old_path = old[0]
                self.state.conn.execute(
                    """
                    UPDATE visualizations SET name=?,notes=?,save_mode=?,source_json=?,section_index=?,
                    spec_json=?,provenance_json=?,frozen_path=?,updated_at=? WHERE id=?
                    """,
                    (clean_name, str(notes or ""), save_mode, canonical_json(source), int(section_index), canonical_json(spec), canonical_json(provenance), frozen_path, now, int(visualization_id)),
                )
                saved_id = int(visualization_id)
            else:
                cur = self.state.conn.execute(
                    """
                    INSERT INTO visualizations(name,notes,save_mode,source_json,section_index,spec_json,provenance_json,frozen_path,created_at,updated_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?)
                    """,
                    (clean_name, str(notes or ""), save_mode, canonical_json(source), int(section_index), canonical_json(spec), canonical_json(provenance), frozen_path, now, now),
                )
                saved_id = int(cur.lastrowid)
            self.state.conn.commit()
        if old_path and old_path != frozen_path:
            try:
                Path(old_path).unlink(missing_ok=True)
            except OSError:
                pass
        return self.get_visualization(saved_id, include_frozen=False)  # type: ignore[return-value]

    def delete_visualization(self, visualization_id: int) -> bool:
        with self.state._lock:
            row = self.state.conn.execute("SELECT frozen_path FROM visualizations WHERE id=?", (int(visualization_id),)).fetchone()
            if row is None:
                return False
            self.state.conn.execute("DELETE FROM visualizations WHERE id=?", (int(visualization_id),))
            self.state.conn.commit()
        if row[0]:
            try:
                Path(row[0]).unlink(missing_ok=True)
            except OSError:
                pass
        return True

    def list_presets(self) -> list[dict[str, Any]]:
        with self.state._lock:
            rows = self.state.conn.execute(
                "SELECT * FROM visualization_presets ORDER BY updated_at DESC,id DESC"
            ).fetchall()
        return [
            {"id": int(row["id"]), "name": row["name"], "spec": json.loads(row["spec_json"]), "created_at": row["created_at"], "updated_at": row["updated_at"]}
            for row in rows
        ]

    def save_preset(self, name: str, spec: dict[str, Any]) -> dict[str, Any]:
        clean_name = str(name).strip()
        if not clean_name:
            raise RequestError("Preset name is required")
        now = _now()
        with self.state._lock:
            cur = self.state.conn.execute(
                "INSERT INTO visualization_presets(name,spec_json,created_at,updated_at) VALUES(?,?,?,?)",
                (clean_name, canonical_json(spec), now, now),
            )
            self.state.conn.commit()
            preset_id = int(cur.lastrowid)
        return next(item for item in self.list_presets() if item["id"] == preset_id)

    def delete_preset(self, preset_id: int) -> bool:
        with self.state._lock:
            cur = self.state.conn.execute("DELETE FROM visualization_presets WHERE id=?", (int(preset_id),))
            self.state.conn.commit()
            return cur.rowcount > 0


class Stage4DService:
    def __init__(self, app_services: Any):
        self.app = app_services
        self.store = PresentationStore(app_services.analysis_state, app_services.config.root)
        self.recent: deque[dict[str, Any]] = deque(maxlen=12)
        self._lock = threading.RLock()

    def record_recent(self, payload: dict[str, Any], result: dict[str, Any]) -> None:
        item = {
            "token": uuid.uuid4().hex,
            "created_at": _now(),
            "mode": str(payload.get("mode") or "basic"),
            "payload": _copy_json(payload),
            "result": _copy_json(result),
            "history_id": result.get("history_id"),
            "cache_key": (result.get("cache") or {}).get("key") if isinstance(result.get("cache"), dict) else None,
            "data_revision": (result.get("cache") or {}).get("data_revision") if isinstance(result.get("cache"), dict) else read_data_revision(self.app.config.database_path),
        }
        with self._lock:
            self.recent.appendleft(item)

    def asset_status(self) -> dict[str, Any]:
        repo_root = Path(__file__).resolve().parents[2]
        base = repo_root / "research_assets" / "3d_baseball"
        manifest = base / "upstream_manifest.json"
        upstream = base / "upstream" / "app.json"
        return {
            "policy": "project-research-asset-only",
            "manifest": str(manifest),
            "manifest_available": manifest.is_file(),
            "local_app_json": str(upstream),
            "local_asset_available": upstream.is_file(),
            "fetch_helper": str(base / "fetch_upstream.py"),
            "external_search_allowed": False,
        }

    def sources(self) -> dict[str, Any]:
        with self._lock:
            recent = [
                {key: item.get(key) for key in ("token", "created_at", "mode", "history_id", "data_revision")}
                | {"row_count": self._result_row_count(item["result"])}
                for item in self.recent
            ]
        return {
            "recent": recent,
            "history": self.app.analysis_state.list_history(100),
            "saved": self.app.analysis_state.list_saved(),
            "visualizations": self.store.list_visualizations(),
            "presets": {"built_in": BUILTIN_PRESETS, "user": self.store.list_presets()},
            "baseball_asset": self.asset_status(),
        }

    @staticmethod
    def _result_row_count(result: dict[str, Any]) -> int:
        if isinstance(result.get("row_count"), int):
            return int(result["row_count"])
        return sum(int(section.get("row_count") or len(section.get("rows") or [])) for section in _sections(result))

    def _recent_item(self, token: str) -> dict[str, Any] | None:
        with self._lock:
            for item in self.recent:
                if item.get("token") == token:
                    return item
        return None

    def _rerun_full(self, payload: dict[str, Any]) -> dict[str, Any]:
        full_payload = _copy_json(payload)
        full_payload.pop("result_limit", None)
        result = self.app.analysis.analyze(full_payload)
        if self._result_row_count(result) > MAX_EXPORT_ROWS:
            raise RequestError(
                f"Full result contains more than {MAX_EXPORT_ROWS:,} rows; narrow the analysis before full visualization/export"
            )
        return result

    def resolve_source(
        self,
        source: dict[str, Any],
        *,
        allow_rerun: bool = False,
        client_result: dict[str, Any] | None = None,
        _depth: int = 0,
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, Any]]:
        if _depth > 4:
            raise RequestError("Visualization source chain is too deep")
        if not isinstance(source, dict):
            raise RequestError("Visualization source must be an object")
        kind = str(source.get("kind") or "")
        payload: dict[str, Any] | None = None
        result: dict[str, Any] | None = None
        provenance: dict[str, Any] = {"source_kind": kind}
        if kind == "recent":
            item = self._recent_item(str(source.get("id") or source.get("token") or ""))
            if item is None:
                raise RequestError("Recent analysis result is no longer available in this session")
            payload = item["payload"]
            result = item["result"]
            provenance.update({"history_id": item.get("history_id"), "data_revision": item.get("data_revision"), "created_at": item.get("created_at"), "mode": item.get("mode")})
        elif kind == "history":
            item = self.app.analysis_state.get_history(int(source.get("id")))
            if item is None:
                raise RequestError("Analysis history item was not found")
            payload = item.get("payload")
            result = item.get("result")
            provenance.update({"history_id": item.get("id"), "data_revision": item.get("data_revision"), "backend": item.get("backend"), "created_at": item.get("created_at"), "mode": item.get("mode")})
        elif kind == "saved":
            item = self.app.analysis_state.get_saved(int(source.get("id")))
            if item is None:
                raise RequestError("Saved analysis was not found")
            payload = item.get("payload")
            result = item.get("result")
            provenance.update({"saved_id": item.get("id"), "data_revision": item.get("data_revision"), "created_at": item.get("created_at"), "mode": (payload or {}).get("mode"), "label": item.get("name")})
        elif kind == "analysis_payload":
            raw = source.get("payload")
            if not isinstance(raw, dict):
                raise RequestError("analysis_payload source requires payload")
            payload = raw
            result = client_result if isinstance(client_result, dict) else None
            provenance.update({"mode": payload.get("mode"), "data_revision": read_data_revision(self.app.config.database_path)})
        elif kind in {"visualization", "frozen_visualization"}:
            item = self.store.get_visualization(int(source.get("id")), include_frozen=True)
            if item is None:
                raise RequestError("Saved visualization was not found")
            provenance.update(item.get("provenance") or {})
            provenance.update({"visualization_id": item["id"], "visualization_name": item["name"], "save_mode": item["save_mode"]})
            if item["save_mode"] == "frozen":
                frozen = item.get("frozen_data")
                if not isinstance(frozen, dict):
                    raise RequestError("Frozen visualization snapshot is unavailable")
                result = {"sections": [frozen["section"]]}
                return None, result, provenance | {"frozen": True}
            return self.resolve_source(item["source"], allow_rerun=allow_rerun, _depth=_depth + 1)
        else:
            raise RequestError(f"Unsupported visualization source: {kind}")

        if allow_rerun and payload is not None and (result is None or not _result_complete(result)):
            result = self._rerun_full(payload)
            provenance["rerun"] = True
            provenance["data_revision"] = read_data_revision(self.app.config.database_path)
        return payload, result, provenance

    def describe(self, payload: dict[str, Any]) -> dict[str, Any]:
        section = payload.get("section")
        if not isinstance(section, dict):
            raise RequestError("section must be an object")
        mode = payload.get("mode")
        return {
            "field_metadata": field_metadata(section),
            "recommendations": recommended_presentations(section, str(mode) if mode else None),
        }

    def prepare_data(self, request: dict[str, Any]) -> dict[str, Any]:
        source = request.get("source")
        if not isinstance(source, dict):
            raise RequestError("source is required")
        allow_rerun = bool(request.get("allow_rerun", False))
        payload, result, provenance = self.resolve_source(
            source,
            allow_rerun=allow_rerun,
            client_result=request.get("client_result") if isinstance(request.get("client_result"), dict) else None,
        )
        if result is None:
            return {"result_available": False, "requires_rerun": payload is not None, "provenance": provenance}
        index, section = _selected_section(result, request.get("section", 0))
        complete = _section_complete(section)
        rows = [row for row in (section.get("rows") or []) if isinstance(row, dict)]
        sampling = _sampling_spec(request.get("sampling"))
        if not complete and not allow_rerun:
            prepared_rows = rows
            sampling_info = {**sampling, "sampled": False, "preview_only": True, "source_rows": int(section.get("row_count") or len(rows)), "returned_rows": len(rows)}
        else:
            prepared_rows, sampling_info = sample_rows(rows, sampling)
        prepared_section = dict(section)
        prepared_section["rows"] = prepared_rows
        prepared_section["returned_row_count"] = len(prepared_rows)
        return {
            "result_available": True,
            "requires_rerun": not complete and not allow_rerun,
            "section_index": index,
            "sections": [{"index": i, "title": str(item.get("title") or f"Section {i + 1}"), "row_count": int(item.get("row_count") or len(item.get("rows") or []))} for i, item in enumerate(_sections(result))],
            "section": prepared_section,
            "field_metadata": field_metadata(prepared_section),
            "recommendations": recommended_presentations(prepared_section, str((payload or {}).get("mode") or "")),
            "sampling": sampling_info,
            "provenance": provenance | {
                "grain": section.get("grain"),
                "backend": section.get("backend") or result.get("backend"),
                "row_count": int(section.get("row_count") or len(rows)),
                "returned_row_count": len(rows),
                "section_title": section.get("title"),
                "complete_result": complete or allow_rerun,
            },
            "analysis_payload": payload,
        }

    def save_visualization(self, request: dict[str, Any], visualization_id: int | None = None) -> dict[str, Any]:
        source = request.get("source")
        spec = request.get("spec")
        if not isinstance(source, dict) or not isinstance(spec, dict):
            raise RequestError("source and spec are required")
        spec = normalize_spec(spec)
        save_mode = str(request.get("save_mode") or "live")
        section_index = int(request.get("section", 0))
        frozen_data = None
        provenance: dict[str, Any] = {}
        if save_mode == "frozen":
            prepared = self.prepare_data({
                "source": source,
                "section": section_index,
                "sampling": spec.get("sampling") or {"mode": "automatic"},
                "allow_rerun": True,
            })
            frozen_data = {"section": prepared["section"], "sampling": prepared["sampling"], "provenance": prepared["provenance"], "field_metadata": prepared["field_metadata"]}
            provenance = prepared["provenance"]
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

    def export(self, request: dict[str, Any]) -> tuple[bytes, str, str]:
        format_name = str(request.get("format") or "csv").lower()
        if format_name not in {"csv", "json", "xlsx", "parquet"}:
            raise RequestError("Export format must be CSV, JSON, XLSX, or Parquet")
        source = request.get("source")
        if not isinstance(source, dict):
            raise RequestError("source is required")
        _, result, provenance = self.resolve_source(source, allow_rerun=True)
        if result is None:
            raise RequestError("Analysis result is unavailable")
        index, section = _selected_section(result, request.get("section", 0))
        rows = [row for row in (section.get("rows") or []) if isinstance(row, dict)]
        if len(rows) > MAX_EXPORT_ROWS:
            raise RequestError(f"Export exceeds the {MAX_EXPORT_ROWS:,}-row safety limit")
        metadata = provenance | {"section_index": index, "section_title": section.get("title"), "row_count": int(section.get("row_count") or len(rows)), "exported_at": _now()}
        stem = _safe_filename(str(request.get("name") or section.get("title") or "analysis-result"))
        if format_name == "csv":
            return _csv_bytes(section), "text/csv; charset=utf-8", f"{stem}.csv"
        if format_name == "json":
            body = json.dumps({"metadata": metadata, "section": section}, ensure_ascii=False, indent=2, allow_nan=True).encode("utf-8")
            return body, "application/json; charset=utf-8", f"{stem}.json"
        if format_name == "xlsx":
            return _xlsx_bytes(section, metadata), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", f"{stem}.xlsx"
        return _parquet_bytes(section), "application/vnd.apache.parquet", f"{stem}.parquet"

    def report(self, request: dict[str, Any]) -> tuple[bytes, str, str]:
        format_name = str(request.get("format") or "html").lower()
        if format_name not in {"html", "pdf"}:
            raise RequestError("Report format must be HTML or PDF")
        source = request.get("source")
        spec = normalize_spec(request.get("spec") if isinstance(request.get("spec"), dict) else {})
        if not isinstance(source, dict):
            raise RequestError("source is required")
        prepared = self.prepare_data({
            "source": source,
            "section": request.get("section", 0),
            "sampling": spec.get("sampling") or {"mode": "automatic"},
            "allow_rerun": True,
        })
        section = prepared["section"]
        title = str(request.get("name") or spec.get("display", {}).get("title") or section.get("title") or "Analysis Report")
        stem = _safe_filename(title)
        if format_name == "html":
            return _html_report(title, prepared, spec, str(request.get("chart_svg") or "")), "text/html; charset=utf-8", f"{stem}.html"
        return _pdf_report(title, prepared, spec), "application/pdf", f"{stem}.pdf"


def normalize_spec(spec: dict[str, Any]) -> dict[str, Any]:
    chart_type = str(spec.get("type") or "scatter")
    if chart_type not in {"line", "bar", "scatter", "range", "dumbbell", "difference"}:
        raise RequestError("Unsupported presentation type")
    mapping = spec.get("mapping") if isinstance(spec.get("mapping"), dict) else {}
    display = spec.get("display") if isinstance(spec.get("display"), dict) else {}
    sampling = _sampling_spec(spec.get("sampling"))
    return {
        "version": PRESENTATION_SPEC_VERSION,
        "type": chart_type,
        "preset": spec.get("preset"),
        "mapping": {key: value for key, value in mapping.items() if value not in (None, "")},
        "display": display,
        "sampling": sampling,
    }


def _safe_filename(value: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-._")
    return clean[:100] or "analysis-result"


def _csv_bytes(section: dict[str, Any]) -> bytes:
    columns = [str(value) for value in section.get("columns") or []]
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(columns)
    for row in section.get("rows") or []:
        writer.writerow([_scalar(row.get(column)) if isinstance(row, dict) else None for column in columns])
    return ("\ufeff" + stream.getvalue()).encode("utf-8")


def _excel_col(index: int) -> str:
    value = index + 1
    output = ""
    while value:
        value, remainder = divmod(value - 1, 26)
        output = chr(65 + remainder) + output
    return output


def _xlsx_cell(ref: str, value: Any) -> str:
    value = _scalar(value)
    if value is None:
        return f'<c r="{ref}"/>'
    if isinstance(value, bool):
        return f'<c r="{ref}" t="b"><v>{1 if value else 0}</v></c>'
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return f'<c r="{ref}"><v>{value}</v></c>'
    text = xml_escape(str(value)[:32767])
    return f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{text}</t></is></c>'


def _sheet_xml(rows: list[list[Any]]) -> str:
    body: list[str] = []
    for row_index, values in enumerate(rows, start=1):
        cells = "".join(_xlsx_cell(f"{_excel_col(column_index)}{row_index}", value) for column_index, value in enumerate(values))
        body.append(f'<row r="{row_index}">{cells}</row>')
    return '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>' + "".join(body) + "</sheetData></worksheet>"


def _xlsx_bytes(section: dict[str, Any], metadata: dict[str, Any]) -> bytes:
    columns = [str(value) for value in section.get("columns") or []]
    data_rows = [columns] + [[row.get(column) for column in columns] for row in section.get("rows") or [] if isinstance(row, dict)]
    meta_rows = [["Key", "Value"]] + [[key, _scalar(value)] for key, value in sorted(metadata.items())]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/worksheets/sheet2.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>')
        archive.writestr("_rels/.rels", '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        archive.writestr("xl/workbook.xml", '<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Result" sheetId="1" r:id="rId1"/><sheet name="Metadata" sheetId="2" r:id="rId2"/></sheets></workbook>')
        archive.writestr("xl/_rels/workbook.xml.rels", '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet2.xml"/></Relationships>')
        archive.writestr("xl/worksheets/sheet1.xml", _sheet_xml(data_rows))
        archive.writestr("xl/worksheets/sheet2.xml", _sheet_xml(meta_rows))
    return buffer.getvalue()


def _parquet_type(values: list[Any]) -> str:
    non_null = [value for value in values if value is not None]
    if non_null and all(isinstance(value, bool) for value in non_null):
        return "BOOLEAN"
    if non_null and all(isinstance(value, int) and not isinstance(value, bool) for value in non_null):
        return "BIGINT"
    if non_null and all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in non_null):
        return "DOUBLE"
    return "VARCHAR"


def _quote_ident(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _parquet_bytes(section: dict[str, Any]) -> bytes:
    columns = [str(value) for value in section.get("columns") or []]
    rows = [row for row in section.get("rows") or [] if isinstance(row, dict)]
    if not columns:
        raise RequestError("Cannot export a result with no columns to Parquet")
    types = {column: _parquet_type([row.get(column) for row in rows[:1000]]) for column in columns}
    with tempfile.TemporaryDirectory(prefix="treepolo-stage4d-") as temp:
        output = Path(temp) / "result.parquet"
        conn = duckdb.connect()
        try:
            conn.execute("CREATE TABLE export_data (" + ",".join(f"{_quote_ident(column)} {types[column]}" for column in columns) + ")")
            if rows:
                placeholders = ",".join("?" for _ in columns)
                values = []
                for row in rows:
                    converted = []
                    for column in columns:
                        value = _scalar(row.get(column))
                        if types[column] == "VARCHAR" and value is not None:
                            value = str(value)
                        converted.append(value)
                    values.append(tuple(converted))
                conn.executemany(f"INSERT INTO export_data VALUES ({placeholders})", values)
            escaped = str(output).replace("'", "''")
            conn.execute(f"COPY export_data TO '{escaped}' (FORMAT PARQUET)")
        finally:
            conn.close()
        return output.read_bytes()


def _sanitize_svg(value: str) -> str:
    if not value.lstrip().startswith("<svg"):
        return ""
    value = re.sub(r"<script\b[^>]*>.*?</script>", "", value, flags=re.IGNORECASE | re.DOTALL)
    value = re.sub(r"\son\w+\s*=\s*(['\"]).*?\1", "", value, flags=re.IGNORECASE | re.DOTALL)
    return value


def _html_report(title: str, prepared: dict[str, Any], spec: dict[str, Any], chart_svg: str) -> bytes:
    section = prepared["section"]
    columns = [str(value) for value in section.get("columns") or []]
    rows = [row for row in section.get("rows") or [] if isinstance(row, dict)]
    provenance = prepared.get("provenance") or {}
    chart = _sanitize_svg(chart_svg)
    table_head = "".join(f"<th>{html.escape(column)}</th>" for column in columns)
    table_rows = []
    for row in rows[:REPORT_TABLE_ROWS]:
        table_rows.append("<tr>" + "".join(f"<td>{html.escape(str(_scalar(row.get(column)) if row.get(column) is not None else '—'))}</td>" for column in columns) + "</tr>")
    source_json = html.escape(json.dumps(provenance, ensure_ascii=False, indent=2, default=str))
    spec_json = html.escape(json.dumps(spec, ensure_ascii=False, indent=2, default=str))
    body = f"""<!doctype html><html lang=\"zh-Hant\"><head><meta charset=\"utf-8\"><title>{html.escape(title)}</title><style>
body{{font-family:Arial,'Microsoft JhengHei',sans-serif;margin:28px;color:#1f2933}}h1{{font-size:24px}}h2{{margin-top:24px;font-size:16px}}.meta{{display:grid;grid-template-columns:180px 1fr;gap:4px 12px;font-size:12px}}table{{border-collapse:collapse;width:100%;font-size:11px}}th,td{{border:1px solid #b8c0ca;padding:4px;text-align:left}}th{{background:#eef2f6}}.chart{{max-width:1100px;border:1px solid #d0d7df;padding:8px}}pre{{white-space:pre-wrap;background:#f5f7f9;padding:10px;font-size:11px}}.note{{font-size:11px;color:#52606d}}</style></head><body>
<h1>{html.escape(title)}</h1><div class=\"meta\"><b>Rows</b><span>{int(provenance.get('row_count') or len(rows))}</span><b>Backend</b><span>{html.escape(str(provenance.get('backend') or '—'))}</span><b>Data revision</b><span>{html.escape(str(provenance.get('data_revision') or '—'))}</span><b>Grain</b><span>{html.escape(str(provenance.get('grain') or '—'))}</span><b>Sampling</b><span>{html.escape(json.dumps(prepared.get('sampling') or {}, ensure_ascii=False))}</span></div>
{f'<h2>Visualization</h2><div class="chart">{chart}</div>' if chart else ''}<h2>Result</h2><p class=\"note\">Report table shows at most {REPORT_TABLE_ROWS} rows. Use CSV/JSON/XLSX/Parquet export for complete data.</p><table><thead><tr>{table_head}</tr></thead><tbody>{''.join(table_rows)}</tbody></table><h2>Provenance</h2><pre>{source_json}</pre><h2>Presentation Spec</h2><pre>{spec_json}</pre></body></html>"""
    return body.encode("utf-8")


def _pdf_font() -> str:
    try:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
    except ImportError as exc:
        raise RequestError("PDF export requires the reportlab package") from exc
    candidates = [
        Path("C:/Windows/Fonts/msjh.ttc"),
        Path("C:/Windows/Fonts/msjh.ttf"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    ]
    for path in candidates:
        if not path.is_file():
            continue
        try:
            pdfmetrics.registerFont(TTFont("Stage4DReport", str(path), subfontIndex=0))
            return "Stage4DReport"
        except Exception:
            continue
    return "Helvetica"


def _pdf_chart(prepared: dict[str, Any], spec: dict[str, Any], font_name: str) -> Any:
    from reportlab.graphics.shapes import Circle, Drawing, Line, PolyLine, Rect, String

    rows = [row for row in prepared["section"].get("rows") or [] if isinstance(row, dict)][:1000]
    mapping = spec.get("mapping") or {}
    chart_type = spec.get("type") or "scatter"
    x_field = mapping.get("x")
    y_field = mapping.get("y")
    lower_field = mapping.get("lower")
    upper_field = mapping.get("upper")
    drawing = Drawing(500, 260)
    drawing.add(Rect(0, 0, 500, 260, fillColor=None, strokeColor=None))
    if not rows or not y_field:
        drawing.add(String(12, 125, "No plottable rows", fontName=font_name, fontSize=10))
        return drawing
    y_values = [float(row[y_field]) for row in rows if isinstance(row.get(y_field), (int, float)) and math.isfinite(float(row[y_field]))]
    if not y_values:
        drawing.add(String(12, 125, "Selected Y field is not numeric", fontName=font_name, fontSize=10))
        return drawing
    left, right, bottom, top = 45.0, 490.0, 35.0, 240.0
    y_min, y_max = min(y_values), max(y_values)
    if y_min == y_max:
        y_min -= 1
        y_max += 1
    def sy(value: float) -> float:
        return bottom + (value - y_min) / (y_max - y_min) * (top - bottom)
    drawing.add(Line(left, bottom, left, top, strokeColor="#56616f"))
    drawing.add(Line(left, bottom, right, bottom, strokeColor="#56616f"))
    if chart_type in {"scatter", "line"}:
        x_numeric = x_field and all(row.get(x_field) is None or isinstance(row.get(x_field), (int, float)) for row in rows)
        if x_numeric and x_field:
            xs = [float(row[x_field]) for row in rows if isinstance(row.get(x_field), (int, float))]
            x_min, x_max = min(xs), max(xs)
            if x_min == x_max:
                x_min -= 1
                x_max += 1
            sx = lambda value: left + (float(value) - x_min) / (x_max - x_min) * (right - left)
        else:
            sx = lambda value: left + float(value) / max(1, len(rows) - 1) * (right - left)
        points = []
        for index, row in enumerate(rows):
            if not isinstance(row.get(y_field), (int, float)):
                continue
            x_value = row.get(x_field) if x_numeric and x_field else index
            x = sx(x_value)
            y = sy(float(row[y_field]))
            points.extend([x, y])
            if chart_type == "scatter":
                drawing.add(Circle(x, y, 1.8, fillColor="#3269a8", strokeColor=None))
        if chart_type == "line" and len(points) >= 4:
            drawing.add(PolyLine(points, strokeColor="#3269a8", strokeWidth=1.3))
    elif chart_type in {"bar", "difference"}:
        values = [(index, float(row[y_field])) for index, row in enumerate(rows[:40]) if isinstance(row.get(y_field), (int, float))]
        width = (right - left) / max(1, len(values))
        zero = sy(0.0) if y_min <= 0 <= y_max else bottom
        for index, value in values:
            y = sy(value)
            drawing.add(Rect(left + index * width + 1, min(zero, y), max(1, width - 2), abs(y - zero), fillColor="#3269a8", strokeColor=None))
    elif chart_type == "range":
        shown = rows[:40]
        width = (right - left) / max(1, len(shown))
        for index, row in enumerate(shown):
            if not isinstance(row.get(y_field), (int, float)):
                continue
            x = left + (index + 0.5) * width
            y = sy(float(row[y_field]))
            drawing.add(Circle(x, y, 2.2, fillColor="#3269a8", strokeColor=None))
            if isinstance(row.get(lower_field), (int, float)) and isinstance(row.get(upper_field), (int, float)):
                drawing.add(Line(x, sy(float(row[lower_field])), x, sy(float(row[upper_field])), strokeColor="#3269a8"))
    elif chart_type == "dumbbell" and lower_field:
        shown = rows[:30]
        width = (right - left) / max(1, len(shown))
        for index, row in enumerate(shown):
            if not isinstance(row.get(y_field), (int, float)) or not isinstance(row.get(lower_field), (int, float)):
                continue
            x = left + (index + 0.5) * width
            y1, y2 = sy(float(row[lower_field])), sy(float(row[y_field]))
            drawing.add(Line(x, y1, x, y2, strokeColor="#7b8794"))
            drawing.add(Circle(x, y1, 2, fillColor="#7b8794", strokeColor=None))
            drawing.add(Circle(x, y2, 2.4, fillColor="#3269a8", strokeColor=None))
    drawing.add(String(left, 12, str(x_field or "row"), fontName=font_name, fontSize=8))
    drawing.add(String(4, top, str(y_field), fontName=font_name, fontSize=8))
    return drawing


def _pdf_report(title: str, prepared: dict[str, Any], spec: dict[str, Any]) -> bytes:
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    except ImportError as exc:
        raise RequestError("PDF export requires the reportlab package") from exc
    font_name = _pdf_font()
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=14 * mm, rightMargin=14 * mm, topMargin=14 * mm, bottomMargin=14 * mm)
    styles = getSampleStyleSheet()
    for style_name in ("Title", "Heading2", "BodyText"):
        styles[style_name].fontName = font_name
    story: list[Any] = [Paragraph(html.escape(title), styles["Title"]), Spacer(1, 8)]
    provenance = prepared.get("provenance") or {}
    meta = [["Rows", str(provenance.get("row_count") or len(prepared["section"].get("rows") or []))], ["Backend", str(provenance.get("backend") or "—")], ["Data revision", str(provenance.get("data_revision") or "—")], ["Sampling", json.dumps(prepared.get("sampling") or {}, ensure_ascii=False)]]
    meta_table = Table(meta, colWidths=[35 * mm, 135 * mm])
    meta_table.setStyle(TableStyle([("FONTNAME", (0, 0), (-1, -1), font_name), ("FONTSIZE", (0, 0), (-1, -1), 8), ("GRID", (0, 0), (-1, -1), .25, colors.HexColor("#b8c0ca")), ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#eef2f6"))]))
    story.extend([meta_table, Spacer(1, 10), Paragraph("Visualization", styles["Heading2"]), _pdf_chart(prepared, spec, font_name), Spacer(1, 10), Paragraph("Result", styles["Heading2"])])
    section = prepared["section"]
    columns = [str(value) for value in section.get("columns") or []]
    data = [columns] + [[str(_scalar(row.get(column)) if row.get(column) is not None else "—")[:120] for column in columns] for row in (section.get("rows") or [])[:REPORT_TABLE_ROWS] if isinstance(row, dict)]
    if data and columns:
        width = 170 * mm / max(1, len(columns))
        table = Table(data, repeatRows=1, colWidths=[width] * len(columns))
        table.setStyle(TableStyle([("FONTNAME", (0, 0), (-1, -1), font_name), ("FONTSIZE", (0, 0), (-1, -1), 6), ("GRID", (0, 0), (-1, -1), .2, colors.HexColor("#c2c9d1")), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef2f6")), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
        story.append(table)
    doc.build(story)
    return buffer.getvalue()


def install(webapp_module: Any) -> None:
    """Install Stage 4D onto the existing lightweight local web app.

    Keeping this as an extension avoids duplicating the relational/numerical analysis
    stack. The product CLI calls this once before the HTTP service is constructed.
    """

    if getattr(webapp_module, "_stage4d_installed", False):
        return
    webapp_module._stage4d_installed = True

    original_services_init = webapp_module.AppServices.__init__
    original_analyze = webapp_module.AppServices.analyze
    original_get = webapp_module._Handler.do_GET
    original_post = webapp_module._Handler.do_POST
    original_delete = webapp_module._Handler.do_DELETE
    original_static = webapp_module._Handler._static

    def services_init(self: Any, *args: Any, **kwargs: Any) -> None:
        original_services_init(self, *args, **kwargs)
        self.stage4d = Stage4DService(self)

    def services_analyze(self: Any, payload: dict[str, Any]) -> dict[str, Any]:
        result = original_analyze(self, payload)
        try:
            self.stage4d.record_recent(payload, result)
        except Exception as exc:
            print(f"Stage 4D recent-result capture failed: {exc}")
        return result

    def send_bytes(handler: Any, status: int, body: bytes, content_type: str, filename: str | None = None) -> None:
        handler.send_response(status)
        handler.send_header("Content-Type", content_type)
        handler.send_header("Content-Length", str(len(body)))
        handler.send_header("Cache-Control", "no-store")
        if filename:
            handler.send_header("Content-Disposition", f'attachment; filename="{_safe_filename(Path(filename).stem)}{Path(filename).suffix}"')
        handler.end_headers()
        handler.wfile.write(body)

    def path_id(path: str, prefix: str) -> int:
        raw = path.removeprefix(prefix).strip("/")
        try:
            return int(raw)
        except (TypeError, ValueError) as exc:
            raise RequestError("Invalid Stage 4D item id") from exc

    def do_get(self: Any) -> None:
        path = urlparse(self.path).path
        try:
            if path == "/api/visualization/sources":
                self._json(200, self.services.stage4d.sources())
                return
            if path == "/api/visualizations":
                self._json(200, {"visualizations": self.services.stage4d.store.list_visualizations()})
                return
            if path.startswith("/api/visualizations/"):
                item = self.services.stage4d.store.get_visualization(path_id(path, "/api/visualizations/"), include_frozen=True)
                self._json(200 if item is not None else 404, {"item": item})
                return
            if path == "/api/visualization-presets":
                self._json(200, {"built_in": BUILTIN_PRESETS, "user": self.services.stage4d.store.list_presets()})
                return
            if path == "/api/visualization/baseball-asset":
                self._json(200, self.services.stage4d.asset_status())
                return
            return original_get(self)
        except Exception as exc:
            self._error(exc)

    def do_post(self: Any) -> None:
        path = urlparse(self.path).path
        if not (path.startswith("/api/visualization") or path in {"/api/visualizations", "/api/export", "/api/report"}):
            return original_post(self)
        try:
            request = self._read_json()
            if path == "/api/visualization/data":
                self._json(200, self.services.stage4d.prepare_data(request))
                return
            if path == "/api/visualization/describe":
                self._json(200, self.services.stage4d.describe(request))
                return
            if path == "/api/visualizations":
                self._json(200, {"item": self.services.stage4d.save_visualization(request)})
                return
            if path.startswith("/api/visualizations/"):
                self._json(200, {"item": self.services.stage4d.save_visualization(request, path_id(path, "/api/visualizations/"))})
                return
            if path == "/api/visualization-presets":
                spec = request.get("spec")
                if not isinstance(spec, dict):
                    raise RequestError("spec is required")
                self._json(200, {"item": self.services.stage4d.store.save_preset(str(request.get("name") or ""), normalize_spec(spec))})
                return
            if path == "/api/export":
                body, content_type, filename = self.services.stage4d.export(request)
                send_bytes(self, 200, body, content_type, filename)
                return
            if path == "/api/report":
                body, content_type, filename = self.services.stage4d.report(request)
                send_bytes(self, 200, body, content_type, filename)
                return
            self._json(404, {"error": "Unknown Stage 4D API endpoint"})
        except Exception as exc:
            self._error(exc)

    def do_delete(self: Any) -> None:
        path = urlparse(self.path).path
        try:
            if path.startswith("/api/visualizations/"):
                deleted = self.services.stage4d.store.delete_visualization(path_id(path, "/api/visualizations/"))
                self._json(200 if deleted else 404, {"deleted": deleted})
                return
            if path.startswith("/api/visualization-presets/"):
                deleted = self.services.stage4d.store.delete_preset(path_id(path, "/api/visualization-presets/"))
                self._json(200 if deleted else 404, {"deleted": deleted})
                return
            return original_delete(self)
        except Exception as exc:
            self._error(exc)

    def stage4d_static(self: Any, request_path: str) -> None:
        if request_path not in {"", "/", "/index.html"}:
            return original_static(self, request_path)
        candidate = webapp_module.STATIC_DIR / "index.html"
        body = candidate.read_bytes().replace(
            b"</body>",
            b'<script src="/field-checklists.js"></script>\n'
            b'<script src="/analysis-controls.js"></script>\n'
            b'<script src="/analysis-progress.js"></script>\n'
            b'<script src="/stage4-analysis-pages.js"></script>\n'
            b'<script src="/stage4-controls.js"></script>\n'
            b'<script src="/backfill-progress.js"></script>\n'
            b'<script src="/fast-status.js"></script>\n'
            b'<script src="/stage4d-visualization.js"></script>\n</body>',
        )
        content_type = mimetypes.guess_type(candidate.name)[0] or "text/html"
        self.send_response(200)
        self.send_header("Content-Type", content_type + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    webapp_module.AppServices.__init__ = services_init
    webapp_module.AppServices.analyze = services_analyze
    webapp_module._Handler.do_GET = do_get
    webapp_module._Handler.do_POST = do_post
    webapp_module._Handler.do_DELETE = do_delete
    webapp_module._Handler._static = stage4d_static
