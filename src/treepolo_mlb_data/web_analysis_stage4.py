from __future__ import annotations

from typing import Any

from .analysis import (
    Binary, Boolean, Column, Grain, InList, IsNull, Limit, Literal, Metric,
    Not, OrderKey, Project, NamedExpr, PITCH_GRAIN,
)
from .analysis.feature_semantics import encode_circular_features
from .analysis.numerical import (
    BootstrapSpec, ClusteringSpec, NumericalExecutor, NumericalTable, RegressionSpec,
)
from .analysis.workflow import (
    AggregateStage, FilterStage, NthStage, OffsetStage, ProjectStage, RankStage,
    RollingStage, SortStage, TrendStage, WorkflowPlanner,
)
from .web_analysis_common import RequestError, _PROGRESS

_WORKFLOW_AGGS = {"count", "sum", "avg", "min", "max", "median", "stddev_pop", "stddev_samp"}
_WINDOW_AGGS = {"count", "sum", "avg", "min", "max"}
_OPS = {"eq": "=", "ne": "!=", "gt": ">", "ge": ">=", "lt": "<", "le": "<="}


def _literal(value: Any) -> Literal:
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.lower() in {"true", "false"}:
            return Literal(stripped.lower() == "true")
        try:
            if stripped and all(char not in stripped.lower() for char in (".", "e")):
                return Literal(int(stripped))
            if stripped:
                return Literal(float(stripped))
        except ValueError:
            pass
        return Literal(value)
    return Literal(value)


class Stage4ModesMixin:
    """User-facing Stage 4 relational workflow and numerical analysis modes."""

    def _known_workflow_field(self, name: Any, fields: tuple[str, ...]) -> str:
        field = str(name or "").strip()
        if not field or field not in fields:
            raise RequestError(f"Unknown workflow field: {field}")
        return field

    def _workflow_condition(self, spec: dict[str, Any], fields: tuple[str, ...]):
        field = self._known_workflow_field(spec.get("field"), fields)
        op = str(spec.get("op", "eq"))
        left = Column(field)
        value_field = str(spec.get("value_field") or "").strip()
        right = Column(self._known_workflow_field(value_field, fields)) if value_field else _literal(spec.get("value"))
        if op == "is_null":
            return IsNull(left)
        if op == "not_null":
            return IsNull(left, True)
        if op in {"in", "not_in"}:
            raw = spec.get("value", [])
            if isinstance(raw, str):
                raw = [item.strip() for item in raw.split(",") if item.strip()]
            if not isinstance(raw, list):
                raise RequestError("Workflow IN comparison requires a list of values")
            return InList(left, tuple(_literal(value) for value in raw), op == "not_in")
        if op not in _OPS:
            raise RequestError(f"Unsupported workflow comparison: {op}")
        return Binary(left, _OPS[op], right)

    def _workflow_order(self, raw: Any, fields: tuple[str, ...]) -> tuple[OrderKey, ...]:
        if isinstance(raw, dict):
            raw = [raw]
        if not isinstance(raw, list) or not raw:
            raise RequestError("Workflow ordered stage requires at least one order field")
        result: list[OrderKey] = []
        for item in raw:
            if not isinstance(item, dict):
                raise RequestError("Invalid workflow order item")
            field = self._known_workflow_field(item.get("field"), fields)
            result.append(OrderKey(Column(field), bool(item.get("descending", False))))
        return tuple(result)

    def _workflow_partitions(self, raw: Any, fields: tuple[str, ...]) -> tuple[str, ...]:
        if raw is None:
            return ()
        if not isinstance(raw, list):
            raise RequestError("Workflow partition fields must be a list")
        return tuple(self._known_workflow_field(field, fields) for field in raw if field)

    def _workflow_metric(self, spec: dict[str, Any], fields: tuple[str, ...], used: set[str]) -> Metric:
        function = str(spec.get("function", "count"))
        if function not in _WORKFLOW_AGGS:
            raise RequestError(f"Unsupported workflow aggregate: {function}")
        field = str(spec.get("field") or "").strip()
        if function != "count" and not field:
            raise RequestError(f"{function} requires a metric field")
        if field:
            field = self._known_workflow_field(field, fields)
        alias = str(spec.get("alias") or ("row_count" if not field else f"{function}_{field}")).strip()
        if not alias or alias in used:
            raise RequestError(f"Workflow metric alias must be unique: {alias}")
        used.add(alias)
        return Metric(alias, function, Column(field) if field else None, bool(spec.get("distinct", False)))

    def _apply_workflow_stages(self, planner: WorkflowPlanner, raw_stages: Any):
        if raw_stages in (None, []):
            return planner.state
        if not isinstance(raw_stages, list):
            raise RequestError("Workflow stages must be a list")
        for index, spec in enumerate(raw_stages, 1):
            if not isinstance(spec, dict):
                raise RequestError(f"Workflow stage {index} must be an object")
            kind = str(spec.get("kind", "")).strip()
            fields = planner.state.fields
            if kind == "aggregate":
                raw_groups = spec.get("group_by", [])
                if not isinstance(raw_groups, list):
                    raise RequestError("Workflow group_by must be a list")
                groups = tuple(self._known_workflow_field(field, fields) for field in raw_groups if field)
                used = set(groups)
                raw_metrics = spec.get("metrics", [])
                if not isinstance(raw_metrics, list):
                    raise RequestError("Workflow metrics must be a list")
                metrics = tuple(self._workflow_metric(metric, fields, used) for metric in raw_metrics)
                if not groups and not metrics:
                    raise RequestError("Workflow aggregate stage requires grouping or metrics")
                planner.apply(AggregateStage(groups, metrics))
            elif kind == "filter":
                planner.apply(FilterStage(self._workflow_condition(spec, fields)))
            elif kind == "rolling":
                function = str(spec.get("function", "avg"))
                if function not in _WINDOW_AGGS:
                    raise RequestError(f"Unsupported rolling function: {function}")
                field_raw = str(spec.get("field") or "").strip()
                field = None if function == "count" and not field_raw else self._known_workflow_field(field_raw, fields)
                planner.apply(RollingStage(
                    str(spec.get("alias") or f"rolling_{function}_{field or 'rows'}"),
                    function,
                    field,
                    self._workflow_partitions(spec.get("partition_by"), fields),
                    self._workflow_order(spec.get("order_by"), fields),
                    int(spec.get("window_size", 3)),
                ))
            elif kind in {"offset", "lag", "lead"}:
                direction = str(spec.get("direction") or (kind if kind in {"lag", "lead"} else "lag"))
                if direction not in {"lag", "lead"}:
                    raise RequestError("Workflow offset direction must be lag or lead")
                field = self._known_workflow_field(spec.get("field"), fields)
                planner.apply(OffsetStage(
                    str(spec.get("alias") or f"{direction}_{field}"), field, direction,
                    int(spec.get("offset", 1)),
                    self._workflow_partitions(spec.get("partition_by"), fields),
                    self._workflow_order(spec.get("order_by"), fields),
                ))
            elif kind == "trend":
                field = self._known_workflow_field(spec.get("field"), fields)
                direction = str(spec.get("direction", "up"))
                if direction not in {"up", "down"}:
                    raise RequestError("Workflow trend direction must be up or down")
                planner.apply(TrendStage(
                    str(spec.get("alias") or f"consecutive_{direction}_{field}"), field, direction,
                    int(spec.get("periods", 3)),
                    self._workflow_partitions(spec.get("partition_by"), fields),
                    self._workflow_order(spec.get("order_by"), fields),
                    bool(spec.get("strict", True)),
                ))
            elif kind in {"nth", "first", "last"}:
                planner.apply(NthStage(
                    self._workflow_partitions(spec.get("partition_by"), fields),
                    self._workflow_order(spec.get("order_by"), fields),
                    1 if kind in {"first", "last"} else int(spec.get("n", 1)),
                    bool(spec.get("from_end", False)) or kind == "last",
                ))
            elif kind == "rank":
                method = str(spec.get("method", "row_number"))
                if method not in {"row_number", "rank", "dense_rank"}:
                    raise RequestError(f"Unsupported workflow rank method: {method}")
                planner.apply(RankStage(
                    str(spec.get("alias") or "rank"),
                    self._workflow_partitions(spec.get("partition_by"), fields),
                    self._workflow_order(spec.get("order_by"), fields),
                    method,
                    int(spec["keep_rank"]) if spec.get("keep_rank") not in (None, "") else None,
                ))
            elif kind == "project":
                raw_fields = spec.get("fields", [])
                if not isinstance(raw_fields, list) or not raw_fields:
                    raise RequestError("Workflow project stage requires fields")
                planner.apply(ProjectStage(tuple(self._known_workflow_field(field, fields) for field in raw_fields)))
            elif kind == "sort":
                planner.apply(SortStage(self._workflow_order(spec.get("order_by"), fields)))
            else:
                raise RequestError(f"Unsupported workflow stage {index}: {kind}")
        return planner.state

    def _workflow_planner(self, payload: dict[str, Any], stages_key: str = "stages") -> WorkflowPlanner:
        source = self._filter_source(payload.get("filters"))
        planner = WorkflowPlanner(source, tuple(self.schema()))
        self._apply_workflow_stages(planner, payload.get(stages_key, []))
        return planner

    def _workflow(self, payload: dict[str, Any]) -> dict[str, Any]:
        planner = self._workflow_planner(payload)
        state = planner.state
        node = self._apply_result_sort(state.node, payload, state.fields)
        limit = max(1, min(int(payload.get("limit", 500)), 5000))
        return self._execute(Limit(node, limit))

    def _numerical_input(self, payload: dict[str, Any], required_fields: tuple[str, ...]) -> tuple[NumericalTable, str]:
        planner = self._workflow_planner(payload, "input_stages")
        state = planner.state
        missing = [field for field in required_fields if field not in state.fields]
        if missing:
            raise RequestError(f"Numerical input fields are unavailable after preparation stages: {missing}")
        fields = tuple(dict.fromkeys(state.grain.keys + required_fields))
        projected = Project(state.node, tuple(NamedExpr(field, Column(field)) for field in fields), state.grain)
        max_rows = max(100, min(int(payload.get("max_input_rows", 200_000)), 1_000_000))
        guarded = self._execute(Limit(projected, max_rows + 1))
        rows = guarded.get("rows", [])
        if len(rows) > max_rows:
            raise RequestError(
                f"Numerical input exceeds safety limit of {max_rows:,} rows. Add filters/aggregation stages or raise Max Input Rows explicitly."
            )
        if not rows:
            raise RequestError("Numerical analysis input is empty")
        table = NumericalTable(tuple(guarded.get("columns", fields)), tuple(rows), state.grain)
        return table, str(guarded.get("backend") or self.analysis_backend)

    @staticmethod
    def _encode_model_features(
        table: NumericalTable,
        features: tuple[str, ...],
        *,
        label: str,
    ) -> tuple[NumericalTable, tuple[str, ...], dict[str, tuple[str, str]]]:
        try:
            return encode_circular_features(table, features)
        except ValueError as exc:
            raise RequestError(f"{label}: {exc}") from exc

    def _clustering(self, payload: dict[str, Any]) -> dict[str, Any]:
        raw_features = payload.get("features", [])
        if not isinstance(raw_features, list) or not raw_features:
            raise RequestError("Clustering requires feature fields")
        features = tuple(str(field) for field in raw_features)
        raw_ids = payload.get("id_fields", [])
        if not isinstance(raw_ids, list):
            raise RequestError("Clustering id_fields must be a list")
        id_fields = tuple(str(field) for field in raw_ids)
        if _PROGRESS.get() is not None:
            _PROGRESS.get()("numerical_prepare", 5.0, "Preparing relational input for clustering")  # type: ignore[misc]
        table, input_backend = self._numerical_input(payload, tuple(dict.fromkeys(features + id_fields)))
        table, model_features, encodings = self._encode_model_features(table, features, label="Clustering features")
        method = str(payload.get("method", "kmeans"))
        if method not in {"kmeans", "gmm"}:
            raise RequestError("Clustering method must be kmeans or gmm")
        result = NumericalExecutor().clustering(table, ClusteringSpec(
            features=model_features,
            method=method,
            clusters=int(payload.get("clusters", 3)),
            standardize=bool(payload.get("standardize", True)),
            seed=int(payload.get("seed", 42)),
            id_fields=id_fields,
            assignment_limit=max(0, min(int(payload.get("assignment_limit", 5000)), 50_000)),
        ), _PROGRESS.get())
        result["input_backend"] = input_backend
        result.setdefault("numerical", {})["requested_features"] = list(features)
        result["numerical"]["feature_encodings"] = {key: list(value) for key, value in encodings.items()}
        return result

    def _regression(self, payload: dict[str, Any]) -> dict[str, Any]:
        dependent = str(payload.get("dependent") or "").strip()
        raw_independent = payload.get("independent", [])
        if not dependent or not isinstance(raw_independent, list) or not raw_independent:
            raise RequestError("Regression requires dependent and independent fields")
        independent = tuple(str(field) for field in raw_independent)
        if _PROGRESS.get() is not None:
            _PROGRESS.get()("numerical_prepare", 5.0, "Preparing relational input for regression")  # type: ignore[misc]
        table, input_backend = self._numerical_input(payload, tuple(dict.fromkeys(independent + (dependent,))))
        table, model_independent, encodings = self._encode_model_features(table, independent, label="Regression predictors")
        model = str(payload.get("model", "linear"))
        if model not in {"linear", "logistic"}:
            raise RequestError("Regression model must be linear or logistic")
        result = NumericalExecutor().regression(table, RegressionSpec(
            dependent=dependent,
            independent=model_independent,
            model=model,
            standardize_predictors=bool(payload.get("standardize_predictors", False)),
            confidence=float(payload.get("confidence", 0.95)),
        ), _PROGRESS.get())
        result["input_backend"] = input_backend
        result.setdefault("numerical", {})["requested_independent"] = list(independent)
        result["numerical"]["feature_encodings"] = {key: list(value) for key, value in encodings.items()}
        return result

    def _bootstrap(self, payload: dict[str, Any]) -> dict[str, Any]:
        value_field = str(payload.get("value_field") or "").strip()
        raw_units = payload.get("resample_unit_fields", [])
        if not value_field or not isinstance(raw_units, list) or not raw_units:
            raise RequestError("Bootstrap requires a value field and explicit resampling unit fields")
        units = tuple(str(field) for field in raw_units)
        group_field = str(payload.get("group_field") or "").strip() or None
        required = (value_field,) + units + ((group_field,) if group_field else ())
        if _PROGRESS.get() is not None:
            _PROGRESS.get()("numerical_prepare", 5.0, "Preparing relational input for bootstrap")  # type: ignore[misc]
        table, input_backend = self._numerical_input(payload, tuple(dict.fromkeys(required)))
        statistic = str(payload.get("statistic", "mean"))
        if statistic not in {"mean", "median", "proportion"}:
            raise RequestError("Bootstrap statistic must be mean, median, or proportion")
        result = NumericalExecutor().bootstrap(table, BootstrapSpec(
            value_field=value_field,
            resample_unit_fields=units,
            statistic=statistic,
            group_field=group_field,
            group_a=payload.get("group_a"),
            group_b=payload.get("group_b"),
            success_value=payload.get("success_value", 1),
            iterations=int(payload.get("iterations", 2000)),
            confidence=float(payload.get("confidence", 0.95)),
            seed=int(payload.get("seed", 42)),
        ), _PROGRESS.get())
        result["input_backend"] = input_backend
        return result
