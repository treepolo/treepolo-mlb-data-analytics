from __future__ import annotations

from typing import Any

from .config_schema import ConfigError, ConfigField
from .methods import ResearchContext, ResearchMethod, ResearchResult, register_method


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


ANALYSIS_PAYLOAD = AnalysisPayloadMethod()


def register_builtin_methods() -> None:
    register_method(ANALYSIS_PAYLOAD)


register_builtin_methods()
