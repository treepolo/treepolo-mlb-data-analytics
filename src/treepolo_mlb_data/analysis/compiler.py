from __future__ import annotations

import re
from dataclasses import dataclass

from .model import (
    Aggregate, Binary, Boolean, Column, Expr, Filter, InList, IsNull, Limit,
    Literal, Node, Not, Project, Rank, SetOperation, Sort, Source, validate,
)

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_BINARY_OPS = {"=", "!=", "<>", ">", ">=", "<", "<=", "+", "-", "*", "/"}


def quote_ident(name: str) -> str:
    if not _IDENT.fullmatch(name):
        raise ValueError(f"unsafe SQL identifier: {name!r}")
    return f'"{name}"'


@dataclass(frozen=True, slots=True)
class CompiledQuery:
    sql: str
    params: tuple[object, ...]


class SQLCompiler:
    def compile(self, node: Node) -> CompiledQuery:
        validate(node)
        sql, params = self._node(node)
        return CompiledQuery(sql, tuple(params))

    def _expr(self, expr: Expr) -> tuple[str, list[object]]:
        if isinstance(expr, Column):
            return quote_ident(expr.name), []
        if isinstance(expr, Literal):
            return "?", [expr.value]
        if isinstance(expr, Binary):
            if expr.op not in _BINARY_OPS:
                raise ValueError(f"unsupported binary operator: {expr.op}")
            left, lp = self._expr(expr.left)
            right, rp = self._expr(expr.right)
            return f"({left} {expr.op} {right})", lp + rp
        if isinstance(expr, Boolean):
            if not expr.terms:
                raise ValueError("boolean expression requires at least one term")
            parts: list[str] = []
            params: list[object] = []
            joiner = " AND " if expr.op == "and" else " OR "
            for term in expr.terms:
                sql, values = self._expr(term)
                parts.append(sql)
                params.extend(values)
            return "(" + joiner.join(parts) + ")", params
        if isinstance(expr, Not):
            sql, params = self._expr(expr.term)
            return f"(NOT {sql})", params
        if isinstance(expr, InList):
            target, params = self._expr(expr.expr)
            if not expr.values:
                return ("1=1" if expr.negated else "1=0"), params
            pieces: list[str] = []
            for value in expr.values:
                sql, values = self._expr(value)
                pieces.append(sql)
                params.extend(values)
            keyword = "NOT IN" if expr.negated else "IN"
            return f"({target} {keyword} ({','.join(pieces)}))", params
        if isinstance(expr, IsNull):
            target, params = self._expr(expr.expr)
            return f"({target} IS {'NOT ' if expr.negated else ''}NULL)", params
        raise TypeError(type(expr))

    def _node(self, node: Node) -> tuple[str, list[object]]:
        if isinstance(node, Source):
            return f"SELECT * FROM {quote_ident(node.table)}", []
        if isinstance(node, Filter):
            child, params = self._node(node.source)
            predicate, pp = self._expr(node.predicate)
            return f"SELECT * FROM ({child}) AS q WHERE {predicate}", params + pp
        if isinstance(node, Aggregate):
            child, params = self._node(node.source)
            select_parts: list[str] = []
            group_parts: list[str] = []
            extra: list[object] = []
            for item in node.group_by:
                expr_sql, ep = self._expr(item.expr)
                select_parts.append(f"{expr_sql} AS {quote_ident(item.alias)}")
                group_parts.append(expr_sql)
                extra.extend(ep)
            for metric in node.metrics:
                fn = metric.function.upper()
                if metric.expr is None:
                    if metric.function != "count":
                        raise ValueError(f"{metric.function} requires an expression")
                    body, mp = "*", []
                else:
                    body, mp = self._expr(metric.expr)
                    if metric.distinct:
                        body = "DISTINCT " + body
                select_parts.append(f"{fn}({body}) AS {quote_ident(metric.alias)}")
                extra.extend(mp)
            if not select_parts:
                raise ValueError("aggregate requires group fields or metrics")
            sql = f"SELECT {', '.join(select_parts)} FROM ({child}) AS q"
            if group_parts:
                sql += " GROUP BY " + ", ".join(group_parts)
            return sql, params + extra
        if isinstance(node, Rank):
            child, params = self._node(node.source)
            part_sql: list[str] = []
            order_sql: list[str] = []
            extra: list[object] = []
            for expr in node.partition_by:
                sql, ep = self._expr(expr); part_sql.append(sql); extra.extend(ep)
            for item in node.order_by:
                sql, ep = self._expr(item.expr); order_sql.append(sql + (" DESC" if item.descending else " ASC")); extra.extend(ep)
            fn = {"row_number": "ROW_NUMBER", "rank": "RANK", "dense_rank": "DENSE_RANK"}[node.method]
            window = ""
            if part_sql: window += "PARTITION BY " + ", ".join(part_sql) + " "
            window += "ORDER BY " + ", ".join(order_sql)
            return f"SELECT q.*, {fn}() OVER ({window}) AS {quote_ident(node.alias)} FROM ({child}) AS q", params + extra
        if isinstance(node, Project):
            child, params = self._node(node.source)
            pieces: list[str] = []
            extra: list[object] = []
            for item in node.fields:
                sql, ep = self._expr(item.expr); pieces.append(f"{sql} AS {quote_ident(item.alias)}"); extra.extend(ep)
            return f"SELECT {', '.join(pieces)} FROM ({child}) AS q", params + extra
        if isinstance(node, Sort):
            child, params = self._node(node.source)
            pieces: list[str] = []
            extra: list[object] = []
            for item in node.order_by:
                sql, ep = self._expr(item.expr); pieces.append(sql + (" DESC" if item.descending else " ASC")); extra.extend(ep)
            return f"SELECT * FROM ({child}) AS q ORDER BY {', '.join(pieces)}", params + extra
        if isinstance(node, Limit):
            child, params = self._node(node.source)
            return f"SELECT * FROM ({child}) AS q LIMIT ?", params + [node.count]
        if isinstance(node, SetOperation):
            left, lp = self._node(node.left)
            right, rp = self._node(node.right)
            op = {"union": "UNION ALL" if node.all_rows else "UNION", "intersect": "INTERSECT", "except": "EXCEPT"}[node.operation]
            return f"SELECT * FROM ({left}) AS l {op} SELECT * FROM ({right}) AS r", lp + rp
        raise TypeError(type(node))
