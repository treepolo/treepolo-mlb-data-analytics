from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal as TypingLiteral


@dataclass(frozen=True, slots=True)
class Grain:
    keys: tuple[str, ...]
    label: str | None = None

    def __post_init__(self) -> None:
        if len(set(self.keys)) != len(self.keys):
            raise ValueError("grain keys must be unique")
        if any(not key for key in self.keys):
            raise ValueError("grain keys must be non-empty strings")


PITCH_GRAIN = Grain(("pitch_uid",), "pitch")
PLATE_APPEARANCE_GRAIN = Grain(("game_pk", "at_bat_number"), "plate_appearance")
GAME_GRAIN = Grain(("game_pk",), "game")
SCALAR_GRAIN = Grain((), "scalar")


@dataclass(frozen=True, slots=True)
class Column:
    name: str
    relation: TypingLiteral["left", "right"] | None = None


@dataclass(frozen=True, slots=True)
class Literal:
    value: Any


@dataclass(frozen=True, slots=True)
class Binary:
    left: Expr
    op: str
    right: Expr


@dataclass(frozen=True, slots=True)
class Boolean:
    op: TypingLiteral["and", "or"]
    terms: tuple[Expr, ...]


@dataclass(frozen=True, slots=True)
class Not:
    term: Expr


@dataclass(frozen=True, slots=True)
class InList:
    expr: Expr
    values: tuple[Expr, ...]
    negated: bool = False


@dataclass(frozen=True, slots=True)
class IsNull:
    expr: Expr
    negated: bool = False


@dataclass(frozen=True, slots=True)
class Case:
    branches: tuple[tuple[Expr, Expr], ...]
    else_expr: Expr | None = None


Expr = Column | Literal | Binary | Boolean | Not | InList | IsNull | Case


@dataclass(frozen=True, slots=True)
class NamedExpr:
    alias: str
    expr: Expr


@dataclass(frozen=True, slots=True)
class Metric:
    alias: str
    function: TypingLiteral["count", "sum", "avg", "min", "max"]
    expr: Expr | None = None
    distinct: bool = False


@dataclass(frozen=True, slots=True)
class OrderKey:
    expr: Expr
    descending: bool = False


@dataclass(frozen=True, slots=True)
class WindowField:
    alias: str
    function: TypingLiteral[
        "row_number", "rank", "dense_rank", "lag", "lead",
        "percent_rank", "cume_dist", "sum", "avg", "count", "min", "max"
    ]
    args: tuple[Expr, ...] = ()
    partition_by: tuple[Expr, ...] = ()
    order_by: tuple[OrderKey, ...] = ()


@dataclass(frozen=True, slots=True)
class Source:
    table: str
    grain: Grain


@dataclass(frozen=True, slots=True)
class Filter:
    source: Node
    predicate: Expr


@dataclass(frozen=True, slots=True)
class Aggregate:
    source: Node
    group_by: tuple[NamedExpr, ...]
    metrics: tuple[Metric, ...]
    grain: Grain


@dataclass(frozen=True, slots=True)
class Rank:
    source: Node
    alias: str
    order_by: tuple[OrderKey, ...]
    partition_by: tuple[Expr, ...] = ()
    method: TypingLiteral["row_number", "rank", "dense_rank"] = "row_number"


@dataclass(frozen=True, slots=True)
class Window:
    source: Node
    fields: tuple[WindowField, ...]


@dataclass(frozen=True, slots=True)
class Project:
    source: Node
    fields: tuple[NamedExpr, ...]
    grain: Grain | None = None


@dataclass(frozen=True, slots=True)
class Sort:
    source: Node
    order_by: tuple[OrderKey, ...]


@dataclass(frozen=True, slots=True)
class Limit:
    source: Node
    count: int


@dataclass(frozen=True, slots=True)
class SetOperation:
    left: Node
    right: Node
    operation: TypingLiteral["union", "intersect", "except"]
    all_rows: bool = False


@dataclass(frozen=True, slots=True)
class Join:
    left: Node
    right: Node
    on: Expr
    fields: tuple[NamedExpr, ...]
    grain: Grain
    how: TypingLiteral["inner", "left"] = "inner"


@dataclass(frozen=True, slots=True)
class CollectSet:
    source: Node
    group_by: tuple[NamedExpr, ...]
    value: Expr
    alias: str
    grain: Grain
    separator: str = "|"


@dataclass(frozen=True, slots=True)
class EventPattern:
    """Select one occurrence of an event inside an ordered partition.

    arrangement applies to all matched event occurrences in each partition.
    "consecutive" requires every matched event to be adjacent; "none_adjacent"
    requires no two matched events to be adjacent.
    """

    source: Node
    partition_by: tuple[NamedExpr, ...]
    order_by: tuple[OrderKey, ...]
    event: Expr
    occurrence: int
    exact_count: int | None = None
    require_last_event: bool = False
    arrangement: TypingLiteral["any", "consecutive", "none_adjacent"] = "any"


@dataclass(frozen=True, slots=True)
class FollowEvent:
    """Select the first target event after each anchor within a bounded row gap.

    Optional between predicates are emitted as 0/1 columns describing whether
    each predicate occurs strictly between the anchor and selected target.
    """

    source: Node
    partition_by: tuple[NamedExpr, ...]
    order_by: tuple[OrderKey, ...]
    anchor: Expr
    target: Expr
    max_gap: int
    between: tuple[NamedExpr, ...] = ()


Node = (
    Source | Filter | Aggregate | Rank | Window | Project | Sort | Limit |
    SetOperation | Join | CollectSet | EventPattern | FollowEvent
)


def output_grain(node: Node) -> Grain:
    if isinstance(node, Source):
        return node.grain
    if isinstance(node, Aggregate):
        return node.grain
    if isinstance(node, Project) and node.grain is not None:
        return node.grain
    if isinstance(node, SetOperation):
        return output_grain(node.left)
    if isinstance(node, Join):
        return node.grain
    if isinstance(node, CollectSet):
        return node.grain
    return output_grain(node.source)


def _validate_named_aliases(items: tuple[NamedExpr, ...], what: str) -> tuple[str, ...]:
    aliases = tuple(item.alias for item in items)
    if any(not alias for alias in aliases):
        raise ValueError(f"{what} aliases must be non-empty")
    if len(set(aliases)) != len(aliases):
        raise ValueError(f"{what} aliases must be unique")
    return aliases


def validate(node: Node) -> Grain:
    if isinstance(node, Source):
        if not node.table:
            raise ValueError("source table is required")
        return node.grain
    if isinstance(node, Filter):
        return validate(node.source)
    if isinstance(node, Aggregate):
        validate(node.source)
        aliases = _validate_named_aliases(node.group_by, "group_by")
        if aliases != node.grain.keys:
            raise ValueError("aggregate grain keys must exactly match group_by aliases")
        metric_aliases = [m.alias for m in node.metrics]
        if len(set(metric_aliases)) != len(metric_aliases):
            raise ValueError("metric aliases must be unique")
        return node.grain
    if isinstance(node, Rank):
        grain = validate(node.source)
        if not node.order_by:
            raise ValueError("rank requires order_by")
        return grain
    if isinstance(node, Window):
        grain = validate(node.source)
        aliases = [field.alias for field in node.fields]
        if not aliases or len(set(aliases)) != len(aliases):
            raise ValueError("window fields require unique aliases")
        return grain
    if isinstance(node, Project):
        source_grain = validate(node.source)
        aliases = set(_validate_named_aliases(node.fields, "project"))
        grain = node.grain or source_grain
        missing = [key for key in grain.keys if key not in aliases]
        if missing:
            raise ValueError(f"project must retain grain keys: {missing}")
        return grain
    if isinstance(node, Sort):
        grain = validate(node.source)
        if not node.order_by:
            raise ValueError("sort requires order_by")
        return grain
    if isinstance(node, Limit):
        grain = validate(node.source)
        if node.count < 0:
            raise ValueError("limit must be non-negative")
        return grain
    if isinstance(node, SetOperation):
        left = validate(node.left); right = validate(node.right)
        if left != right:
            raise ValueError("set operation requires identical grains")
        if node.all_rows and node.operation != "union":
            raise ValueError("all_rows is only valid for union")
        return left
    if isinstance(node, Join):
        validate(node.left); validate(node.right)
        aliases = _validate_named_aliases(node.fields, "join fields")
        if tuple(key for key in node.grain.keys if key in aliases) != node.grain.keys:
            raise ValueError("join grain keys must be present in join fields")
        return node.grain
    if isinstance(node, CollectSet):
        validate(node.source)
        aliases = _validate_named_aliases(node.group_by, "collect_set group_by")
        if aliases != node.grain.keys:
            raise ValueError("collect_set grain keys must exactly match group_by aliases")
        if not node.alias or node.alias in aliases:
            raise ValueError("collect_set alias must be unique")
        if not node.separator:
            raise ValueError("collect_set separator cannot be empty")
        return node.grain
    if isinstance(node, EventPattern):
        grain = validate(node.source)
        if not node.partition_by or not node.order_by:
            raise ValueError("event pattern requires partition_by and order_by")
        if node.occurrence < 1:
            raise ValueError("event occurrence must be >= 1")
        if node.exact_count is not None and node.exact_count < 1:
            raise ValueError("exact_count must be >= 1")
        if node.exact_count is not None and node.occurrence > node.exact_count:
            raise ValueError("occurrence cannot exceed exact_count")
        return grain
    if isinstance(node, FollowEvent):
        grain = validate(node.source)
        if not node.partition_by or not node.order_by:
            raise ValueError("follow event requires partition_by and order_by")
        if node.max_gap < 1:
            raise ValueError("follow event max_gap must be >= 1")
        _validate_named_aliases(node.between, "between")
        return grain
    raise TypeError(type(node))
