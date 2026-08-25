from __future__ import annotations

from typing import Any

from .analysis import (
    Aggregate, Column, EventPattern, FollowEvent, Grain, Limit, Metric, NamedExpr,
    OrderKey, Sort,
)
from .web_analysis_common import RequestError


_BASIC_METRIC_FUNCTIONS = {"count", "sum", "avg", "min", "max", "median", "stddev_pop", "stddev_samp"}
_NUMERIC_ONLY_METRICS = {"median", "stddev_pop", "stddev_samp"}


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
                field = str(spec.get("field", ""))
                expr = None
                if function != "count" or field:
                    field = self._field(field)
                    if function in _NUMERIC_ONLY_METRICS and self.schema().get(field) not in {"INTEGER", "REAL"}:
                        raise RequestError(f"{function} requires a numeric field")
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
        sort = payload.get("sort") or {}
        sort_field = str(sort.get("field", ""))
        if sort_field:
            if group_by or metric_specs:
                output_fields = set(group_by) | {metric.alias for metric in metrics}
                if sort_field not in output_fields:
                    raise RequestError("Grouped results can be sorted only by selected group fields or computed metrics")
            else:
                sort_field = self._field(sort_field)
            node = Sort(node, (OrderKey(Column(sort_field), bool(sort.get("descending", False))),))
        return self._execute(Limit(node, max(0, min(int(payload.get("limit", 200)), 5000))))

    def _sequence_pattern(self, payload: dict[str, Any]) -> dict[str, Any]:
        exact_raw = payload.get("exact_count")
        arrangement = str(payload.get("arrangement", "any"))
        if arrangement not in {"any", "consecutive", "none_adjacent"}:
            raise RequestError("Unsupported event arrangement")
        node = EventPattern(
            self._filter_source(payload.get("filters")),
            (NamedExpr("game_pk", Column("game_pk")), NamedExpr("at_bat_number", Column("at_bat_number"))),
            (OrderKey(Column("pitch_number")),),
            self._condition(payload.get("event") or {}),
            int(payload.get("occurrence", 1)),
            int(exact_raw) if exact_raw not in (None, "") else None,
            bool(payload.get("require_last_event", False)),
            arrangement,
        )
        return self._execute(self._result_projection(node))

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
            (OrderKey(Column("pitch_number")),),
            self._condition(payload.get("anchor") or {}),
            self._condition(payload.get("target") or {}),
            int(payload.get("max_gap", 3)),
            tuple(between),
        )
        return self._execute(self._result_projection(node, tuple(extra)))
