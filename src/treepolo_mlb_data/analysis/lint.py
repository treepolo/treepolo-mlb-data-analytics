from __future__ import annotations

from .model import (
    Aggregate, Binary, Boolean, Case, CollectSet, Column, EventPattern, Filter, FollowEvent, InList, IsNull, Join, Limit,
    Literal, Node, Not, Project, Rank, SetOperation, Sort, Source, Window,
)


def expr_columns(expr) -> set[tuple[str, str | None]]:
    """(name, relation) for every Column used by an expression."""
    if isinstance(expr, Column):
        return {(expr.name, expr.relation)}
    if isinstance(expr, Literal) or expr is None:
        return set()
    if isinstance(expr, Binary):
        return expr_columns(expr.left) | expr_columns(expr.right)
    if isinstance(expr, Boolean):
        return set().union(*(expr_columns(t) for t in expr.terms)) if expr.terms else set()
    if isinstance(expr, Not):
        return expr_columns(expr.term)
    if isinstance(expr, InList):
        return expr_columns(expr.expr).union(*(expr_columns(v) for v in expr.values))
    if isinstance(expr, IsNull):
        return expr_columns(expr.expr)
    if isinstance(expr, Case):
        found = set()
        for predicate, value in expr.branches:
            found |= expr_columns(predicate) | expr_columns(value)
        return found | expr_columns(expr.else_expr)
    raise TypeError(type(expr))


def lint_columns(node: Node, source_columns) -> list[str]:
    """Return one message per column that a node uses but its input does not provide.

    SQLite silently reads an unknown double-quoted identifier as a *string literal*, so a typo gives wrong numbers
    instead of an error (DuckDB raises). Run this before executing any research query.
    """
    problems: list[str] = []

    def check(where: str, used, available: set[str]) -> None:
        for name, relation in sorted(used, key=lambda x: (x[0], x[1] or "")):
            if relation is None and name not in available:
                problems.append(f"{where}: column {name!r} is not produced by its input")

    def walk(n: Node) -> set[str]:
        if isinstance(n, Source):
            return set(source_columns)
        if isinstance(n, Filter):
            child = walk(n.source); check("Filter", expr_columns(n.predicate), child); return child
        if isinstance(n, Aggregate):
            child = walk(n.source)
            for item in n.group_by: check("Aggregate.group_by", expr_columns(item.expr), child)
            for metric in n.metrics: check(f"Aggregate.metric {metric.alias}", expr_columns(metric.expr), child)
            return {i.alias for i in n.group_by} | {m.alias for m in n.metrics}
        if isinstance(n, Window):
            child = walk(n.source)
            for f in n.fields:
                used = set().union(*(expr_columns(a) for a in f.args)) if f.args else set()
                for e in f.partition_by: used |= expr_columns(e)
                for k in f.order_by: used |= expr_columns(k.expr)
                check(f"Window.{f.alias}", used, child)
            return child | {f.alias for f in n.fields}
        if isinstance(n, Rank):
            child = walk(n.source)
            used = set()
            for e in n.partition_by: used |= expr_columns(e)
            for k in n.order_by: used |= expr_columns(k.expr)
            check("Rank", used, child); return child | {n.alias}
        if isinstance(n, Project):
            child = walk(n.source)
            for item in n.fields: check(f"Project.{item.alias}", expr_columns(item.expr), child)
            return {i.alias for i in n.fields}
        if isinstance(n, Sort):
            child = walk(n.source)
            for k in n.order_by: check("Sort", expr_columns(k.expr), child)
            return child
        if isinstance(n, Limit):
            return walk(n.source)
        if isinstance(n, SetOperation):
            left = walk(n.left); walk(n.right); return left
        if isinstance(n, Join):
            left = walk(n.left); right = walk(n.right)
            for item in n.fields:
                for name, relation in expr_columns(item.expr):
                    side = left if relation == "left" else right if relation == "right" else None
                    if side is None or name not in side:
                        problems.append(f"Join.{item.alias}: column {relation}.{name} is not produced by its input")
            return {i.alias for i in n.fields}
        raise NotImplementedError(f"lint does not cover {type(n).__name__}")  # CollectSet/EventPattern/FollowEvent: add when needed
    walk(node)
    return problems
