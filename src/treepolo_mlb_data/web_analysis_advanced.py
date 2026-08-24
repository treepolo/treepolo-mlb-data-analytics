from __future__ import annotations

from typing import Any

from .analysis import (
    Aggregate, Binary, Boolean, Column, Filter, Grain, InList, Join, Literal,
    Metric, NamedExpr, OrderKey, Project, SetOperation, Sort, Window, WindowField,
    arsenal_table, empirical_percentile, pitch_usage, rank_pitch_roles,
)
from .web_analysis_common import RequestError


class AdvancedModesMixin:
    def _arsenal(self, payload: dict[str, Any]) -> dict[str, Any]:
        source = self._filter_source(payload.get("filters")); entities = self._entity_fields(payload)
        usage = pitch_usage(source, entity_fields=entities)
        arsenal = arsenal_table(usage, entity_fields=entities, min_usage=float(payload.get("min_usage", 0.05)))
        ranked = rank_pitch_roles(usage, entity_fields=entities, metric="usage_rate", method=self._tie_method(payload))
        terms = tuple(Binary(Column(field, "left"), "=", Column(field, "right")) for field in entities)
        on = terms[0] if len(terms) == 1 else Boolean("and", terms)
        fields = [NamedExpr(field, Column(field, "left")) for field in entities]
        fields.extend((
            NamedExpr("pitch_type", Column("pitch_type", "left")),
            NamedExpr("pitch_count", Column("pitch_count", "left")),
            NamedExpr("total_pitch_count", Column("total_pitch_count", "left")),
            NamedExpr("usage_rate", Column("usage_rate", "left")),
            NamedExpr("role_rank", Column("role_rank", "left")),
            NamedExpr("arsenal", Column("arsenal", "right")),
        ))
        node = Join(ranked, arsenal, on, tuple(fields), Grain(entities + ("pitch_type",), "pitch_role"))
        node = Sort(node, tuple(OrderKey(Column(field)) for field in entities) + (OrderKey(Column("role_rank")),))
        return self._execute(node)

    def _pitch_role(self, payload: dict[str, Any]) -> dict[str, Any]:
        source = self._filter_source(payload.get("filters")); entities = self._entity_fields(payload)
        pitch_field = self._field("pitch_type"); metric_kind = str(payload.get("metric_kind", "usage_rate"))
        if metric_kind == "usage_rate":
            relation = pitch_usage(source, entity_fields=entities, pitch_field=pitch_field); metric_name = "usage_rate"
        else:
            value_field = self._field(str(payload.get("value_field", "release_speed")))
            function = str(payload.get("function", "avg"))
            if function not in {"count", "sum", "avg", "min", "max"}:
                raise RequestError("Unsupported role metric function")
            grouping = entities + (pitch_field,); metric_name = "role_metric"
            relation = Aggregate(
                source, tuple(NamedExpr(field, Column(field)) for field in grouping),
                (Metric(metric_name, function, Column(value_field) if function != "count" else None),),
                Grain(grouping, "pitch_role_metric"),
            )
        exclude = payload.get("exclude_pitch_types") or []
        if exclude:
            relation = Filter(relation, InList(Column(pitch_field), tuple(Literal(str(v)) for v in exclude), True))
        ranked = rank_pitch_roles(
            relation, entity_fields=entities, metric=metric_name, alias="role_rank",
            descending=bool(payload.get("descending", True)), method=self._tie_method(payload),
        )
        return self._execute(Filter(ranked, Binary(Column("role_rank"), "=", Literal(int(payload.get("rank", 1))))))

    def _temporal(self, payload: dict[str, Any]) -> dict[str, Any]:
        source = self._filter_source(payload.get("filters")); entities = self._entity_fields(payload)
        period_field = self._field(str(payload.get("period_field", "game_pk")))
        value_field = self._field(str(payload.get("value_field", "release_speed")))
        function = str(payload.get("function", "avg"))
        if function not in {"count", "sum", "avg", "min", "max"}:
            raise RequestError("Unsupported temporal metric function")
        grouping = entities + (period_field,); metric_name = "current_value"
        grouped = Aggregate(
            source, tuple(NamedExpr(field, Column(field)) for field in grouping),
            (Metric(metric_name, function, None if function == "count" else Column(value_field)),),
            Grain(grouping, "temporal"),
        )
        fn = "lag" if str(payload.get("direction", "previous")) == "previous" else "lead"
        window = Window(grouped, (WindowField(
            "reference_value", fn, (Column(metric_name), Literal(max(1, int(payload.get("offset", 1))))),
            tuple(Column(field) for field in entities), (OrderKey(Column(period_field)),),
        ),))
        fields = [NamedExpr(field, Column(field)) for field in grouping]
        fields.extend((
            NamedExpr("current_value", Column("current_value")),
            NamedExpr("reference_value", Column("reference_value")),
            NamedExpr("difference", Binary(Column("current_value"), "-", Column("reference_value"))),
        ))
        return self._execute(Project(window, tuple(fields), Grain(grouping, "temporal")))

    def _percentile(self, payload: dict[str, Any]) -> dict[str, Any]:
        source = self._filter_source(payload.get("filters")); entities = self._entity_fields(payload)
        value_field = self._field(str(payload.get("value_field", "release_speed")))
        node = empirical_percentile(source, value_field=value_field, alias="percentile", partition_fields=entities)
        op = ">=" if str(payload.get("side", "high")) == "high" else "<="
        node = Filter(node, Binary(Column("percentile"), op, Literal(float(payload.get("threshold", 0.8)))))
        return self._execute(self._result_projection(node, ("percentile",)))

    def _cross_level(self, payload: dict[str, Any]) -> dict[str, Any]:
        source = self._filter_source(payload.get("filters"))
        unit_fields = tuple(self._field(str(f)) for f in payload.get("unit_fields", ["pitcher", "game_pk"]) if f)
        baseline_fields = tuple(self._field(str(f)) for f in payload.get("baseline_fields", ["pitcher"]) if f)
        if not unit_fields or not baseline_fields or not set(baseline_fields).issubset(unit_fields):
            raise RequestError("Baseline fields must be a non-empty subset of unit fields")
        value_field = self._field(str(payload.get("value_field", "release_speed")))
        function = str(payload.get("function", "avg"))
        if function not in {"count", "sum", "avg", "min", "max"}:
            raise RequestError("Unsupported comparison metric function")
        value = None if function == "count" else Column(value_field)
        unit = Aggregate(source, tuple(NamedExpr(f, Column(f)) for f in unit_fields), (Metric("unit_value", function, value),), Grain(unit_fields, "unit"))
        baseline = Aggregate(source, tuple(NamedExpr(f, Column(f)) for f in baseline_fields), (Metric("baseline_value", function, value),), Grain(baseline_fields, "baseline"))
        terms = tuple(Binary(Column(f, "left"), "=", Column(f, "right")) for f in baseline_fields)
        on = terms[0] if len(terms) == 1 else Boolean("and", terms)
        fields = [NamedExpr(f, Column(f, "left")) for f in unit_fields]
        fields.extend((
            NamedExpr("unit_value", Column("unit_value", "left")),
            NamedExpr("baseline_value", Column("baseline_value", "right")),
            NamedExpr("difference", Binary(Column("unit_value", "left"), "-", Column("baseline_value", "right"))),
        ))
        return self._execute(Join(unit, baseline, on, tuple(fields), Grain(unit_fields, "cross_level")))

    def _period_pitch_set(self, filters, start: str, end: str, entities: tuple[str, ...], min_usage: float):
        period_filters = list(filters or []) + [
            {"field": "game_date", "op": "ge", "value": start},
            {"field": "game_date", "op": "le", "value": end},
        ]
        usage = pitch_usage(self._filter_source(period_filters), entity_fields=entities)
        eligible = Filter(usage, Binary(Column("usage_rate"), ">", Literal(min_usage)))
        fields = tuple(NamedExpr(field, Column(field)) for field in entities + ("pitch_type",))
        return Project(eligible, fields, Grain(entities + ("pitch_type",), "arsenal_pitch"))

    def _arsenal_change(self, payload: dict[str, Any]) -> dict[str, Any]:
        entities = self._entity_fields(payload); min_usage = float(payload.get("min_usage", 0.05))
        a = payload.get("period_a") or {}; b = payload.get("period_b") or {}
        for label, period in (("A", a), ("B", b)):
            if not period.get("start") or not period.get("end"):
                raise RequestError(f"Period {label} requires start and end dates")
        first = self._period_pitch_set(payload.get("filters"), str(a["start"]), str(a["end"]), entities, min_usage)
        second = self._period_pitch_set(payload.get("filters"), str(b["start"]), str(b["end"]), entities, min_usage)
        return {"sections": [
            {"title": "新增球種 Added Pitches", **self._execute(SetOperation(second, first, "except"))},
            {"title": "移除球種 Removed Pitches", **self._execute(SetOperation(first, second, "except"))},
        ]}
