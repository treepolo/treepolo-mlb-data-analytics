from __future__ import annotations

from typing import Any

from .analysis import Binary, Case, Column, Literal, Metric
from .analysis.workflow import DerivedStage, WorkflowPlanner
from .web_analysis_common import RequestError
from .web_analysis_stage4 import Stage4ModesMixin, _literal


class Stage4ExtendedModesMixin(Stage4ModesMixin):
    """Complete workflow composition with conditional metrics and derived arithmetic."""

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

    def _apply_workflow_stages(self, planner: WorkflowPlanner, raw_stages: Any):
        if raw_stages in (None, []):
            return planner.state
        if not isinstance(raw_stages, list):
            raise RequestError("Workflow stages must be a list")
        for index, spec in enumerate(raw_stages, 1):
            if not isinstance(spec, dict):
                raise RequestError(f"Workflow stage {index} must be an object")
            if str(spec.get("kind", "")).strip() != "derive":
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
            # such as pitch usage (conditional count / total count) must therefore
            # force a real-valued numerator so both relational backends agree.
            left_expr = Column(left)
            if op == "/":
                left_expr = Binary(left_expr, "*", Literal(1.0))
            planner.apply(DerivedStage(alias, Binary(left_expr, op, right)))
        return planner.state
