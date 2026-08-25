from __future__ import annotations

from typing import Any

from .analysis import (
    Aggregate, Binary, Boolean, Case, Column, Filter, Grain, InList, Join, Literal,
    Metric, NamedExpr, Project, arsenal_table, pitch_usage, rank_pitch_roles,
)
from .analysis.numerical import ClusteringSpec, NumericalExecutor
from .analysis.workflow import DerivedStage, WorkflowPlanner, WorkflowState
from .web_analysis_common import RequestError, _PROGRESS
from .web_analysis_stage4 import Stage4ModesMixin, _literal


class Stage4ExtendedModesMixin(Stage4ModesMixin):
    """Complete workflow composition with baseball selectors and numerical continuation."""

    def _workflow_metric(self, spec: dict[str, Any], fields: tuple[str, ...], used: set[str]) -> Metric:
        metric = super()._workflow_metric(spec, fields, used)
        condition_spec = spec.get("condition")
        if not condition_spec:
            return metric
        if not isinstance(condition_spec, dict):
            raise RequestError("Workflow metric condition must be an object")
        predicate = self._workflow_condition(condition_spec, fields)
        value = metric.expr if metric.expr is not None else Literal(1)
        return Metric(
            metric.alias,
            metric.function,
            Case(((predicate, value),), Literal(None)),
            metric.distinct,
        )

    def _stage_entity_fields(self, raw: Any, fields: tuple[str, ...], label: str) -> tuple[str, ...]:
        if not isinstance(raw, list) or not raw:
            raise RequestError(f"{label} requires at least one entity/group field")
        return tuple(self._known_workflow_field(field, fields) for field in raw if field)

    @staticmethod
    def _join_predicate(fields: tuple[str, ...]):
        terms = tuple(Binary(Column(field, "left"), "=", Column(field, "right")) for field in fields)
        return terms[0] if len(terms) == 1 else Boolean("and", terms)

    def _apply_arsenal_signature_stage(self, planner: WorkflowPlanner, spec: dict[str, Any]) -> None:
        state = planner.state
        entities = self._stage_entity_fields(spec.get("entity_fields"), state.fields, "Arsenal signature")
        pitch_field = self._known_workflow_field(spec.get("pitch_field", "pitch_type"), state.fields)
        alias = str(spec.get("alias") or "arsenal").strip()
        if not alias or alias in state.fields:
            raise RequestError(f"Arsenal signature alias must be new and non-empty: {alias}")
        min_usage = float(spec.get("min_usage", 0.05))
        if not 0 <= min_usage <= 1:
            raise RequestError("Arsenal minimum usage must be between 0 and 1")
        usage = pitch_usage(state.node, entity_fields=entities, pitch_field=pitch_field)
        signature = arsenal_table(
            usage,
            entity_fields=entities,
            pitch_field=pitch_field,
            min_usage=min_usage,
            alias=alias,
        )
        fields = tuple(NamedExpr(field, Column(field, "left")) for field in state.fields) + (
            NamedExpr(alias, Column(alias, "right")),
        )
        node = Join(
            state.node,
            signature,
            self._join_predicate(entities),
            fields,
            state.grain,
            "inner",
        )
        planner.state = WorkflowState(node, state.fields + (alias,), state.grain)

    def _apply_pitch_role_selector_stage(self, planner: WorkflowPlanner, spec: dict[str, Any]) -> None:
        state = planner.state
        entities = self._stage_entity_fields(spec.get("entity_fields"), state.fields, "Relative pitch selector")
        pitch_field = self._known_workflow_field(spec.get("pitch_field", "pitch_type"), state.fields)
        if pitch_field != "pitch_type":
            raise RequestError("Relative pitch selector currently requires pitch_type as its pitch field")
        metric_kind = str(spec.get("metric_kind", "usage_rate"))
        tie_method = str(spec.get("tie_method", "dense_rank"))
        if tie_method not in {"row_number", "rank", "dense_rank"}:
            raise RequestError("Unsupported relative-pitch tie handling method")

        if metric_kind == "usage_rate":
            relation = pitch_usage(state.node, entity_fields=entities, pitch_field=pitch_field)
            metric_name = "usage_rate"
        elif metric_kind == "field_metric":
            function = str(spec.get("function", "avg"))
            if function not in {"count", "sum", "avg", "min", "max", "median", "stddev_pop", "stddev_samp"}:
                raise RequestError(f"Unsupported relative-pitch aggregate: {function}")
            value_field = str(spec.get("value_field") or "").strip()
            if function != "count" and not value_field:
                raise RequestError("Relative pitch field metric requires a value field")
            if value_field:
                value_field = self._known_workflow_field(value_field, state.fields)
            grouping = entities + (pitch_field,)
            metric_name = "role_metric"
            relation = Aggregate(
                state.node,
                tuple(NamedExpr(field, Column(field)) for field in grouping),
                (Metric(metric_name, function, Column(value_field) if value_field else None),),
                Grain(grouping, "workflow_pitch_role_metric"),
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
        selected = Filter(ranked, Binary(Column("__ta_selected_role_rank"), "=", Literal(selected_rank)))

        join_keys = entities + (pitch_field,)
        alias = str(spec.get("alias") or "selected_role_rank").strip()
        if not alias or alias in state.fields:
            raise RequestError(f"Relative pitch selector alias must be new and non-empty: {alias}")
        output_fields = tuple(NamedExpr(field, Column(field, "left")) for field in state.fields) + (
            NamedExpr(alias, Column("__ta_selected_role_rank", "right")),
        )
        node = Join(
            state.node,
            selected,
            self._join_predicate(join_keys),
            output_fields,
            state.grain,
            "inner",
        )
        planner.state = WorkflowState(node, state.fields + (alias,), state.grain)

    def _apply_workflow_stages(self, planner: WorkflowPlanner, raw_stages: Any):
        if raw_stages in (None, []):
            return planner.state
        if not isinstance(raw_stages, list):
            raise RequestError("Workflow stages must be a list")
        for index, spec in enumerate(raw_stages, 1):
            if not isinstance(spec, dict):
                raise RequestError(f"Workflow stage {index} must be an object")
            kind = str(spec.get("kind", "")).strip()
            if kind == "arsenal_signature":
                self._apply_arsenal_signature_stage(planner, spec)
                continue
            if kind == "pitch_role_select":
                self._apply_pitch_role_selector_stage(planner, spec)
                continue
            if kind != "derive":
                super()._apply_workflow_stages(planner, [spec])
                continue

            fields = planner.state.fields
            alias = str(spec.get("alias") or "").strip()
            left = self._known_workflow_field(spec.get("left"), fields)
            op = str(spec.get("operator", "/"))
            if op not in {"+", "-", "*", "/", "%"}:
                raise RequestError(f"Unsupported derived arithmetic operator: {op}")
            right_field = str(spec.get("right_field") or "").strip()
            if right_field:
                right = Column(self._known_workflow_field(right_field, fields))
            else:
                if "right_value" not in spec:
                    raise RequestError("Derived field requires right_field or right_value")
                right = _literal(spec.get("right_value"))

            # SQLite performs integer division when both operands are integers,
            # while DuckDB promotes `/` to a fractional result. Research metrics
            # such as pitch usage must be identical across both relational paths.
            left_expr = Column(left)
            if op == "/":
                left_expr = Binary(left_expr, "*", Literal(1.0))
            planner.apply(DerivedStage(alias, Binary(left_expr, op, right)))
        return planner.state

    def _clustering(self, payload: dict[str, Any]) -> dict[str, Any]:
        raw_features = payload.get("features", [])
        if not isinstance(raw_features, list) or not raw_features:
            raise RequestError("Clustering requires feature fields")
        features = tuple(str(field) for field in raw_features)
        raw_ids = payload.get("id_fields", [])
        raw_partitions = payload.get("partition_fields", [])
        if not isinstance(raw_ids, list) or not isinstance(raw_partitions, list):
            raise RequestError("Clustering id_fields and partition_fields must be lists")
        id_fields = tuple(str(field) for field in raw_ids)
        partition_fields = tuple(str(field) for field in raw_partitions)
        required = tuple(dict.fromkeys(features + id_fields + partition_fields))
        progress = _PROGRESS.get()
        if progress is not None:
            progress("numerical_prepare", 5.0, "Preparing relational input for clustering")
        table, input_backend = self._numerical_input(payload, required)
        method = str(payload.get("method", "kmeans"))
        if method not in {"kmeans", "gmm"}:
            raise RequestError("Clustering method must be kmeans or gmm")
        result = NumericalExecutor().clustering(
            table,
            ClusteringSpec(
                features=features,
                method=method,
                clusters=int(payload.get("clusters", 3)),
                standardize=bool(payload.get("standardize", True)),
                seed=int(payload.get("seed", 42)),
                id_fields=id_fields,
                partition_fields=partition_fields,
                assignment_limit=max(0, min(int(payload.get("assignment_limit", 5000)), 50_000)),
            ),
            progress,
        )
        result["input_backend"] = input_backend
        return result
