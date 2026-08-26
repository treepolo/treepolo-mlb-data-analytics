from __future__ import annotations

from typing import Any

from .model import (
    Aggregate, Binary, Boolean, Case, CollectSet, Column, EventPattern, Expr,
    Filter, FollowEvent, Grain, InList, IsNull, Join, Limit, Literal, Metric,
    NamedExpr, Node, Not, OrderKey, Project, Rank, SetOperation, Sort, Source,
    Window, WindowField, WindowFrame,
)


def _grain_to_dict(grain: Grain) -> dict[str, Any]:
    return {"keys": list(grain.keys), "label": grain.label}


def _grain_from_dict(data: dict[str, Any]) -> Grain:
    return Grain(tuple(data.get("keys", [])), data.get("label"))


def expr_to_dict(expr: Expr) -> dict[str, Any]:
    if isinstance(expr, Column): return {"kind": "column", "name": expr.name, "relation": expr.relation}
    if isinstance(expr, Literal): return {"kind": "literal", "value": expr.value}
    if isinstance(expr, Binary): return {"kind": "binary", "left": expr_to_dict(expr.left), "op": expr.op, "right": expr_to_dict(expr.right)}
    if isinstance(expr, Boolean): return {"kind": "boolean", "op": expr.op, "terms": [expr_to_dict(x) for x in expr.terms]}
    if isinstance(expr, Not): return {"kind": "not", "term": expr_to_dict(expr.term)}
    if isinstance(expr, InList): return {"kind": "in", "expr": expr_to_dict(expr.expr), "values": [expr_to_dict(x) for x in expr.values], "negated": expr.negated}
    if isinstance(expr, IsNull): return {"kind": "is_null", "expr": expr_to_dict(expr.expr), "negated": expr.negated}
    if isinstance(expr, Case): return {"kind": "case", "branches": [[expr_to_dict(p), expr_to_dict(v)] for p, v in expr.branches], "else": expr_to_dict(expr.else_expr) if expr.else_expr else None}
    raise TypeError(type(expr))


def expr_from_dict(data: dict[str, Any]) -> Expr:
    kind = data["kind"]
    if kind == "column": return Column(data["name"], data.get("relation"))
    if kind == "literal": return Literal(data.get("value"))
    if kind == "binary": return Binary(expr_from_dict(data["left"]), data["op"], expr_from_dict(data["right"]))
    if kind == "boolean": return Boolean(data["op"], tuple(expr_from_dict(x) for x in data["terms"]))
    if kind == "not": return Not(expr_from_dict(data["term"]))
    if kind == "in": return InList(expr_from_dict(data["expr"]), tuple(expr_from_dict(x) for x in data["values"]), bool(data.get("negated", False)))
    if kind == "is_null": return IsNull(expr_from_dict(data["expr"]), bool(data.get("negated", False)))
    if kind == "case": return Case(tuple((expr_from_dict(p), expr_from_dict(v)) for p, v in data["branches"]), expr_from_dict(data["else"]) if data.get("else") else None)
    raise ValueError(f"unknown expression kind: {kind}")


def _named_to_dict(item: NamedExpr) -> dict[str, Any]: return {"alias": item.alias, "expr": expr_to_dict(item.expr)}
def _named_from_dict(data: dict[str, Any]) -> NamedExpr: return NamedExpr(data["alias"], expr_from_dict(data["expr"]))
def _metric_to_dict(metric: Metric) -> dict[str, Any]: return {"alias": metric.alias, "function": metric.function, "expr": expr_to_dict(metric.expr) if metric.expr else None, "distinct": metric.distinct}
def _metric_from_dict(data: dict[str, Any]) -> Metric: return Metric(data["alias"], data["function"], expr_from_dict(data["expr"]) if data.get("expr") else None, bool(data.get("distinct", False)))
def _order_to_dict(item: OrderKey) -> dict[str, Any]: return {"expr": expr_to_dict(item.expr), "descending": item.descending}
def _order_from_dict(data: dict[str, Any]) -> OrderKey: return OrderKey(expr_from_dict(data["expr"]), bool(data.get("descending", False)))


def _frame_to_dict(frame: WindowFrame | None) -> dict[str, Any] | None:
    if frame is None:
        return None
    return {"start": frame.start, "end": frame.end, "unit": frame.unit}


def _frame_from_dict(data: dict[str, Any] | None) -> WindowFrame | None:
    if not data:
        return None
    return WindowFrame(data.get("start"), data.get("end", 0), data.get("unit", "rows"))


def _window_to_dict(item: WindowField) -> dict[str, Any]:
    return {
        "alias": item.alias, "function": item.function,
        "args": [expr_to_dict(x) for x in item.args],
        "partition_by": [expr_to_dict(x) for x in item.partition_by],
        "order_by": [_order_to_dict(x) for x in item.order_by],
        "frame": _frame_to_dict(item.frame),
    }


def _window_from_dict(data: dict[str, Any]) -> WindowField:
    return WindowField(
        data["alias"], data["function"], tuple(expr_from_dict(x) for x in data.get("args", [])),
        tuple(expr_from_dict(x) for x in data.get("partition_by", [])),
        tuple(_order_from_dict(x) for x in data.get("order_by", [])),
        _frame_from_dict(data.get("frame")),
    )


def node_to_dict(node: Node) -> dict[str, Any]:
    if isinstance(node, Source): return {"kind": "source", "table": node.table, "grain": _grain_to_dict(node.grain)}
    if isinstance(node, Filter): return {"kind": "filter", "source": node_to_dict(node.source), "predicate": expr_to_dict(node.predicate)}
    if isinstance(node, Aggregate): return {"kind": "aggregate", "source": node_to_dict(node.source), "group_by": [_named_to_dict(x) for x in node.group_by], "metrics": [_metric_to_dict(x) for x in node.metrics], "grain": _grain_to_dict(node.grain)}
    if isinstance(node, Rank): return {"kind": "rank", "source": node_to_dict(node.source), "alias": node.alias, "order_by": [_order_to_dict(x) for x in node.order_by], "partition_by": [expr_to_dict(x) for x in node.partition_by], "method": node.method}
    if isinstance(node, Window): return {"kind": "window", "source": node_to_dict(node.source), "fields": [_window_to_dict(x) for x in node.fields]}
    if isinstance(node, Project): return {"kind": "project", "source": node_to_dict(node.source), "fields": [_named_to_dict(x) for x in node.fields], "grain": _grain_to_dict(node.grain) if node.grain else None}
    if isinstance(node, Sort): return {"kind": "sort", "source": node_to_dict(node.source), "order_by": [_order_to_dict(x) for x in node.order_by]}
    if isinstance(node, Limit): return {"kind": "limit", "source": node_to_dict(node.source), "count": node.count}
    if isinstance(node, SetOperation): return {"kind": "set", "left": node_to_dict(node.left), "right": node_to_dict(node.right), "operation": node.operation, "all_rows": node.all_rows}
    if isinstance(node, Join): return {"kind": "join", "left": node_to_dict(node.left), "right": node_to_dict(node.right), "on": expr_to_dict(node.on), "fields": [_named_to_dict(x) for x in node.fields], "grain": _grain_to_dict(node.grain), "how": node.how}
    if isinstance(node, CollectSet): return {"kind": "collect_set", "source": node_to_dict(node.source), "group_by": [_named_to_dict(x) for x in node.group_by], "value": expr_to_dict(node.value), "alias": node.alias, "grain": _grain_to_dict(node.grain), "separator": node.separator}
    if isinstance(node, EventPattern): return {"kind": "event_pattern", "source": node_to_dict(node.source), "partition_by": [_named_to_dict(x) for x in node.partition_by], "order_by": [_order_to_dict(x) for x in node.order_by], "event": expr_to_dict(node.event), "occurrence": node.occurrence, "exact_count": node.exact_count, "require_last_event": node.require_last_event, "arrangement": node.arrangement}
    if isinstance(node, FollowEvent): return {"kind": "follow_event", "source": node_to_dict(node.source), "partition_by": [_named_to_dict(x) for x in node.partition_by], "order_by": [_order_to_dict(x) for x in node.order_by], "anchor": expr_to_dict(node.anchor), "target": expr_to_dict(node.target), "max_gap": node.max_gap, "between": [_named_to_dict(x) for x in node.between]}
    raise TypeError(type(node))


def node_from_dict(data: dict[str, Any]) -> Node:
    kind = data["kind"]
    if kind == "source": return Source(data["table"], _grain_from_dict(data["grain"]))
    if kind == "filter": return Filter(node_from_dict(data["source"]), expr_from_dict(data["predicate"]))
    if kind == "aggregate": return Aggregate(node_from_dict(data["source"]), tuple(_named_from_dict(x) for x in data["group_by"]), tuple(_metric_from_dict(x) for x in data["metrics"]), _grain_from_dict(data["grain"]))
    if kind == "rank": return Rank(node_from_dict(data["source"]), data["alias"], tuple(_order_from_dict(x) for x in data["order_by"]), tuple(expr_from_dict(x) for x in data.get("partition_by", [])), data.get("method", "row_number"))
    if kind == "window": return Window(node_from_dict(data["source"]), tuple(_window_from_dict(x) for x in data["fields"]))
    if kind == "project": return Project(node_from_dict(data["source"]), tuple(_named_from_dict(x) for x in data["fields"]), _grain_from_dict(data["grain"]) if data.get("grain") else None)
    if kind == "sort": return Sort(node_from_dict(data["source"]), tuple(_order_from_dict(x) for x in data["order_by"]))
    if kind == "limit": return Limit(node_from_dict(data["source"]), int(data["count"]))
    if kind == "set": return SetOperation(node_from_dict(data["left"]), node_from_dict(data["right"]), data["operation"], bool(data.get("all_rows", False)))
    if kind == "join": return Join(node_from_dict(data["left"]), node_from_dict(data["right"]), expr_from_dict(data["on"]), tuple(_named_from_dict(x) for x in data["fields"]), _grain_from_dict(data["grain"]), data.get("how", "inner"))
    if kind == "collect_set": return CollectSet(node_from_dict(data["source"]), tuple(_named_from_dict(x) for x in data["group_by"]), expr_from_dict(data["value"]), data["alias"], _grain_from_dict(data["grain"]), data.get("separator", "|"))
    if kind == "event_pattern": return EventPattern(node_from_dict(data["source"]), tuple(_named_from_dict(x) for x in data["partition_by"]), tuple(_order_from_dict(x) for x in data["order_by"]), expr_from_dict(data["event"]), int(data["occurrence"]), data.get("exact_count"), bool(data.get("require_last_event", False)), data.get("arrangement", "any"))
    if kind == "follow_event": return FollowEvent(node_from_dict(data["source"]), tuple(_named_from_dict(x) for x in data["partition_by"]), tuple(_order_from_dict(x) for x in data["order_by"]), expr_from_dict(data["anchor"]), expr_from_dict(data["target"]), int(data["max_gap"]), tuple(_named_from_dict(x) for x in data.get("between", [])))
    raise ValueError(f"unknown node kind: {kind}")