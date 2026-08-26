from __future__ import annotations

from dataclasses import dataclass
from typing import Literal as TypingLiteral

from .model import (
    Aggregate, Binary, Boolean, Case, Column, Expr, Filter, Grain, Literal,
    Metric, NamedExpr, Node, OrderKey, Project, Rank, Sort, Window,
    WindowField, WindowFrame, output_grain,
)


@dataclass(frozen=True, slots=True)
class WorkflowState:
    node: Node
    fields: tuple[str, ...]
    grain: Grain


@dataclass(frozen=True, slots=True)
class AggregateStage:
    group_by: tuple[str, ...]
    metrics: tuple[Metric, ...]


@dataclass(frozen=True, slots=True)
class FilterStage:
    predicate: Expr


@dataclass(frozen=True, slots=True)
class DerivedStage:
    alias: str
    expr: Expr


@dataclass(frozen=True, slots=True)
class RollingStage:
    alias: str
    function: TypingLiteral["sum", "avg", "count", "min", "max"]
    field: str | None
    partition_by: tuple[str, ...]
    order_by: tuple[OrderKey, ...]
    window_size: int


@dataclass(frozen=True, slots=True)
class OffsetStage:
    alias: str
    field: str
    direction: TypingLiteral["lag", "lead"]
    offset: int
    partition_by: tuple[str, ...]
    order_by: tuple[OrderKey, ...]


@dataclass(frozen=True, slots=True)
class TrendStage:
    alias: str
    field: str
    direction: TypingLiteral["up", "down"]
    periods: int
    partition_by: tuple[str, ...]
    order_by: tuple[OrderKey, ...]
    strict: bool = True


@dataclass(frozen=True, slots=True)
class NthStage:
    partition_by: tuple[str, ...]
    order_by: tuple[OrderKey, ...]
    n: int = 1
    from_end: bool = False


@dataclass(frozen=True, slots=True)
class RankStage:
    alias: str
    partition_by: tuple[str, ...]
    order_by: tuple[OrderKey, ...]
    method: TypingLiteral["row_number", "rank", "dense_rank"] = "row_number"
    keep_rank: int | None = None


@dataclass(frozen=True, slots=True)
class ProjectStage:
    fields: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SortStage:
    order_by: tuple[OrderKey, ...]


WorkflowStage = AggregateStage | FilterStage | DerivedStage | RollingStage | OffsetStage | TrendStage | NthStage | RankStage | ProjectStage | SortStage


def _unique(items: tuple[str, ...]) -> tuple[str, ...]:
    result: list[str] = []
    for item in items:
        if item not in result:
            result.append(item)
    return tuple(result)


def _project_existing(node: Node, fields: tuple[str, ...], grain: Grain) -> Project:
    return Project(node, tuple(NamedExpr(field, Column(field)) for field in fields), grain)


class WorkflowPlanner:
    """Compose user-facing relational stages while preserving fields and grain."""

    def __init__(self, source: Node, fields: tuple[str, ...]):
        self.state = WorkflowState(source, _unique(fields), output_grain(source))

    def apply(self, stage: WorkflowStage) -> WorkflowState:
        state = self.state
        if isinstance(stage, AggregateStage):
            aliases = tuple(metric.alias for metric in stage.metrics)
            if len(set(stage.group_by + aliases)) != len(stage.group_by) + len(aliases):
                raise ValueError("workflow aggregate output aliases must be unique")
            grain = Grain(stage.group_by, "grouped" if stage.group_by else "scalar")
            node = Aggregate(
                state.node,
                tuple(NamedExpr(field, Column(field)) for field in stage.group_by),
                stage.metrics,
                grain,
            )
            self.state = WorkflowState(node, stage.group_by + aliases, grain)
            return self.state

        if isinstance(stage, FilterStage):
            self.state = WorkflowState(Filter(state.node, stage.predicate), state.fields, state.grain)
            return self.state

        if isinstance(stage, DerivedStage):
            if not stage.alias or stage.alias in state.fields:
                raise ValueError(f"workflow output field already exists or is empty: {stage.alias}")
            projected_fields = tuple(NamedExpr(field, Column(field)) for field in state.fields) + (NamedExpr(stage.alias, stage.expr),)
            node = Project(state.node, projected_fields, state.grain)
            self.state = WorkflowState(node, state.fields + (stage.alias,), state.grain)
            return self.state

        if isinstance(stage, RollingStage):
            if stage.window_size < 1:
                raise ValueError("rolling window size must be >= 1")
            if stage.alias in state.fields:
                raise ValueError(f"workflow output field already exists: {stage.alias}")
            args = () if stage.function == "count" and stage.field is None else (Column(str(stage.field)),)
            field = WindowField(
                stage.alias,
                stage.function,
                args,
                tuple(Column(field) for field in stage.partition_by),
                stage.order_by,
                WindowFrame(-(stage.window_size - 1), 0),
            )
            node = Window(state.node, (field,))
            self.state = WorkflowState(node, state.fields + (stage.alias,), state.grain)
            return self.state

        if isinstance(stage, OffsetStage):
            if stage.offset < 1:
                raise ValueError("lag/lead offset must be >= 1")
            if stage.alias in state.fields:
                raise ValueError(f"workflow output field already exists: {stage.alias}")
            field = WindowField(
                stage.alias,
                stage.direction,
                (Column(stage.field), Literal(stage.offset)),
                tuple(Column(field) for field in stage.partition_by),
                stage.order_by,
            )
            node = Window(state.node, (field,))
            self.state = WorkflowState(node, state.fields + (stage.alias,), state.grain)
            return self.state

        if isinstance(stage, TrendStage):
            if stage.periods < 2:
                raise ValueError("trend requires at least 2 consecutive periods")
            if stage.alias in state.fields:
                raise ValueError(f"workflow output field already exists: {stage.alias}")
            internal: list[WindowField] = []
            lag_names: list[str] = []
            for offset in range(1, stage.periods):
                lag_name = f"__ta_trend_{stage.alias}_{offset}"
                lag_names.append(lag_name)
                internal.append(WindowField(
                    lag_name,
                    "lag",
                    (Column(stage.field), Literal(offset)),
                    tuple(Column(field) for field in stage.partition_by),
                    stage.order_by,
                ))
            windowed = Window(state.node, tuple(internal))
            values = [Column(stage.field)] + [Column(name) for name in lag_names]
            comparisons: list[Expr] = []
            op = ">" if stage.direction == "up" and stage.strict else ">=" if stage.direction == "up" else "<" if stage.strict else "<="
            for index in range(len(values) - 1):
                comparisons.append(Binary(values[index], op, values[index + 1]))
            predicate: Expr = comparisons[0] if len(comparisons) == 1 else Boolean("and", tuple(comparisons))
            trend_expr = Case(((predicate, Literal(1)),), Literal(0))
            projected_fields = tuple(NamedExpr(field, Column(field)) for field in state.fields) + (NamedExpr(stage.alias, trend_expr),)
            node = Project(windowed, projected_fields, state.grain)
            self.state = WorkflowState(node, state.fields + (stage.alias,), state.grain)
            return self.state

        if isinstance(stage, NthStage):
            if stage.n < 1:
                raise ValueError("nth selection requires n >= 1")
            rank_alias = "__ta_workflow_nth"
            order_by = tuple(OrderKey(item.expr, not item.descending) for item in stage.order_by) if stage.from_end else stage.order_by
            ranked = Rank(
                state.node,
                rank_alias,
                order_by,
                tuple(Column(field) for field in stage.partition_by),
                "row_number",
            )
            filtered = Filter(ranked, Binary(Column(rank_alias), "=", Literal(stage.n)))
            node = _project_existing(filtered, state.fields, state.grain)
            self.state = WorkflowState(node, state.fields, state.grain)
            return self.state

        if isinstance(stage, RankStage):
            if stage.alias in state.fields:
                raise ValueError(f"workflow output field already exists: {stage.alias}")
            ranked = Rank(
                state.node,
                stage.alias,
                stage.order_by,
                tuple(Column(field) for field in stage.partition_by),
                stage.method,
            )
            fields = state.fields + (stage.alias,)
            node: Node = ranked
            if stage.keep_rank is not None:
                if stage.keep_rank < 1:
                    raise ValueError("selected rank must be >= 1")
                node = Filter(node, Binary(Column(stage.alias), "=", Literal(stage.keep_rank)))
            self.state = WorkflowState(node, fields, state.grain)
            return self.state

        if isinstance(stage, ProjectStage):
            requested = _unique(stage.fields)
            missing_grain = [key for key in state.grain.keys if key not in requested]
            if missing_grain:
                raise ValueError(f"workflow projection must retain grain keys: {missing_grain}")
            node = Project(state.node, tuple(NamedExpr(field, Column(field)) for field in requested), state.grain)
            self.state = WorkflowState(node, requested, state.grain)
            return self.state

        if isinstance(stage, SortStage):
            self.state = WorkflowState(Sort(state.node, stage.order_by), state.fields, state.grain)
            return self.state

        raise TypeError(type(stage))

    def apply_all(self, stages: tuple[WorkflowStage, ...]) -> WorkflowState:
        for stage in stages:
            self.apply(stage)
        return self.state
