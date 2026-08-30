from __future__ import annotations

from typing import Any

from .analysis._lazy_scientific import np
from .analysis import (
    Aggregate, Binary, Boolean, Case, Column, Filter, Grain, InList, Join, Literal,
    Metric, NamedExpr, arsenal_table, pitch_usage, rank_pitch_roles,
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
        return Metric(metric.alias, metric.function, Case(((predicate, value),), Literal(None)), metric.distinct)

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
            usage, entity_fields=entities, pitch_field=pitch_field,
            min_usage=min_usage, alias=alias,
        )
        fields = tuple(NamedExpr(field, Column(field, "left")) for field in state.fields) + (
            NamedExpr(alias, Column(alias, "right")),
        )
        planner.state = WorkflowState(
            Join(state.node, signature, self._join_predicate(entities), fields, state.grain, "inner"),
            state.fields + (alias,), state.grain,
        )

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
            relation = Filter(relation, InList(Column(pitch_field), tuple(Literal(str(value)) for value in exclude), True))

        ranked = rank_pitch_roles(
            relation, entity_fields=entities, metric=metric_name,
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
        planner.state = WorkflowState(
            Join(state.node, selected, self._join_predicate(join_keys), output_fields, state.grain, "inner"),
            state.fields + (alias,), state.grain,
        )

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
        table, model_features, encodings = self._encode_model_features(table, features, label="Clustering features")
        method = str(payload.get("method", "kmeans"))
        if method not in {"kmeans", "gmm"}:
            raise RequestError("Clustering method must be kmeans or gmm")
        result = NumericalExecutor().clustering(
            table,
            ClusteringSpec(
                features=model_features, method=method, clusters=int(payload.get("clusters", 3)),
                standardize=bool(payload.get("standardize", True)), seed=int(payload.get("seed", 42)),
                id_fields=id_fields, partition_fields=partition_fields,
                assignment_limit=max(0, min(int(payload.get("assignment_limit", 5000)), 50_000)),
            ),
            progress,
        )
        result["input_backend"] = input_backend
        result.setdefault("numerical", {})["requested_features"] = list(features)
        result["numerical"]["feature_encodings"] = {key: list(value) for key, value in encodings.items()}
        return result

    def _cluster_compare(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Run stress-test #10 as a reusable multi-stage research analysis.

        1) annotate each entity's arsenal signature;
        2) within each arsenal group select the best non-reference pitch type;
        3) cluster that selected pitch separately inside every entity;
        4) select the best cluster by a numeric evaluation field;
        5) compare that cluster with the reference pitch for the same entity.
        """
        schema = self.schema()
        raw_entities = payload.get("entity_fields", ["pitcher"])
        if not isinstance(raw_entities, list) or not raw_entities:
            raise RequestError("Cluster comparison requires entity fields")
        entities = tuple(self._field(str(field)) for field in raw_entities if field)
        if not entities:
            raise RequestError("Cluster comparison requires at least one entity field")
        reference_pitch = str(payload.get("reference_pitch_type", "FF") or "FF")
        selection_field = self._field(str(payload.get("selection_value_field") or "release_speed"))
        evaluation_field = self._field(str(payload.get("evaluation_field") or selection_field))
        features_raw = payload.get("features", [])
        if not isinstance(features_raw, list) or not features_raw:
            raise RequestError("Cluster comparison requires movement/shape feature fields")
        features = tuple(self._field(str(field)) for field in features_raw)
        selection_function = str(payload.get("selection_function", "avg"))
        if selection_function not in {"avg", "min", "max", "sum", "median"}:
            raise RequestError("Cluster comparison selection function must be avg/min/max/sum/median")
        selection_direction = str(payload.get("selection_direction", "asc"))
        evaluation_direction = str(payload.get("evaluation_direction", "asc"))
        if selection_direction not in {"asc", "desc"} or evaluation_direction not in {"asc", "desc"}:
            raise RequestError("Cluster comparison directions must be asc or desc")
        arsenal_alias = "arsenal"

        candidate_payload = {
            "mode": "clustering",
            "filters": payload.get("filters", []),
            "input_stages": [
                {
                    "kind": "arsenal_signature",
                    "entity_fields": list(entities),
                    "pitch_field": "pitch_type",
                    "min_usage": float(payload.get("min_usage", 0.05)),
                    "alias": arsenal_alias,
                },
                {
                    "kind": "pitch_role_select",
                    "entity_fields": [arsenal_alias],
                    "pitch_field": "pitch_type",
                    "metric_kind": "field_metric",
                    "value_field": selection_field,
                    "function": selection_function,
                    "direction": selection_direction,
                    "exclude_pitch_types": [reference_pitch],
                    "rank": 1,
                    "tie_method": str(payload.get("tie_method", "row_number")),
                    "alias": "selected_role_rank",
                },
            ] + list(payload.get("candidate_stages") or []),
            "max_input_rows": int(payload.get("max_input_rows", 200_000)),
        }
        required = tuple(dict.fromkeys(
            entities + (arsenal_alias, "pitch_type", selection_field, evaluation_field) + features
        ))
        progress = _PROGRESS.get()
        if progress is not None:
            progress("numerical_prepare", 5.0, "Selecting arsenal-group pitch and preparing per-entity clustering")
        candidate_table, input_backend = self._numerical_input(candidate_payload, required)
        cluster_spec = ClusteringSpec(
            features=features,
            method=str(payload.get("method", "kmeans")),
            clusters=int(payload.get("clusters", 3)),
            standardize=bool(payload.get("standardize", True)),
            seed=int(payload.get("seed", 42)),
            id_fields=(arsenal_alias, "pitch_type", evaluation_field),
            partition_fields=entities,
            assignment_limit=0,
        )
        if cluster_spec.method not in {"kmeans", "gmm"}:
            raise RequestError("Cluster comparison method must be kmeans or gmm")
        clustered = NumericalExecutor().cluster_table(candidate_table, cluster_spec, progress)

        cluster_values: dict[tuple[tuple[Any, ...], int], list[float]] = {}
        cluster_rows: dict[tuple[tuple[Any, ...], int], list[dict[str, Any]]] = {}
        for row in clustered.assignments.rows:
            value = row.get(evaluation_field)
            try:
                number = float(value)
            except (TypeError, ValueError):
                continue
            if not np.isfinite(number):
                continue
            entity_key = tuple(row.get(field) for field in entities)
            key = (entity_key, int(row["cluster"]))
            cluster_values.setdefault(key, []).append(number)
            cluster_rows.setdefault(key, []).append(row)
        if not cluster_values:
            raise RequestError("No complete evaluation values remain after clustering")

        best: dict[tuple[Any, ...], tuple[int, float, list[dict[str, Any]]]] = {}
        for (entity_key, cluster), values in cluster_values.items():
            score = float(np.mean(values))
            current = best.get(entity_key)
            better = current is None or (score < current[1] if evaluation_direction == "asc" else score > current[1])
            if better:
                best[entity_key] = (cluster, score, cluster_rows[(entity_key, cluster)])

        reference_filters = list(payload.get("filters") or []) + [
            {"field": "pitch_type", "op": "eq", "value": reference_pitch}
        ]
        reference_source = self._filter_source(reference_filters)
        reference_node = Aggregate(
            reference_source,
            tuple(NamedExpr(field, Column(field)) for field in entities),
            (
                Metric("reference_sample_size", "count"),
                Metric("reference_value", "avg", Column(evaluation_field)),
            ),
            Grain(entities, "cluster_reference"),
        )
        reference_result = self._execute(reference_node)
        reference_by_entity = {
            tuple(row.get(field) for field in entities): row for row in reference_result.get("rows", [])
        }

        comparison_rows: list[dict[str, Any]] = []
        for entity_key, (cluster, candidate_value, rows) in best.items():
            sample = rows
            arsenal_values = {row.get(arsenal_alias) for row in sample if row.get(arsenal_alias) is not None}
            pitch_values = {row.get("pitch_type") for row in sample if row.get("pitch_type") is not None}
            reference = reference_by_entity.get(entity_key, {})
            reference_value = reference.get("reference_value")
            row = {field: value for field, value in zip(entities, entity_key)}
            row.update({
                "arsenal": " | ".join(sorted(str(value) for value in arsenal_values)),
                "candidate_pitch_type": " | ".join(sorted(str(value) for value in pitch_values)),
                "best_cluster": cluster,
                "candidate_sample_size": len(sample),
                "candidate_value": candidate_value,
                "reference_pitch_type": reference_pitch,
                "reference_sample_size": reference.get("reference_sample_size"),
                "reference_value": reference_value,
                "difference": candidate_value - float(reference_value) if reference_value is not None else None,
            })
            comparison_rows.append(row)

        columns = entities + (
            "arsenal", "candidate_pitch_type", "best_cluster", "candidate_sample_size",
            "candidate_value", "reference_pitch_type", "reference_sample_size",
            "reference_value", "difference",
        )
        comparison_rows.sort(key=lambda row: tuple(str(row.get(field)) for field in entities))
        return {
            "sections": [
                {
                    "title": "最佳分群與參考球種比較 Best Cluster vs Reference Pitch",
                    "columns": list(columns),
                    "rows": comparison_rows,
                    "grain": {"keys": list(entities), "label": "entity_cluster_comparison"},
                    "row_count": len(comparison_rows),
                    "backend": "numerical",
                },
                clustered.summary.to_dict(),
            ],
            "backend": "numerical",
            "input_backend": input_backend,
            "numerical": {
                "method": "cluster_compare",
                "features": list(features),
                "partition_fields": list(entities),
                "selection_field": selection_field,
                "selection_direction": selection_direction,
                "evaluation_field": evaluation_field,
                "evaluation_direction": evaluation_direction,
                "reference_pitch_type": reference_pitch,
            },
        }