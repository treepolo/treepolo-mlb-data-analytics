from __future__ import annotations

from typing import Any

from .analysis import (
    Aggregate, Column, EventPattern, FollowEvent, Grain, Limit, Metric, NamedExpr,
)
from .web_analysis_common import RequestError


_BASIC_METRIC_FUNCTIONS = {"count", "sum", "avg", "min", "max", "median", "stddev_pop", "stddev_samp"}
_NUMERIC_ONLY_METRICS = {"avg", "sum", "median", "stddev_pop", "stddev_samp"}
_METRIC_LABELS = {
    "count": ("Count", "筆數"),
    "sum": ("Sum", "總和"),
    "avg": ("Average", "平均值"),
    "min": ("Minimum", "最小值"),
    "max": ("Maximum", "最大值"),
    "median": ("Median", "中位數"),
    "stddev_pop": ("Population standard deviation", "母體標準差"),
    "stddev_samp": ("Sample standard deviation", "樣本標準差"),
}


class CoreModesMixin:
    def _basic(self, payload: dict[str, Any]) -> dict[str, Any]:
        node = self._filter_source(payload.get("filters"))
        group_by = tuple(self._field(str(field)) for field in payload.get("group_by", []) if field)
        metric_specs = payload.get("metrics", [])
        metrics: list[Metric] = []
        if group_by or metric_specs:
            used: set[str] = set(group_by)
            for spec in metric_specs:
                function = str(spec.get("function", "count"))
                if function not in _BASIC_METRIC_FUNCTIONS:
                    raise RequestError(f"Unsupported metric function: {function}")
                field = str(spec.get("field", "")).strip()
                expr = None
                if function != "count" and not field:
                    english, chinese = _METRIC_LABELS[function]
                    raise RequestError(f"{english} requires a metric field / {chinese}必須指定計算欄位")
                if function != "count" or field:
                    field = self._field(field)
                    if function in _NUMERIC_ONLY_METRICS and self.schema().get(field) not in {"INTEGER", "REAL"}:
                        english, chinese = _METRIC_LABELS[function]
                        raise RequestError(f"{english} requires a numeric field / {chinese}必須使用數值欄位")
                    expr = Column(field)
                base = "row_count" if expr is None else f"{function}_{field}"
                alias = base
                suffix = 2
                while alias in used:
                    alias = f"{base}_{suffix}"; suffix += 1
                used.add(alias)
                metrics.append(Metric(alias, function, expr, bool(spec.get("distinct", False))))
            node = Aggregate(
                node,
                tuple(NamedExpr(field, Column(field)) for field in group_by),
                tuple(metrics),
                Grain(group_by, "grouped" if group_by else "scalar"),
            )
            allowed = tuple(group_by) + tuple(metric.alias for metric in metrics)
        else:
            allowed = tuple(self.schema())
        node = self._apply_result_sort(node, payload, allowed)
        return self._execute(Limit(node, max(0, min(int(payload.get("limit", 200)), 5000))))

    def _sequence_pattern(self, payload: dict[str, Any]) -> dict[str, Any]:
        exact_raw = payload.get("exact_count")
        arrangement = str(payload.get("arrangement", "any"))
        if arrangement not in {"any", "consecutive", "none_adjacent"}:
            raise RequestError("Unsupported event arrangement")
        node = EventPattern(
            self._filter_source(payload.get("filters")),
            (NamedExpr("game_pk", Column("game_pk")), NamedExpr("at_bat_number", Column("at_bat_number"))),
            (self._pitch_order_key(),),
            self._condition(payload.get("event") or {}),
            int(payload.get("occurrence", 1)),
            int(exact_raw) if exact_raw not in (None, "") else None,
            bool(payload.get("require_last_event", False)),
            arrangement,
        )
        fields = self._result_field_names()
        node = self._result_projection(node)
        node = self._apply_result_sort(node, payload, fields)
        return self._execute(node)

    def _follow_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        between: list[NamedExpr] = []
        extra: list[str] = []
        for index, spec in enumerate(payload.get("between", []), 1):
            if not spec.get("field"):
                continue
            alias = f"between_{index}"
            between.append(NamedExpr(alias, self._condition(spec)))
            extra.append(alias)
        node = FollowEvent(
            self._filter_source(payload.get("filters")),
            (NamedExpr("game_pk", Column("game_pk")), NamedExpr("at_bat_number", Column("at_bat_number"))),
            (self._pitch_order_key(),),
            self._condition(payload.get("anchor") or {}),
            self._condition(payload.get("target") or {}),
            int(payload.get("max_gap", 3)),
            tuple(between),
        )
        fields = self._result_field_names(tuple(extra))
        node = self._result_projection(node, tuple(extra))
        node = self._apply_result_sort(node, payload, fields)
        return self._execute(node)

    @staticmethod
    def _pitch_order_key():
        from .analysis import OrderKey
        return OrderKey(Column("pitch_number"))
