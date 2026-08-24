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


Expr = Column | Literal | Binary | Boolean | Not | InList | IsNull


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
class Project:
    source: Node
    fields: tuple[NamedExpr, ...]


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


Node = Source | Filter | Aggregate | Rank | Project | Sort | Limit | SetOperation


def output_grain(node: Node) -> Grain:
    if isinstance(node, Source):
        return node.grain
    if isinstance(node, Aggregate):
        return node.grain
    if isinstance(node, SetOperation):
        return output_grain(node.left)
    return output_grain(node.source)


def validate(node: Node) -> Grain:
    if isinstance(node, Source):
        if not node.table:
            raise ValueError("source table is required")
        return node.grain
    if isinstance(node, Filter):
        return validate(node.source)
    if isinstance(node, Aggregate):
        validate(node.source)
        aliases = tuple(item.alias for item in node.group_by)
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
        if node.method not in {"row_number", "rank", "dense_rank"}:
            raise ValueError(f"unsupported rank method: {node.method}")
        return grain
    if isinstance(node, Project):
        grain = validate(node.source)
        aliases = {item.alias for item in node.fields}
        missing = [key for key in grain.keys if key not in aliases]
        if missing:
            raise ValueError(f"project must retain grain keys: {missing}")
        return grain
    if isinstance(node, Sort):
        return validate(node.source)
    if isinstance(node, Limit):
        grain = validate(node.source)
        if node.count < 0:
            raise ValueError("limit must be non-negative")
        return grain
    if isinstance(node, SetOperation):
        left = validate(node.left)
        right = validate(node.right)
        if left != right:
            raise ValueError("set operation requires identical grains")
        if node.all_rows and node.operation != "union":
            raise ValueError("all_rows is only valid for union")
        return left
    raise TypeError(type(node))
