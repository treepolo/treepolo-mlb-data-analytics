from __future__ import annotations

from typing import Any

from .analysis import (
    Aggregate, Binary, Column, Filter, Grain, InList, IsNull, Literal, Metric,
    NamedExpr, Project, Window, WindowField, pitch_usage, rank_pitch_roles,
)
from .analysis.workflow import WorkflowState
from .web_analysis_common import RequestError


class AcceptanceRuntimeFixesMixin:
    """Runtime refinements for the acceptance batch.

    Keep the public acceptance composition layer small while avoiding very deep
    duplicated SQL trees in relative-pitch selection. In particular, when a
    field metric also needs a minimum-usage gate, compute the metric and usage
    denominator in one grouped relation instead of joining two separately
    expanded copies of the current workflow state.
    """

    def _build_ranked_pitch_relation(
        self,
        state: WorkflowState,
        spec: dict[str, Any],
    ) -> tuple[tuple[str, ...], str, Any]:
        entities = self._stage_entity_fields(spec.get("entity_fields"), state.fields, "Relative pitch selector")
        pitch_field = self._known_workflow_field(spec.get("pitch_field", "pitch_type"), state.fields)
        if pitch_field != "pitch_type":
            raise RequestError("Relative pitch selector currently requires pitch_type as its pitch field")

        min_usage_raw = spec.get("min_usage")
        min_usage: float | None = None
        if min_usage_raw not in (None, ""):
            min_usage = float(min_usage_raw)
            if not 0 <= min_usage <= 1:
                raise RequestError("Relative pitch minimum usage must be between 0 and 1")

        metric_kind = str(spec.get("metric_kind", "usage_rate"))
        grouping = entities + (pitch_field,)
        if metric_kind == "usage_rate":
            relation = pitch_usage(state.node, entity_fields=entities, pitch_field=pitch_field)
            metric_name = "usage_rate"
            if min_usage is not None:
                relation = Filter(relation, Binary(Column("usage_rate"), ">=", Literal(min_usage)))
        elif metric_kind == "field_metric":
            function = str(spec.get("function", "avg"))
            if function not in {"count", "sum", "avg", "min", "max", "median", "stddev_pop", "stddev_samp"}:
                raise RequestError(f"Unsupported relative-pitch aggregate: {function}")
            value_field = str(spec.get("value_field") or "").strip()
            if function != "count" and not value_field:
                raise RequestError("Relative pitch field metric requires a value field")
            if value_field:
                value_field = self._known_workflow_field(value_field, state.fields)

            metric_name = "role_metric"
            clean_source = Filter(state.node, IsNull(Column(pitch_field), True))
            metrics = [Metric(metric_name, function, Column(value_field) if value_field else None)]
            if min_usage is not None:
                metrics.append(Metric("__ta_pitch_count", "count"))
            relation = Aggregate(
                clean_source,
                tuple(NamedExpr(field, Column(field)) for field in grouping),
                tuple(metrics),
                Grain(grouping, "workflow_pitch_role_metric"),
            )

            if min_usage is not None:
                relation = Window(
                    relation,
                    (
                        WindowField(
                            "__ta_total_pitch_count",
                            "sum",
                            (Column("__ta_pitch_count"),),
                            tuple(Column(field) for field in entities),
                        ),
                    ),
                )
                relation = Project(
                    relation,
                    tuple(NamedExpr(field, Column(field)) for field in grouping)
                    + (
                        NamedExpr(metric_name, Column(metric_name)),
                        NamedExpr("__ta_pitch_count", Column("__ta_pitch_count")),
                        NamedExpr("__ta_total_pitch_count", Column("__ta_total_pitch_count")),
                        NamedExpr(
                            "__ta_usage_rate",
                            Binary(
                                Binary(Column("__ta_pitch_count"), "*", Literal(1.0)),
                                "/",
                                Column("__ta_total_pitch_count"),
                            ),
                        ),
                    ),
                    Grain(grouping, "workflow_pitch_role_metric"),
                )
                relation = Filter(
                    relation,
                    Binary(Column("__ta_usage_rate"), ">=", Literal(min_usage)),
                )
        else:
            raise RequestError("Relative pitch selector metric_kind must be usage_rate or field_metric")

        exclude = spec.get("exclude_pitch_types") or []
        if isinstance(exclude, str):
            exclude = [item.strip() for item in exclude.split(",") if item.strip()]
        if not isinstance(exclude, list):
            raise RequestError("Relative pitch excluded types must be a list")
        if exclude:
            relation = Filter(
                relation,
                InList(Column(pitch_field), tuple(Literal(str(value)) for value in exclude), True),
            )

        tie_method = str(spec.get("tie_method", "row_number"))
        if tie_method not in {"row_number", "rank", "dense_rank"}:
            raise RequestError("Unsupported relative-pitch tie handling method")
        ranked = rank_pitch_roles(
            relation,
            entity_fields=entities,
            metric=metric_name,
            alias="__ta_selected_role_rank",
            descending=str(spec.get("direction", "desc")) != "asc",
            method=tie_method,
        )
        selected_rank = int(spec.get("rank", 1))
        if selected_rank < 1:
            raise RequestError("Relative pitch selected rank must be >= 1")
        selected = Filter(
            ranked,
            Binary(Column("__ta_selected_role_rank"), "=", Literal(selected_rank)),
        )
        return entities, pitch_field, selected
