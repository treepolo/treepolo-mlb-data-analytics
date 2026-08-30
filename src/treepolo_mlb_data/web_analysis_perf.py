from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from os import cpu_count
from time import perf_counter
from typing import Any

from .analysis._lazy_scientific import np
from .analysis import (
    Aggregate, Column, Filter, Grain, InList, IsNull, Limit, Literal, Metric,
    NamedExpr, Project,
)
from .analysis.feature_semantics import complete_partition_counts
from .analysis.numerical import (
    ClusteringOutput, ClusteringSpec, NumericalExecutor, NumericalSection, NumericalTable,
)
from .web_analysis_common import RequestError, _PROGRESS


class PerformanceClusterCompareMixin:
    """Fast path for the common Cluster Comparison workload.

    With no candidate preparation stages, the analysis can preserve the exact
    research semantics while reducing database work to two bounded queries:
    one entity/pitch aggregate query and one selected-candidate materialization
    query. Candidate selection and reference statistics are then composed from
    the aggregate checkpoint in memory. Custom candidate stages retain the
    checkpointed legacy path because arbitrary workflow transforms must remain
    relationally exact.
    """

    @staticmethod
    def _perf_elapsed_ms(started: float) -> float:
        return round((perf_counter() - started) * 1000.0, 3)

    @staticmethod
    def _perf_combine_metric(
        values: list[tuple[float, int]],
        function: str,
    ) -> float | None:
        if not values:
            return None
        if function == "avg":
            denominator = sum(count for _, count in values)
            if denominator <= 0:
                return None
            return float(sum(value * count for value, count in values) / denominator)
        if function == "sum":
            return float(sum(value for value, _ in values))
        if function == "min":
            return float(min(value for value, _ in values))
        if function == "max":
            return float(max(value for value, _ in values))
        return None

    def _perf_cluster_partitions(
        self,
        table: NumericalTable,
        spec: ClusteringSpec,
        entities: tuple[str, ...],
        progress,
        requested_workers: Any,
    ) -> tuple[ClusteringOutput, int]:
        groups: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
        for row in table.rows:
            key = tuple(row.get(field) for field in entities)
            if any(value is None for value in key):
                continue
            groups.setdefault(key, []).append(row)
        if not groups:
            raise RequestError("No clustering partitions remain after filtering")

        available_cpu = max(1, int(cpu_count() or 1))
        default_workers = min(4, available_cpu, len(groups))
        try:
            workers = int(requested_workers) if requested_workers not in (None, "") else default_workers
        except (TypeError, ValueError):
            workers = default_workers
        workers = max(1, min(workers, 8, available_cpu, len(groups)))

        single_spec = ClusteringSpec(
            features=spec.features,
            method=spec.method,
            clusters=spec.clusters,
            standardize=spec.standardize,
            seed=spec.seed,
            id_fields=tuple(dict.fromkeys(spec.id_fields + entities)),
            partition_fields=(),
            assignment_limit=0,
        )

        def fit_partition(item: tuple[tuple[Any, ...], list[dict[str, Any]]]):
            key, rows = item
            partition_table = NumericalTable(table.columns, tuple(rows), table.grain)
            output = NumericalExecutor().cluster_table(partition_table, single_spec, None)
            return key, output

        outputs: list[tuple[tuple[Any, ...], ClusteringOutput]] = []
        items = list(groups.items())
        if workers == 1 or len(items) < 3:
            for index, item in enumerate(items, 1):
                outputs.append(fit_partition(item))
                if progress is not None:
                    progress(
                        "numerical_compute",
                        20.0 + 55.0 * index / max(1, len(items)),
                        f"Clustering partition {index}/{len(items)}",
                    )
        else:
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="cluster-compare") as executor:
                future_map = {executor.submit(fit_partition, item): item[0] for item in items}
                completed = 0
                for future in as_completed(future_map):
                    outputs.append(future.result())
                    completed += 1
                    if progress is not None:
                        progress(
                            "numerical_compute",
                            20.0 + 55.0 * completed / max(1, len(items)),
                            f"Clustering partitions {completed}/{len(items)} with {workers} workers",
                        )

        outputs.sort(key=lambda item: tuple(str(value) for value in item[0]))
        summary_rows: list[dict[str, Any]] = []
        assignment_rows: list[dict[str, Any]] = []
        probability_field: str | None = None
        for entity_key, output in outputs:
            probability_field = probability_field or output.probability_field
            for source in output.summary.rows:
                row = {field: value for field, value in zip(entities, entity_key)}
                row.update(source)
                summary_rows.append(row)
            assignment_rows.extend(dict(row) for row in output.assignments.rows)

        summary_columns = entities + ("cluster", "sample_size") + tuple(
            name
            for field in spec.features
            for name in (f"center_{field}", f"mean_{field}", f"std_{field}")
        )
        summary = NumericalSection(
            "分群摘要 Cluster Summary",
            summary_columns,
            tuple(summary_rows),
            Grain(entities + ("cluster",), "cluster"),
        )
        assignment_columns = table.columns + ("cluster",) + (("cluster_probability",) if probability_field else ())
        assignments = NumericalTable(assignment_columns, tuple(assignment_rows), table.grain)
        return ClusteringOutput(summary, assignments, probability_field), workers

    def _cluster_compare(self, payload: dict[str, Any]) -> dict[str, Any]:
        candidate_stages = payload.get("candidate_stages") or []
        if candidate_stages:
            # Arbitrary preparation stages can alter grain and fields. Preserve
            # the established relational implementation for that advanced path.
            return super()._cluster_compare(payload)
        if not isinstance(candidate_stages, list):
            raise RequestError("candidate_stages must be a list")

        total_started = perf_counter()
        timings: dict[str, float] = {}
        progress = _PROGRESS.get()

        raw_entities = payload.get("entity_fields", ["pitcher"])
        if not isinstance(raw_entities, list) or not raw_entities:
            raise RequestError("Cluster comparison requires entity fields")
        entities = tuple(self._field(str(field)) for field in raw_entities if field)
        if not entities:
            raise RequestError("Cluster comparison requires at least one entity field")

        reference_pitch = str(payload.get("reference_pitch_type", "FF") or "FF")
        selection_field = self._field(str(payload.get("selection_value_field") or "release_speed"))
        evaluation_field = self._field(str(payload.get("evaluation_field") or selection_field))
        raw_features = payload.get("features", [])
        if not isinstance(raw_features, list) or not raw_features:
            raise RequestError("Cluster comparison requires movement/shape feature fields")
        features = tuple(self._field(str(field)) for field in raw_features)

        selection_function = str(payload.get("selection_function", "avg"))
        if selection_function not in {"avg", "min", "max", "sum", "median"}:
            raise RequestError("Cluster comparison selection function must be avg/min/max/sum/median")
        # Median cannot be composed exactly from per-entity medians. Keep the
        # established path rather than introducing an approximation.
        if selection_function == "median":
            return super()._cluster_compare(payload)

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
        max_rows = max(100, min(int(payload.get("max_input_rows", 200_000)), 1_000_000))
        arsenal_alias = "arsenal"
        base_source = self._filter_source(payload.get("filters", []))

        if progress is not None:
            progress("numerical_prepare", 3.0, "Aggregating pitch usage, candidate metrics, and reference values")

        query_started = perf_counter()
        clean_source = Filter(base_source, IsNull(Column("pitch_type"), True))
        grouping = entities + ("pitch_type",)
        aggregate_node = Aggregate(
            clean_source,
            tuple(NamedExpr(field, Column(field)) for field in grouping),
            (
                Metric("__ta_pitch_count", "count"),
                Metric("__ta_selection_count", "count", Column(selection_field)),
                Metric("__ta_selection_metric", selection_function, Column(selection_field)),
                Metric("__ta_evaluation_value", "avg", Column(evaluation_field)),
            ),
            Grain(grouping, "cluster_compare_checkpoint"),
        )
        aggregate_result = self._execute(aggregate_node)
        aggregate_rows = list(aggregate_result.get("rows", []))
        timings["aggregate_query_ms"] = self._perf_elapsed_ms(query_started)
        if not aggregate_rows:
            raise RequestError("Cluster comparison has no classified pitches after filtering")
        input_backends = {str(aggregate_result.get("backend"))} if aggregate_result.get("backend") else set()

        totals_by_entity: dict[tuple[Any, ...], int] = {}
        counts_by_entity: dict[tuple[Any, ...], list[tuple[str, int]]] = {}
        aggregate_by_entity_pitch: dict[tuple[tuple[Any, ...], str], dict[str, Any]] = {}
        for row in aggregate_rows:
            pitch_type = row.get("pitch_type")
            if pitch_type is None:
                continue
            entity_key = tuple(row.get(field) for field in entities)
            try:
                count = int(row.get("__ta_pitch_count") or 0)
            except (TypeError, ValueError):
                count = 0
            if count <= 0:
                continue
            pitch = str(pitch_type)
            totals_by_entity[entity_key] = totals_by_entity.get(entity_key, 0) + count
            counts_by_entity.setdefault(entity_key, []).append((pitch, count))
            aggregate_by_entity_pitch[(entity_key, pitch)] = row

        arsenal_by_entity: dict[tuple[Any, ...], str] = {}
        entities_by_arsenal: dict[str, list[tuple[Any, ...]]] = {}
        for entity_key, pitch_counts in counts_by_entity.items():
            total = totals_by_entity.get(entity_key, 0)
            if total <= 0:
                continue
            eligible = sorted({pitch for pitch, count in pitch_counts if (count / total) > min_usage})
            if not eligible:
                continue
            signature = "|".join(eligible)
            arsenal_by_entity[entity_key] = signature
            entities_by_arsenal.setdefault(signature, []).append(entity_key)
        if not entities_by_arsenal:
            raise RequestError("No entity has an arsenal meeting the minimum-usage threshold")

        if progress is not None:
            progress("numerical_prepare", 10.0, "Selecting best eligible pitch from the aggregate checkpoint")

        selection_started = perf_counter()
        selected_by_arsenal: dict[str, tuple[str, ...]] = {}
        reference_by_entity: dict[tuple[Any, ...], dict[str, Any]] = {}
        for entity_key in arsenal_by_entity:
            reference = aggregate_by_entity_pitch.get((entity_key, reference_pitch))
            if reference is not None:
                reference_by_entity[entity_key] = {
                    "reference_sample_size": reference.get("__ta_pitch_count"),
                    "reference_value": reference.get("__ta_evaluation_value"),
                }

        for signature, entity_keys in entities_by_arsenal.items():
            eligible_types = tuple(pitch for pitch in signature.split("|") if pitch and pitch != reference_pitch)
            scored: list[tuple[str, float]] = []
            for pitch in eligible_types:
                parts: list[tuple[float, int]] = []
                for entity_key in entity_keys:
                    row = aggregate_by_entity_pitch.get((entity_key, pitch))
                    if row is None:
                        continue
                    try:
                        metric = float(row.get("__ta_selection_metric"))
                    except (TypeError, ValueError):
                        continue
                    if not np.isfinite(metric):
                        continue
                    count_field = "__ta_selection_count" if selection_function == "avg" else "__ta_pitch_count"
                    try:
                        weight = int(row.get(count_field) or 0)
                    except (TypeError, ValueError):
                        weight = 0
                    if selection_function == "avg" and weight <= 0:
                        continue
                    parts.append((metric, weight))
                combined = self._perf_combine_metric(parts, selection_function)
                if combined is not None and np.isfinite(combined):
                    scored.append((pitch, combined))
            if not scored:
                continue
            best_score = min(score for _, score in scored) if selection_direction == "asc" else max(score for _, score in scored)
            tied = sorted(pitch for pitch, score in scored if score == best_score)
            selected_by_arsenal[signature] = (tied[0],) if tie_method == "row_number" else tuple(tied)
        timings["candidate_selection_ms"] = self._perf_elapsed_ms(selection_started)
        if not selected_by_arsenal:
            raise RequestError("No eligible non-reference candidate pitch remains after selection")

        if progress is not None:
            progress("numerical_prepare", 18.0, "Materializing all selected candidate pitches in one query")

        materialize_started = perf_counter()
        entities_by_selected_types: dict[tuple[str, ...], list[tuple[Any, ...]]] = {}
        for signature, selected_types in selected_by_arsenal.items():
            entities_by_selected_types.setdefault(selected_types, []).extend(entities_by_arsenal[signature])

        predicates = []
        for selected_types, entity_keys in entities_by_selected_types.items():
            predicates.append(
                self._runtime_and(
                    self._runtime_entity_membership(entities, entity_keys),
                    InList(Column("pitch_type"), tuple(Literal(pitch) for pitch in selected_types)),
                )
            )
        selected_source = Filter(base_source, self._runtime_or(*predicates))
        raw_fields = tuple(dict.fromkeys(("pitch_uid",) + entities + ("pitch_type", selection_field, evaluation_field) + features))
        projected = Project(
            selected_source,
            tuple(NamedExpr(field, Column(field)) for field in raw_fields),
            Grain(("pitch_uid",), "pitch"),
        )
        candidate_result = self._execute(Limit(projected, max_rows + 1))
        if candidate_result.get("backend"):
            input_backends.add(str(candidate_result["backend"]))
        rows = list(candidate_result.get("rows", []))
        if len(rows) > max_rows:
            raise RequestError(
                f"Numerical input exceeds safety limit of {max_rows:,} rows. Add filters or raise Max Input Rows explicitly."
            )
        candidate_rows: list[dict[str, Any]] = []
        for source_row in rows:
            entity_key = tuple(source_row.get(field) for field in entities)
            signature = arsenal_by_entity.get(entity_key)
            if not signature:
                continue
            row = dict(source_row)
            row[arsenal_alias] = signature
            candidate_rows.append(row)
        timings["candidate_materialization_ms"] = self._perf_elapsed_ms(materialize_started)
        if not candidate_rows:
            raise RequestError("No candidate pitch rows remain for clustering")

        candidate_columns = raw_fields + (arsenal_alias,)
        candidate_table = NumericalTable(candidate_columns, tuple(candidate_rows), Grain(("pitch_uid",), "pitch"))
        input_backend = "+".join(sorted(input_backends)) if input_backends else self.analysis_backend
        candidate_table, model_features, encodings = self._encode_model_features(
            candidate_table,
            features,
            label="Cluster comparison features",
        )

        requested_clusters = int(payload.get("clusters", 3))
        if not 2 <= requested_clusters <= 50:
            raise RequestError("Cluster comparison cluster count must be between 2 and 50")
        complete_counts = complete_partition_counts(
            candidate_table,
            features=model_features,
            partition_fields=entities,
        )
        eligible_entity_keys = {key for key, count in complete_counts.items() if count >= requested_clusters}
        skipped_rows: list[dict[str, Any]] = []
        for entity_key, count in complete_counts.items():
            if count >= requested_clusters:
                continue
            skipped = {field: value for field, value in zip(entities, entity_key)}
            skipped.update({
                "complete_rows": count,
                "requested_clusters": requested_clusters,
                "reason": "complete rows below requested cluster count",
            })
            skipped_rows.append(skipped)
        if not eligible_entity_keys:
            raise RequestError(
                "No entity has enough complete feature rows for the requested cluster count. "
                "Reduce Clusters per Entity, use fewer/more complete features, or add data."
            )
        candidate_table = NumericalTable(
            candidate_table.columns,
            tuple(
                row for row in candidate_table.rows
                if tuple(row.get(field) for field in entities) in eligible_entity_keys
            ),
            candidate_table.grain,
        )

        cluster_spec = ClusteringSpec(
            features=model_features,
            method=str(payload.get("method", "kmeans")),
            clusters=requested_clusters,
            standardize=bool(payload.get("standardize", True)),
            seed=int(payload.get("seed", 42)),
            id_fields=(arsenal_alias, "pitch_type", evaluation_field),
            partition_fields=entities,
            assignment_limit=0,
        )
        if cluster_spec.method not in {"kmeans", "gmm"}:
            raise RequestError("Cluster comparison method must be kmeans or gmm")

        clustering_started = perf_counter()
        try:
            clustered, workers = self._perf_cluster_partitions(
                candidate_table,
                cluster_spec,
                entities,
                progress,
                payload.get("cluster_workers"),
            )
        except ValueError as exc:
            raise RequestError(str(exc)) from exc
        timings["numerical_clustering_ms"] = self._perf_elapsed_ms(clustering_started)

        post_started = perf_counter()
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

        comparison_rows: list[dict[str, Any]] = []
        for entity_key, (cluster, candidate_value, cluster_sample) in best.items():
            pitch_values = {row.get("pitch_type") for row in cluster_sample if row.get("pitch_type") is not None}
            reference = reference_by_entity.get(entity_key, {})
            reference_value = reference.get("reference_value")
            row = {field: value for field, value in zip(entities, entity_key)}
            row.update({
                "arsenal": arsenal_by_entity.get(entity_key, ""),
                "candidate_pitch_type": " | ".join(sorted(str(value) for value in pitch_values)),
                "best_cluster": cluster,
                "candidate_sample_size": len(cluster_sample),
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
        skipped_rows.sort(key=lambda row: tuple(str(row.get(field)) for field in entities))
        timings["postprocess_ms"] = self._perf_elapsed_ms(post_started)
        timings["total_ms"] = self._perf_elapsed_ms(total_started)

        sections = [
            {
                "title": "最佳分群與參考球種比較 Best Cluster vs Reference Pitch",
                "columns": list(columns),
                "rows": comparison_rows,
                "grain": {"keys": list(entities), "label": "entity_cluster_comparison"},
                "row_count": len(comparison_rows),
                "backend": "numerical",
            },
            clustered.summary.to_dict(),
        ]
        if skipped_rows:
            skipped_columns = entities + ("complete_rows", "requested_clusters", "reason")
            sections.append({
                "title": "略過的分析個體 Skipped Entities",
                "columns": list(skipped_columns),
                "rows": skipped_rows,
                "grain": {"keys": list(entities), "label": "skipped_cluster_entities"},
                "row_count": len(skipped_rows),
                "backend": "numerical",
            })

        return {
            "sections": sections,
            "backend": "numerical",
            "input_backend": input_backend,
            "numerical": {
                "method": "cluster_compare",
                "requested_features": list(features),
                "features": list(model_features),
                "feature_encodings": {key: list(value) for key, value in encodings.items()},
                "partition_fields": list(entities),
                "selection_field": selection_field,
                "selection_direction": selection_direction,
                "evaluation_field": evaluation_field,
                "evaluation_direction": evaluation_direction,
                "reference_pitch_type": reference_pitch,
                "skipped_entities": len(skipped_rows),
                "performance": {
                    "fast_path": True,
                    "database_queries": 2,
                    "cluster_workers": workers,
                    "arsenal_signatures": len(entities_by_arsenal),
                    "candidate_rows": len(candidate_table.rows),
                    "timings_ms": timings,
                },
            },
        }
