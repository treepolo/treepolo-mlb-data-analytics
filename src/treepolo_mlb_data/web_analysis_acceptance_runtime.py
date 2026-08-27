from __future__ import annotations

from typing import Any

import numpy as np

from .analysis import (
    Aggregate, Binary, Boolean, Column, Filter, Grain, InList, IsNull, Limit,
    Literal, Metric, NamedExpr, Project, Window, WindowField, pitch_usage,
    rank_pitch_roles,
)
from .analysis.numerical import ClusteringSpec, NumericalExecutor, NumericalTable
from .analysis.workflow import WorkflowPlanner, WorkflowState
from .web_analysis_common import RequestError, _PROGRESS


class AcceptanceRuntimeFixesMixin:
    """Runtime refinements for the acceptance batch.

    Relative-pitch selection avoids duplicated deep SQL trees, while the
    multi-stage cluster comparison is deliberately checkpointed into several
    compact relational queries before numerical clustering. This keeps the
    typed relational semantics but avoids SQLite parser-depth failures caused
    by repeatedly inlining the same arsenal/selector subtrees.
    """

    @staticmethod
    def _runtime_and(*terms):
        terms = tuple(term for term in terms if term is not None)
        if not terms:
            raise RequestError("At least one predicate is required")
        return terms[0] if len(terms) == 1 else Boolean("and", terms)

    @staticmethod
    def _runtime_or(*terms):
        terms = tuple(term for term in terms if term is not None)
        if not terms:
            raise RequestError("At least one predicate is required")
        return terms[0] if len(terms) == 1 else Boolean("or", terms)

    def _runtime_entity_membership(self, entities: tuple[str, ...], keys: list[tuple[Any, ...]]):
        if not keys:
            raise RequestError("Entity membership requires at least one entity")
        if len(entities) == 1:
            field = entities[0]
            normal = [key[0] for key in keys if key[0] is not None]
            predicates = []
            if normal:
                predicates.append(InList(Column(field), tuple(Literal(value) for value in normal)))
            if any(key[0] is None for key in keys):
                predicates.append(IsNull(Column(field)))
            return self._runtime_or(*predicates)

        row_predicates = []
        for key in keys:
            terms = []
            for field, value in zip(entities, key):
                terms.append(IsNull(Column(field)) if value is None else Binary(Column(field), "=", Literal(value)))
            row_predicates.append(self._runtime_and(*terms))
        return self._runtime_or(*row_predicates)

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

    def _cluster_compare(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Checkpointed implementation of the multi-stage cluster comparison.

        Semantics remain: build an arsenal signature per entity, group entities
        sharing that signature, choose the best eligible non-reference pitch
        inside each arsenal group, cluster that pitch independently per entity,
        then compare the best cluster with the same entity's reference pitch.
        """
        schema = self.schema()
        schema_fields = tuple(schema)
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
        tie_method = str(payload.get("tie_method", "row_number"))
        if tie_method not in {"row_number", "rank", "dense_rank"}:
            raise RequestError("Unsupported cluster-comparison tie handling method")
        min_usage = float(payload.get("min_usage", 0.05))
        if not 0 <= min_usage <= 1:
            raise RequestError("Cluster comparison minimum usage must be between 0 and 1")
        arsenal_alias = "arsenal"

        progress = _PROGRESS.get()
        if progress is not None:
            progress("numerical_prepare", 3.0, "Computing per-entity pitch usage and arsenal signatures")

        base_source = self._filter_source(payload.get("filters", []))
        usage_result = self._execute(pitch_usage(base_source, entity_fields=entities, pitch_field="pitch_type"))
        usage_rows = usage_result.get("rows", [])
        if not usage_rows:
            raise RequestError("Cluster comparison has no classified pitches after filtering")
        input_backends = {str(usage_result.get("backend"))} if usage_result.get("backend") else set()

        pitches_by_entity: dict[tuple[Any, ...], list[tuple[str, float]]] = {}
        for row in usage_rows:
            pitch_type = row.get("pitch_type")
            if pitch_type is None:
                continue
            try:
                usage_rate = float(row.get("usage_rate"))
            except (TypeError, ValueError):
                continue
            key = tuple(row.get(field) for field in entities)
            pitches_by_entity.setdefault(key, []).append((str(pitch_type), usage_rate))

        arsenal_by_entity: dict[tuple[Any, ...], str] = {}
        entities_by_arsenal: dict[str, list[tuple[Any, ...]]] = {}
        for entity_key, pitch_rows in pitches_by_entity.items():
            eligible = sorted({pitch for pitch, rate in pitch_rows if rate > min_usage})
            if not eligible:
                continue
            signature = "|".join(eligible)
            arsenal_by_entity[entity_key] = signature
            entities_by_arsenal.setdefault(signature, []).append(entity_key)
        if not entities_by_arsenal:
            raise RequestError("No entity has an arsenal meeting the minimum-usage threshold")

        if progress is not None:
            progress("numerical_prepare", 10.0, "Selecting the best eligible non-reference pitch inside each arsenal group")

        selected_by_arsenal: dict[str, tuple[str, ...]] = {}
        for signature, entity_keys in entities_by_arsenal.items():
            eligible_types = tuple(pitch for pitch in signature.split("|") if pitch and pitch != reference_pitch)
            if not eligible_types:
                continue
            source = Filter(
                base_source,
                self._runtime_and(
                    self._runtime_entity_membership(entities, entity_keys),
                    InList(Column("pitch_type"), tuple(Literal(pitch) for pitch in eligible_types)),
                ),
            )
            metric_node = Aggregate(
                source,
                (NamedExpr("pitch_type", Column("pitch_type")),),
                (Metric("role_metric", selection_function, Column(selection_field)),),
                Grain(("pitch_type",), "cluster_candidate_metric"),
            )
            metric_result = self._execute(metric_node)
            if metric_result.get("backend"):
                input_backends.add(str(metric_result["backend"]))
            scored: list[tuple[str, float]] = []
            for row in metric_result.get("rows", []):
                try:
                    score = float(row.get("role_metric"))
                except (TypeError, ValueError):
                    continue
                if np.isfinite(score) and row.get("pitch_type") is not None:
                    scored.append((str(row["pitch_type"]), score))
            if not scored:
                continue
            best_score = min(score for _, score in scored) if selection_direction == "asc" else max(score for _, score in scored)
            tied = sorted(pitch for pitch, score in scored if score == best_score)
            selected_by_arsenal[signature] = (tied[0],) if tie_method == "row_number" else tuple(tied)
        if not selected_by_arsenal:
            raise RequestError("No eligible non-reference candidate pitch remains after selection")

        if progress is not None:
            progress("numerical_prepare", 18.0, "Materializing selected candidate pitches for per-entity clustering")

        max_rows = max(100, min(int(payload.get("max_input_rows", 200_000)), 1_000_000))
        candidate_stages = payload.get("candidate_stages") or []
        if not isinstance(candidate_stages, list):
            raise RequestError("candidate_stages must be a list")
        required = tuple(dict.fromkeys(entities + (arsenal_alias, "pitch_type", selection_field, evaluation_field) + features))
        candidate_rows: list[dict[str, Any]] = []
        candidate_columns: tuple[str, ...] | None = None
        candidate_grain: Grain | None = None

        for signature, selected_types in selected_by_arsenal.items():
            entity_keys = entities_by_arsenal[signature]
            filtered = Filter(
                base_source,
                self._runtime_and(
                    self._runtime_entity_membership(entities, entity_keys),
                    InList(Column("pitch_type"), tuple(Literal(pitch) for pitch in selected_types)),
                ),
            )
            annotated = Project(
                filtered,
                tuple(NamedExpr(field, Column(field)) for field in schema_fields)
                + (NamedExpr(arsenal_alias, Literal(signature)),),
                Grain(("pitch_uid",), "pitch"),
            )
            planner = WorkflowPlanner(annotated, schema_fields + (arsenal_alias,))
            self._apply_workflow_stages(planner, candidate_stages)
            state = planner.state
            missing = [field for field in required if field not in state.fields]
            if missing:
                raise RequestError(f"Cluster-comparison candidate fields are unavailable after preparation stages: {missing}")
            fields = tuple(dict.fromkeys(state.grain.keys + required))
            projected = Project(state.node, tuple(NamedExpr(field, Column(field)) for field in fields), state.grain)
            remaining = max_rows - len(candidate_rows)
            if remaining <= 0:
                raise RequestError(
                    f"Numerical input exceeds safety limit of {max_rows:,} rows. Add filters or raise Max Input Rows explicitly."
                )
            result = self._execute(Limit(projected, remaining + 1))
            if result.get("backend"):
                input_backends.add(str(result["backend"]))
            rows = list(result.get("rows", []))
            if len(rows) > remaining:
                raise RequestError(
                    f"Numerical input exceeds safety limit of {max_rows:,} rows. Add filters or raise Max Input Rows explicitly."
                )
            if candidate_columns is None:
                candidate_columns = tuple(result.get("columns", fields))
                candidate_grain = state.grain
            elif tuple(result.get("columns", fields)) != candidate_columns or state.grain != candidate_grain:
                raise RequestError("Candidate preparation stages produced incompatible schemas across arsenal groups")
            candidate_rows.extend(rows)

        if not candidate_rows or candidate_columns is None or candidate_grain is None:
            raise RequestError("No candidate pitch rows remain for clustering")
        candidate_table = NumericalTable(candidate_columns, tuple(candidate_rows), candidate_grain)
        input_backend = "+".join(sorted(input_backends)) if input_backends else self.analysis_backend

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
        try:
            clustered = NumericalExecutor().cluster_table(candidate_table, cluster_spec, progress)
        except ValueError as exc:
            details = []
            for signature, selected_types in selected_by_arsenal.items():
                for entity_key in entities_by_arsenal[signature]:
                    label = ", ".join(f"{field}={value!r}" for field, value in zip(entities, entity_key))
                    details.append(f"{label}: {'|'.join(selected_types)}")
                    if len(details) >= 10:
                        break
                if len(details) >= 10:
                    break
            suffix = "; selected candidate(s): " + "; ".join(details) if details else ""
            raise RequestError(f"{exc}{suffix}") from exc

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

        reference_source = Filter(base_source, Binary(Column("pitch_type"), "=", Literal(reference_pitch)))
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
        if reference_result.get("backend"):
            input_backends.add(str(reference_result["backend"]))
            input_backend = "+".join(sorted(input_backends))
        reference_by_entity = {
            tuple(row.get(field) for field in entities): row for row in reference_result.get("rows", [])
        }

        comparison_rows: list[dict[str, Any]] = []
        for entity_key, (cluster, candidate_value, rows) in best.items():
            pitch_values = {row.get("pitch_type") for row in rows if row.get("pitch_type") is not None}
            reference = reference_by_entity.get(entity_key, {})
            reference_value = reference.get("reference_value")
            row = {field: value for field, value in zip(entities, entity_key)}
            row.update({
                "arsenal": arsenal_by_entity.get(entity_key, ""),
                "candidate_pitch_type": " | ".join(sorted(str(value) for value in pitch_values)),
                "best_cluster": cluster,
                "candidate_sample_size": len(rows),
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
