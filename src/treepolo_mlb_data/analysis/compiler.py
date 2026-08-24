from __future__ import annotations

import re
from dataclasses import dataclass

from .model import (
    Aggregate, Binary, Boolean, Case, CollectSet, Column, EventPattern, Expr,
    Filter, FollowEvent, InList, IsNull, Join, Limit, Literal, Node, Not,
    Project, Rank, SetOperation, Sort, Source, Window, WindowField, validate,
)

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_BINARY_OPS = {"=", "!=", "<>", ">", ">=", "<", "<=", "+", "-", "*", "/", "%"}


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

    def _expr(
        self,
        expr: Expr,
        *,
        default_alias: str | None = None,
        relation_aliases: dict[str, str] | None = None,
    ) -> tuple[str, list[object]]:
        if isinstance(expr, Column):
            alias = None
            if expr.relation is not None:
                if relation_aliases is None or expr.relation not in relation_aliases:
                    raise ValueError(f"qualified column {expr.relation}.{expr.name} is not valid in this context")
                alias = relation_aliases[expr.relation]
            elif default_alias:
                alias = default_alias
            prefix = f"{quote_ident(alias)}." if alias else ""
            return prefix + quote_ident(expr.name), []
        if isinstance(expr, Literal):
            return "?", [expr.value]
        if isinstance(expr, Binary):
            if expr.op not in _BINARY_OPS:
                raise ValueError(f"unsupported binary operator: {expr.op}")
            left, lp = self._expr(expr.left, default_alias=default_alias, relation_aliases=relation_aliases)
            right, rp = self._expr(expr.right, default_alias=default_alias, relation_aliases=relation_aliases)
            return f"({left} {expr.op} {right})", lp + rp
        if isinstance(expr, Boolean):
            if not expr.terms:
                raise ValueError("boolean expression requires at least one term")
            pieces: list[str] = []; params: list[object] = []
            joiner = " AND " if expr.op == "and" else " OR "
            for term in expr.terms:
                sql, values = self._expr(term, default_alias=default_alias, relation_aliases=relation_aliases)
                pieces.append(sql); params.extend(values)
            return "(" + joiner.join(pieces) + ")", params
        if isinstance(expr, Not):
            sql, params = self._expr(expr.term, default_alias=default_alias, relation_aliases=relation_aliases)
            return f"(NOT {sql})", params
        if isinstance(expr, InList):
            target, params = self._expr(expr.expr, default_alias=default_alias, relation_aliases=relation_aliases)
            if not expr.values:
                return ("1=1" if expr.negated else "1=0"), params
            pieces: list[str] = []
            for value in expr.values:
                sql, values = self._expr(value, default_alias=default_alias, relation_aliases=relation_aliases)
                pieces.append(sql); params.extend(values)
            keyword = "NOT IN" if expr.negated else "IN"
            return f"({target} {keyword} ({','.join(pieces)}))", params
        if isinstance(expr, IsNull):
            target, params = self._expr(expr.expr, default_alias=default_alias, relation_aliases=relation_aliases)
            return f"({target} IS {'NOT ' if expr.negated else ''}NULL)", params
        if isinstance(expr, Case):
            if not expr.branches:
                raise ValueError("case expression requires at least one branch")
            pieces = ["CASE"]; params: list[object] = []
            for predicate, value in expr.branches:
                ps, pp = self._expr(predicate, default_alias=default_alias, relation_aliases=relation_aliases)
                vs, vp = self._expr(value, default_alias=default_alias, relation_aliases=relation_aliases)
                pieces.append(f"WHEN {ps} THEN {vs}"); params.extend(pp); params.extend(vp)
            if expr.else_expr is not None:
                es, ep = self._expr(expr.else_expr, default_alias=default_alias, relation_aliases=relation_aliases)
                pieces.append(f"ELSE {es}"); params.extend(ep)
            pieces.append("END")
            return "(" + " ".join(pieces) + ")", params
        raise TypeError(type(expr))

    def _order(self, items, *, default_alias: str | None = None) -> tuple[list[str], list[object]]:
        pieces: list[str] = []; params: list[object] = []
        for item in items:
            sql, ep = self._expr(item.expr, default_alias=default_alias)
            pieces.append(sql + (" DESC" if item.descending else " ASC")); params.extend(ep)
        return pieces, params

    def _window_field(self, field: WindowField) -> tuple[str, list[object]]:
        fn = field.function.upper(); params: list[object] = []; args: list[str] = []
        if field.function in {"row_number", "rank", "dense_rank", "percent_rank", "cume_dist"}:
            if field.args:
                raise ValueError(f"{field.function} does not accept arguments")
        elif field.function in {"lag", "lead"}:
            if not 1 <= len(field.args) <= 3:
                raise ValueError(f"{field.function} requires 1 to 3 arguments")
        elif field.function == "count" and not field.args:
            args = ["*"]
        elif len(field.args) != 1:
            raise ValueError(f"{field.function} requires one argument")
        for arg in field.args:
            sql, ep = self._expr(arg); args.append(sql); params.extend(ep)
        parts: list[str] = []
        if field.partition_by:
            psql: list[str] = []
            for expr in field.partition_by:
                sql, ep = self._expr(expr); psql.append(sql); params.extend(ep)
            parts.append("PARTITION BY " + ", ".join(psql))
        if field.order_by:
            order, op = self._order(field.order_by); params.extend(op)
            parts.append("ORDER BY " + ", ".join(order))
        return f"{fn}({', '.join(args)}) OVER ({' '.join(parts)}) AS {quote_ident(field.alias)}", params

    def _prepared_ordered_stream(self, child: str, child_params: list[object], partition_by, order_by) -> tuple[str, list[str], list[object]]:
        """Create internal partition/order columns once, avoiding repeated expression placeholders."""
        select = ["q.*"]; params: list[object] = []
        part_names: list[str] = []
        for i, item in enumerate(partition_by):
            name = f"__ta_part_{i}"; part_names.append(name)
            sql, ep = self._expr(item.expr, default_alias="q")
            select.append(f"{sql} AS {quote_ident(name)}"); params.extend(ep)
        order_names: list[tuple[str, bool]] = []
        for i, item in enumerate(order_by):
            name = f"__ta_order_{i}"; order_names.append((name, item.descending))
            sql, ep = self._expr(item.expr, default_alias="q")
            select.append(f"{sql} AS {quote_ident(name)}"); params.extend(ep)
        prepared = f"SELECT {', '.join(select)} FROM ({child}) AS q"
        part = ", ".join(quote_ident(x) for x in part_names)
        order = ", ".join(quote_ident(name) + (" DESC" if desc else " ASC") for name, desc in order_names)
        window = (f"PARTITION BY {part} " if part else "") + f"ORDER BY {order}"
        base = f"SELECT p.*, ROW_NUMBER() OVER ({window}) AS {quote_ident('__ta_seq')}, COUNT(*) OVER ({'PARTITION BY ' + part if part else ''}) AS {quote_ident('__ta_rows')} FROM prepared AS p"
        return f"prepared AS ({prepared}), base AS ({base})", part_names, params + child_params

    @staticmethod
    def _same_partition(left_alias: str, right_alias: str, names: list[str]) -> str:
        return " AND ".join(
            f'{quote_ident(left_alias)}.{quote_ident(name)} IS {quote_ident(right_alias)}.{quote_ident(name)}'
            for name in names
        ) or "1=1"

    def _node(self, node: Node) -> tuple[str, list[object]]:
        if isinstance(node, Source):
            return f"SELECT * FROM {quote_ident(node.table)}", []
        if isinstance(node, Filter):
            child, cp = self._node(node.source); pred, pp = self._expr(node.predicate)
            return f"SELECT * FROM ({child}) AS q WHERE {pred}", cp + pp
        if isinstance(node, Aggregate):
            child, cp = self._node(node.source)
            select: list[str] = []; groups: list[str] = []; sp: list[object] = []
            for item in node.group_by:
                sql, ep = self._expr(item.expr); select.append(f"{sql} AS {quote_ident(item.alias)}"); groups.append(quote_ident(item.alias)); sp.extend(ep)
            for metric in node.metrics:
                fn = metric.function.upper()
                if metric.expr is None:
                    if metric.function != "count": raise ValueError(f"{metric.function} requires an expression")
                    body, mp = "*", []
                else:
                    body, mp = self._expr(metric.expr)
                    if metric.distinct: body = "DISTINCT " + body
                select.append(f"{fn}({body}) AS {quote_ident(metric.alias)}"); sp.extend(mp)
            if not select: raise ValueError("aggregate requires group fields or metrics")
            sql = f"SELECT {', '.join(select)} FROM ({child}) AS q"
            if groups: sql += " GROUP BY " + ", ".join(groups)
            return sql, sp + cp
        if isinstance(node, Rank):
            return self._node(Window(node.source, (WindowField(node.alias, node.method, (), node.partition_by, node.order_by),)))
        if isinstance(node, Window):
            child, cp = self._node(node.source); fields: list[str] = []; fp: list[object] = []
            for field in node.fields:
                sql, ep = self._window_field(field); fields.append(sql); fp.extend(ep)
            return f"SELECT q.*, {', '.join(fields)} FROM ({child}) AS q", fp + cp
        if isinstance(node, Project):
            child, cp = self._node(node.source); fields: list[str] = []; fp: list[object] = []
            for item in node.fields:
                sql, ep = self._expr(item.expr); fields.append(f"{sql} AS {quote_ident(item.alias)}"); fp.extend(ep)
            return f"SELECT {', '.join(fields)} FROM ({child}) AS q", fp + cp
        if isinstance(node, Sort):
            child, cp = self._node(node.source); order, op = self._order(node.order_by)
            return f"SELECT * FROM ({child}) AS q ORDER BY {', '.join(order)}", cp + op
        if isinstance(node, Limit):
            child, cp = self._node(node.source)
            return f"SELECT * FROM ({child}) AS q LIMIT ?", cp + [node.count]
        if isinstance(node, SetOperation):
            left, lp = self._node(node.left); right, rp = self._node(node.right)
            op = {"union": "UNION ALL" if node.all_rows else "UNION", "intersect": "INTERSECT", "except": "EXCEPT"}[node.operation]
            return f"SELECT * FROM ({left}) AS l {op} SELECT * FROM ({right}) AS r", lp + rp
        if isinstance(node, Join):
            left, lp = self._node(node.left); right, rp = self._node(node.right); aliases = {"left": "l", "right": "r"}
            fields: list[str] = []; fp: list[object] = []
            for item in node.fields:
                sql, ep = self._expr(item.expr, relation_aliases=aliases); fields.append(f"{sql} AS {quote_ident(item.alias)}"); fp.extend(ep)
            on, op = self._expr(node.on, relation_aliases=aliases)
            keyword = "LEFT JOIN" if node.how == "left" else "JOIN"
            sql = f"SELECT {', '.join(fields)} FROM ({left}) AS l {keyword} ({right}) AS r ON {on}"
            return sql, fp + lp + rp + op
        if isinstance(node, CollectSet):
            child, cp = self._node(node.source); prep: list[str] = []; pp: list[object] = []
            names: list[str] = []
            for item in node.group_by:
                sql, ep = self._expr(item.expr, default_alias="q"); prep.append(f"{sql} AS {quote_ident(item.alias)}"); names.append(item.alias); pp.extend(ep)
            value, vp = self._expr(node.value, default_alias="q"); prep.append(f"{value} AS {quote_ident('__ta_value')}"); pp.extend(vp)
            prepared = f"SELECT {', '.join(prep)} FROM ({child}) AS q"
            order = ", ".join(quote_ident(x) for x in names + ["__ta_value"])
            distinct = f"SELECT DISTINCT * FROM ({prepared}) AS p WHERE {quote_ident('__ta_value')} IS NOT NULL ORDER BY {order}"
            groups = ", ".join(quote_ident(x) for x in names); prefix = groups + ", " if groups else ""
            sql = f"SELECT {prefix}GROUP_CONCAT({quote_ident('__ta_value')}, ?) AS {quote_ident(node.alias)} FROM ({distinct}) AS s"
            if groups: sql += " GROUP BY " + groups
            return sql, [node.separator] + pp + cp
        if isinstance(node, EventPattern):
            child, cp = self._node(node.source)
            ctes, part_names, base_params = self._prepared_ordered_stream(child, cp, node.partition_by, node.order_by)
            event, ep = self._expr(node.event, default_alias="b")
            part = ", ".join(quote_ident(x) for x in part_names); win = f"PARTITION BY {part} " if part else ""
            marked = f"SELECT b.*, CASE WHEN {event} THEN 1 ELSE 0 END AS {quote_ident('__ta_is_event')} FROM base AS b"
            events = (
                f"SELECT m.*, ROW_NUMBER() OVER ({win}ORDER BY {quote_ident('__ta_seq')}) AS {quote_ident('__ta_event_ordinal')}, "
                f"COUNT(*) OVER ({'PARTITION BY ' + part if part else ''}) AS {quote_ident('__ta_event_count')}, "
                f"LAG({quote_ident('__ta_seq')}) OVER ({win}ORDER BY {quote_ident('__ta_seq')}) AS {quote_ident('__ta_prev_event_seq')} "
                f"FROM marked AS m WHERE {quote_ident('__ta_is_event')} = 1"
            )
            stats_fields = [
                f"MAX({quote_ident('__ta_event_count')}) AS {quote_ident('__ta_event_count')}",
                f"MIN({quote_ident('__ta_seq')}) AS {quote_ident('__ta_min_event_seq')}",
                f"MAX({quote_ident('__ta_seq')}) AS {quote_ident('__ta_max_event_seq')}",
                f"MAX({quote_ident('__ta_rows')}) AS {quote_ident('__ta_rows')}",
                f"MAX(CASE WHEN {quote_ident('__ta_prev_event_seq')} IS NOT NULL AND {quote_ident('__ta_seq')} - {quote_ident('__ta_prev_event_seq')} = 1 THEN 1 ELSE 0 END) AS {quote_ident('__ta_any_adjacent')}"
            ]
            prefix = part + ", " if part else ""; stats = f"SELECT {prefix}{', '.join(stats_fields)} FROM events"
            if part: stats += " GROUP BY " + part
            same = self._same_partition("e", "s", part_names)
            where = [f'e.{quote_ident("__ta_event_ordinal")} = ?']; tail: list[object] = [node.occurrence]
            if node.exact_count is not None:
                where.append(f's.{quote_ident("__ta_event_count")} = ?'); tail.append(node.exact_count)
            if node.require_last_event:
                where.append(f's.{quote_ident("__ta_max_event_seq")} = s.{quote_ident("__ta_rows")}')
            if node.arrangement == "consecutive":
                where.append(f'(s.{quote_ident("__ta_max_event_seq")} - s.{quote_ident("__ta_min_event_seq")} + 1) = s.{quote_ident("__ta_event_count")}')
            elif node.arrangement == "none_adjacent":
                where.append(f's.{quote_ident("__ta_any_adjacent")} = 0')
            sql = f"WITH {ctes}, marked AS ({marked}), events AS ({events}), stats AS ({stats}) SELECT e.* FROM events AS e JOIN stats AS s ON {same} WHERE {' AND '.join(where)}"
            return sql, base_params + ep + tail
        if isinstance(node, FollowEvent):
            child, cp = self._node(node.source)
            ctes, part_names, base_params = self._prepared_ordered_stream(child, cp, node.partition_by, node.order_by)
            same = self._same_partition("a", "t", part_names)
            anchor, ap = self._expr(node.anchor, default_alias="a"); target, tp = self._expr(node.target, default_alias="t")
            between_fields: list[str] = []; bp: list[object] = []
            for item in node.between:
                same_ab = self._same_partition("a", "b", part_names); pred, predp = self._expr(item.expr, default_alias="b")
                exists = (
                    f"EXISTS(SELECT 1 FROM base AS b WHERE {same_ab} AND b.{quote_ident('__ta_seq')} > a.{quote_ident('__ta_seq')} "
                    f"AND b.{quote_ident('__ta_seq')} < t.{quote_ident('__ta_seq')} AND {pred})"
                )
                between_fields.append(f"CASE WHEN {exists} THEN 1 ELSE 0 END AS {quote_ident(item.alias)}"); bp.extend(predp)
            extras = ", " + ", ".join(between_fields) if between_fields else ""
            anchor_partition = [f'a.{quote_ident(x)}' for x in part_names] + [f'a.{quote_ident("__ta_seq")}']
            candidates = (
                f"SELECT t.*, a.{quote_ident('__ta_seq')} AS {quote_ident('__ta_anchor_seq')}{extras}, "
                f"ROW_NUMBER() OVER (PARTITION BY {', '.join(anchor_partition)} ORDER BY t.{quote_ident('__ta_seq')}) AS {quote_ident('__ta_pair_rank')} "
                f"FROM base AS a JOIN base AS t ON {same} AND t.{quote_ident('__ta_seq')} > a.{quote_ident('__ta_seq')} "
                f"AND t.{quote_ident('__ta_seq')} <= a.{quote_ident('__ta_seq')} + ? WHERE {anchor} AND {target}"
            )
            sql = f"WITH {ctes}, candidates AS ({candidates}) SELECT * FROM candidates WHERE {quote_ident('__ta_pair_rank')} = 1"
            return sql, base_params + bp + [node.max_gap] + ap + tp
        raise TypeError(type(node))
