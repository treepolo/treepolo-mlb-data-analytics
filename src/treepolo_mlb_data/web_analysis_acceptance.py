from __future__ import annotations

from typing import Any

from .analysis import (
    Aggregate, Binary, Boolean, Column, EventPattern, Filter, Grain, InList, Join,
    Literal, Metric, NamedExpr, OrderKey, Project, SetOperation,
    empirical_percentile, pitch_usage, rank_pitch_roles,
)
from .analysis.workflow import WorkflowPlanner, WorkflowState
from .web_analysis_common import RequestError


class AcceptanceFixesMixin:
    """Acceptance-suite composition features layered over the Stage 4 planner."""

    @staticmethod
    def _acceptance_join_predicate(fields: tuple[str, ...]):
        terms = tuple(Binary(Column(field, "left"), "=", Column(field, "right")) for field in fields)
        if not terms:
            raise RequestError("Join requires at least one key field")
        return terms[0] if len(terms) == 1 else Boolean("and", terms)

    def _build_ranked_pitch_relation(
        self,
        state: WorkflowState,
        spec: dict[str, Any],
    ) -> tuple[tuple[str, ...], str, Any]:
        entities = self._stage_entity_fields(spec.get("entity_fields"), state.fields, "Relative pitch selector")
        pitch_field = self._known_workflow_field(spec.get("pitch_field", "pitch_type"), state.fields)
        if pitch_field != "pitch_type":
            raise RequestError("Relative pitch selector currently requires pitch_type as its pitch field")

        metric_kind = str(spec.get("metric_kind", "usage_rate"))
        grouping = entities + (pitch_field,)
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
            metric_name = "role_metric"
            relation = Aggregate(
                state.node,
                tuple(NamedExpr(field, Column(field)) for field in grouping),
                (Metric(metric_name, function, Column(value_field) if value_field else None),),
                Grain(grouping, "workflow_pitch_role_metric"),
            )
        else:
            raise RequestError("Relative pitch selector metric_kind must be usage_rate or field_metric")

        min_usage_raw = spec.get("min_usage")
        if min_usage_raw not in (None, ""):
            min_usage = float(min_usage_raw)
            if not 0 <= min_usage <= 1:
                raise RequestError("Relative pitch minimum usage must be between 0 and 1")
            usage = pitch_usage(state.node, entity_fields=entities, pitch_field=pitch_field)
            eligible_usage = Filter(usage, Binary(Column("usage_rate"), ">=", Literal(min_usage)))
            if metric_kind == "usage_rate":
                relation = eligible_usage
            else:
                fields = tuple(NamedExpr(field, Column(field, "left")) for field in grouping) + (
                    NamedExpr(metric_name, Column(metric_name, "left")),
                )
                relation = Join(
                    relation,
                    eligible_usage,
                    self._acceptance_join_predicate(grouping),
                    fields,
                    Grain(grouping, "workflow_pitch_role_metric"),
                    "inner",
                )

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

    def _apply_pitch_role_selector_stage(self, planner: WorkflowPlanner, spec: dict[str, Any]) -> None:
        state = planner.state
        entities, pitch_field, selected = self._build_ranked_pitch_relation(state, spec)
        alias = str(spec.get("alias") or "selected_role_rank").strip()
        if not alias or alias in state.fields:
            raise RequestError(f"Relative pitch selector alias must be new and non-empty: {alias}")
        join_keys = entities + (pitch_field,)
        output_fields = tuple(NamedExpr(field, Column(field, "left")) for field in state.fields) + (
            NamedExpr(alias, Column("__ta_selected_role_rank", "right")),
        )
        planner.state = WorkflowState(
            Join(
                state.node,
                selected,
                self._acceptance_join_predicate(join_keys),
                output_fields,
                state.grain,
                "inner",
            ),
            state.fields + (alias,),
            state.grain,
        )

    def _apply_pitch_role_annotate_stage(self, planner: WorkflowPlanner, spec: dict[str, Any]) -> None:
        state = planner.state
        entities, pitch_field, selected = self._build_ranked_pitch_relation(state, spec)
        alias = str(spec.get("alias") or "selected_pitch_type").strip()
        if not alias or alias in state.fields:
            raise RequestError(f"Relative pitch annotation alias must be new and non-empty: {alias}")
        selected_pitch = Project(
            selected,
            tuple(NamedExpr(field, Column(field)) for field in entities)
            + (NamedExpr(alias, Column(pitch_field)),),
            Grain(entities + (alias,), "workflow_selected_pitch"),
        )
        output_fields = tuple(NamedExpr(field, Column(field, "left")) for field in state.fields) + (
            NamedExpr(alias, Column(alias, "right")),
        )
        planner.state = WorkflowState(
            Join(
                state.node,
                selected_pitch,
                self._acceptance_join_predicate(entities),
                output_fields,
                state.grain,
                "inner",
            ),
            state.fields + (alias,),
            state.grain,
        )

    def _apply_empirical_percentile_stage(self, planner: WorkflowPlanner, spec: dict[str, Any]) -> None:
        state = planner.state
        field = self._known_workflow_field(spec.get("field"), state.fields)
        alias = str(spec.get("alias") or "percentile").strip()
        if not alias or alias in state.fields:
            raise RequestError(f"Percentile alias must be new and non-empty: {alias}")
        raw_partitions = spec.get("partition_by", [])
        if not isinstance(raw_partitions, list):
            raise RequestError("Percentile partition_by must be a list")
        partitions = tuple(
            self._known_workflow_field(partition, state.fields)
            for partition in raw_partitions
            if partition
        )
        planner.state = WorkflowState(
            empirical_percentile(
                state.node,
                value_field=field,
                alias=alias,
                partition_fields=partitions,
            ),
            state.fields + (alias,),
            state.grain,
        )

    def _apply_event_pattern_cohorts_stage(self, planner: WorkflowPlanner, spec: dict[str, Any]) -> None:
        state = planner.state
        event_spec = spec.get("event")
        if not isinstance(event_spec, dict):
            event_spec = {
                "field": spec.get("event_field", "pitch_type"),
                "op": spec.get("event_op", "eq"),
                "value": spec.get("event_value", "ST"),
            }
        event = self._workflow_condition(event_spec, state.fields)

        raw_partitions = spec.get("partition_by", ["game_pk", "at_bat_number"])
        raw_order = spec.get("order_by", [{"field": "pitch_number", "descending": False}])
        if not isinstance(raw_partitions, list):
            raise RequestError("Event-pattern partition_by must be a list")
        partitions = tuple(
            self._known_workflow_field(field, state.fields)
            for field in raw_partitions
            if field
        )
        if not partitions:
            raise RequestError("Event-pattern cohorts require partition fields")
        order = self._workflow_order(raw_order, state.fields)
        partition_exprs = tuple(NamedExpr(field, Column(field)) for field in partitions)

        raw_arrangements = spec.get("arrangements", ["consecutive", "none_adjacent"])
        if isinstance(raw_arrangements, str):
            raw_arrangements = [item.strip() for item in raw_arrangements.split(",") if item.strip()]
        if not isinstance(raw_arrangements, list) or not raw_arrangements:
            raise RequestError("Event-pattern cohorts require at least one arrangement")
        arrangement_map = {
            "any": "any",
            "consecutive": "consecutive",
            "all_consecutive": "consecutive",
            "none_adjacent": "none_adjacent",
        }
        arrangements: list[str] = []
        for raw in raw_arrangements:
            key = str(raw).strip()
            if key not in arrangement_map:
                raise RequestError(f"Unsupported event arrangement: {key}")
            canonical = arrangement_map[key]
            if canonical not in arrangements:
                arrangements.append(canonical)

        cohort_alias = str(spec.get("cohort_alias") or "pattern_cohort").strip()
        if not cohort_alias or cohort_alias in state.fields:
            raise RequestError(f"Event-pattern cohort alias must be new and non-empty: {cohort_alias}")
        labels = spec.get("labels") if isinstance(spec.get("labels"), dict) else {}
        exact_raw = spec.get("exact_count")
        exact_count = int(exact_raw) if exact_raw not in (None, "") else None
        occurrence = int(spec.get("occurrence", 1))
        if occurrence < 1:
            raise RequestError("Event-pattern occurrence must be >= 1")

        nodes = []
        for arrangement in arrangements:
            pattern = EventPattern(
                state.node,
                partition_exprs,
                order,
                event,
                occurrence,
                exact_count,
                bool(spec.get("require_last_event", False)),
                arrangement,
            )
            label = str(labels.get(arrangement) or arrangement)
            fields = tuple(NamedExpr(field, Column(field)) for field in state.fields) + (
                NamedExpr(cohort_alias, Literal(label)),
            )
            nodes.append(Project(pattern, fields, state.grain))

        combined = nodes[0]
        for node in nodes[1:]:
            combined = SetOperation(combined, node, "union", all_rows=True)
        planner.state = WorkflowState(
            combined,
            state.fields + (cohort_alias,),
            state.grain,
        )

    def _apply_workflow_stages(self, planner: WorkflowPlanner, raw_stages: Any):
        if raw_stages in (None, []):
            return planner.state
        if not isinstance(raw_stages, list):
            raise RequestError("Workflow stages must be a list")

        inherited_min_usage: float | None = None
        for index, raw_spec in enumerate(raw_stages, 1):
            if not isinstance(raw_spec, dict):
                raise RequestError(f"Workflow stage {index} must be an object")
            spec = dict(raw_spec)
            kind = str(spec.get("kind", "")).strip()

            if kind == "arsenal_signature":
                inherited_min_usage = float(spec.get("min_usage", 0.05))
            elif kind in {"pitch_role_select", "pitch_role_annotate"} and inherited_min_usage is not None:
                spec.setdefault("min_usage", inherited_min_usage)

            if kind == "pitch_role_annotate":
                self._apply_pitch_role_annotate_stage(planner, spec)
            elif kind == "empirical_percentile":
                self._apply_empirical_percentile_stage(planner, spec)
            elif kind == "event_pattern_cohorts":
                self._apply_event_pattern_cohorts_stage(planner, spec)
            else:
                super()._apply_workflow_stages(planner, [spec])
        return planner.state
