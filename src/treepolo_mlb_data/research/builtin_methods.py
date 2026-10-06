from __future__ import annotations

import sqlite3
from typing import Any

from . import profile
from .config_schema import ConfigError, ConfigField
from .methods import ResearchContext, ResearchMethod, ResearchResult, register_method
from .scope import scope_where_sql


class AnalysisPayloadMethod(ResearchMethod):
    """Record any existing analysis mode (basic, workflow, regression...) as a research run."""

    kind = "analysis_payload"
    version = 1
    label_zh = "既有分析記錄為研究"
    label_en = "Existing analysis recorded as a research run"
    requires_scope = False
    fields = (
        ConfigField(
            "payload", "json", required=True,
            label_zh="分析請求", label_en="Analysis payload",
            help_zh="與 /api/analyze 相同的請求內容；不得含 result_limit，避免結果被靜默截斷。",
            help_en="Same body as /api/analyze; must not contain result_limit so rows are never silently truncated.",
        ),
    )

    def validate(self, config: dict[str, Any], ctx: ResearchContext) -> None:
        payload = config["payload"]
        if not isinstance(payload, dict) or not payload.get("mode"):
            raise ConfigError("payload must be an object with a mode / payload 必須是含 mode 的物件")
        if "result_limit" in payload:
            raise ConfigError(
                "payload must not contain result_limit (it silently truncates rows) / payload 不得含 result_limit（會靜默截斷資料列）"
            )

    def run(self, ctx: ResearchContext, config: dict[str, Any]) -> ResearchResult:
        payload = config["payload"]
        result = ctx.facade.analyze(payload, progress=ctx.progress)
        if isinstance(result.get("sections"), list) and result["sections"]:
            sections = tuple(dict(section) for section in result["sections"])
        else:
            rows = list(result.get("rows", []))
            sections = ({
                "title": str(payload["mode"]),
                "columns": list(result.get("columns", [])),
                "rows": rows,
                "grain": result.get("grain") or {"keys": [], "label": None},
                "row_count": int(result.get("row_count", len(rows))),
                "backend": result.get("backend"),
            },)
        return ResearchResult(sections=sections, extras={"payload_mode": str(payload["mode"])})


class DataProfileMethod(ResearchMethod):
    """Measure value ranges, field coverage, bunt share and zone definitions in the data scope."""

    kind = "data_profile"
    version = 1
    label_zh = "資料剖析"
    label_en = "Data profile"
    requires_scope = True
    fields = (
        ConfigField(
            "sections", "str_list", default=list(profile.SECTION_NAMES), unique_sorted=True,
            label_zh="要計算的節", label_en="Sections to compute",
            help_zh="預設全部；名稱見 research methods 輸出。", help_en="All by default; see the method description for names.",
        ),
        ConfigField(
            "tracked_fields", "str_list", default=list(profile.DEFAULT_TRACKED_FIELDS),
            label_zh="要量測覆蓋率的欄位", label_en="Fields whose coverage is measured",
            help_zh="必須是 pitches 表的現有欄位。", help_en="Must be existing columns of the pitches table.",
        ),
    )

    def _columns(self, ctx: ResearchContext) -> set[str]:
        conn = sqlite3.connect(ctx.config.database_path)
        try:
            return profile.table_columns(conn)
        finally:
            conn.close()

    def validate(self, config: dict[str, Any], ctx: ResearchContext) -> None:
        unknown = sorted(set(config["sections"]) - set(profile.SECTION_NAMES))
        if unknown:
            raise ConfigError(f"Unknown sections: {unknown} / 未知的節: {unknown}")
        if not config["sections"]:
            raise ConfigError("At least one section is required / 至少要選一節")
        available = self._columns(ctx)
        missing_fields = sorted(set(config["tracked_fields"]) - available)
        if missing_fields:
            raise ConfigError(f"tracked_fields not in the pitches table: {missing_fields} / tracked_fields 不在資料表中: {missing_fields}")
        missing: dict[str, list[str]] = {}
        for name in config["sections"]:
            gone = [c for c in profile.SECTION_COLUMNS[name] if c not in available]
            if gone:
                missing[name] = gone
        if missing:
            raise ConfigError(f"The pitches table lacks columns needed by sections: {missing} / 資料表缺少各節所需欄位: {missing}")

    def run(self, ctx: ResearchContext, config: dict[str, Any]) -> ResearchResult:
        where, params = scope_where_sql(ctx.scope)
        wanted = [name for name in profile.SECTION_NAMES if name in config["sections"]]
        sections: list[dict[str, Any]] = []
        conn = sqlite3.connect(f"file:{ctx.config.database_path}?mode=ro", uri=True)
        try:
            available = profile.table_columns(conn)
            for index, name in enumerate(wanted):
                if ctx.progress:
                    ctx.progress("data_profile", 100.0 * index / len(wanted), f"Profiling {name}")
                if name == "outcome_category_counts":
                    sections.append(profile.outcome_category_counts(ctx))
                else:
                    sections.append(profile.SQL_SECTIONS[name](
                        conn, where, params, tracked_fields=tuple(config["tracked_fields"]), available=frozenset(available)))
            rows_in_scope = profile.pitch_rows_in_scope(conn, ctx.scope)
        finally:
            conn.close()
        return ResearchResult(
            sections=tuple(sections),
            extras={"scope": ctx.scope, "pitch_rows_in_scope": rows_in_scope, "generated_by": "data_profile v1"},
        )


ANALYSIS_PAYLOAD = AnalysisPayloadMethod()
DATA_PROFILE = DataProfileMethod()


def register_builtin_methods() -> None:
    register_method(ANALYSIS_PAYLOAD)
    register_method(DATA_PROFILE)


register_builtin_methods()
