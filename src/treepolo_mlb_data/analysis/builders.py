from __future__ import annotations

from typing import Literal as TypingLiteral

from .model import (
    Aggregate, Binary, CollectSet, Column, Filter, Grain, Literal, Metric,
    NamedExpr, Node, OrderKey, Project, Rank, Window, WindowField,
)


def pitch_usage(
    source: Node,
    *,
    entity_fields: tuple[str, ...] = ("pitcher",),
    pitch_field: str = "pitch_type",
) -> Node:
    """Build per-entity pitch counts and usage rates from any pitch-grain relation."""
    group_names = entity_fields + (pitch_field,)
    grouped = Aggregate(
        source,
        tuple(NamedExpr(name, Column(name)) for name in group_names),
        (Metric("pitch_count", "count"),),
        Grain(group_names, "pitch_usage"),
    )
    with_total = Window(
        grouped,
        (WindowField("total_pitch_count", "sum", (Column("pitch_count"),), tuple(Column(x) for x in entity_fields)),),
    )
    fields = [NamedExpr(name, Column(name)) for name in group_names]
    fields.extend((
        NamedExpr("pitch_count", Column("pitch_count")),
        NamedExpr("total_pitch_count", Column("total_pitch_count")),
        NamedExpr("usage_rate", Binary(Binary(Column("pitch_count"), "*", Literal(1.0)), "/", Column("total_pitch_count"))),
    ))
    return Project(with_total, tuple(fields), Grain(group_names, "pitch_usage"))


def arsenal_table(
    usage: Node,
    *,
    entity_fields: tuple[str, ...] = ("pitcher",),
    pitch_field: str = "pitch_type",
    min_usage: float = 0.05,
    alias: str = "arsenal",
) -> Node:
    eligible = Filter(usage, Binary(Column("usage_rate"), ">", Literal(min_usage)))
    grain = Grain(entity_fields, "arsenal")
    return CollectSet(
        eligible,
        tuple(NamedExpr(name, Column(name)) for name in entity_fields),
        Column(pitch_field),
        alias,
        grain,
    )


def rank_pitch_roles(
    usage: Node,
    *,
    entity_fields: tuple[str, ...] = ("pitcher",),
    metric: str = "usage_rate",
    alias: str = "role_rank",
    descending: bool = True,
    method: TypingLiteral["row_number", "rank", "dense_rank"] = "dense_rank",
) -> Node:
    """Rank pitch roles within each entity.

    dense_rank is the default so tied usage or performance values remain tied.
    row_number is available when a caller explicitly needs one deterministic pitch.
    """
    order = [OrderKey(Column(metric), descending=descending)]
    if method == "row_number":
        order.append(OrderKey(Column("pitch_type")))
    return Rank(
        usage,
        alias,
        tuple(order),
        tuple(Column(x) for x in entity_fields),
        method,
    )


def empirical_percentile(
    source: Node,
    *,
    value_field: str,
    alias: str,
    partition_fields: tuple[str, ...],
) -> Node:
    """Attach an empirical cumulative percentile inside each partition."""
    return Window(
        source,
        (WindowField(alias, "cume_dist", (), tuple(Column(x) for x in partition_fields), (OrderKey(Column(value_field)),)),),
    )
