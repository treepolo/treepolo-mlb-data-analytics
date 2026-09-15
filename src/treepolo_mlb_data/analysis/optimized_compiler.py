from __future__ import annotations

from .compiler import SQLCompiler, quote_ident
from .model import FollowEvent, Node


class OptimizedSQLCompiler(SQLCompiler):
    """SQL compiler with bounded-window execution for follow-event queries.

    The original FollowEvent lowering joined every anchor to candidate target rows
    and then evaluated correlated EXISTS subqueries for each between predicate.
    That is semantically correct, but SQLite can turn the full-season query into
    a very expensive nested-loop plan.  Here we keep the same semantics while
    doing the expensive work with partition-local window scans:

    * mark anchor/target/between predicates once;
    * find the first target in the next ``max_gap`` rows with a bounded MIN
      window over the deterministic per-partition sequence;
    * compute cumulative counts for between predicates once;
    * join each anchor to exactly one target row.

    DuckDB accepts the same SQL, so both analytical backends continue to execute
    an identical relational plan.
    """

    def _node(self, node: Node) -> tuple[str, list[object]]:
        if not isinstance(node, FollowEvent):
            return super()._node(node)

        child, child_params = self._node(node.source)
        ctes, part_names, base_params = self._prepared_ordered_stream(
            child, child_params, node.partition_by, node.order_by
        )

        anchor_sql, anchor_params = self._expr(node.anchor, default_alias="b")
        target_sql, target_params = self._expr(node.target, default_alias="b")

        marked_fields = [
            f"CASE WHEN {anchor_sql} THEN 1 ELSE 0 END AS {quote_ident('__ta_is_anchor')}",
            f"CASE WHEN {target_sql} THEN 1 ELSE 0 END AS {quote_ident('__ta_is_target')}",
        ]
        between_params: list[object] = []
        between_internal: list[tuple[str, str, str]] = []
        for index, item in enumerate(node.between):
            predicate_sql, predicate_params = self._expr(item.expr, default_alias="b")
            marker = f"__ta_between_marker_{index}"
            cumulative = f"__ta_between_cumulative_{index}"
            marked_fields.append(
                f"CASE WHEN {predicate_sql} THEN 1 ELSE 0 END AS {quote_ident(marker)}"
            )
            between_params.extend(predicate_params)
            between_internal.append((item.alias, marker, cumulative))

        marked = f"SELECT b.*, {', '.join(marked_fields)} FROM base AS b"

        partition = ", ".join(quote_ident(name) for name in part_names)
        window_prefix = f"PARTITION BY {partition} " if partition else ""
        seq = quote_ident("__ta_seq")
        is_target = quote_ident("__ta_is_target")
        target_seq = quote_ident("__ta_target_seq")
        max_gap = int(node.max_gap)

        scanned_fields = [
            (
                f"MIN(CASE WHEN {is_target} = 1 THEN {seq} END) OVER ("
                f"{window_prefix}ORDER BY {seq} "
                f"ROWS BETWEEN 1 FOLLOWING AND {max_gap} FOLLOWING) AS {target_seq}"
            )
        ]
        for _, marker, cumulative in between_internal:
            scanned_fields.append(
                f"SUM({quote_ident(marker)}) OVER ("
                f"{window_prefix}ORDER BY {seq} "
                f"ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) "
                f"AS {quote_ident(cumulative)}"
            )
        scanned = f"SELECT m.*, {', '.join(scanned_fields)} FROM marked AS m"

        anchors = (
            f"SELECT * FROM scanned WHERE {quote_ident('__ta_is_anchor')} = 1 "
            f"AND {target_seq} IS NOT NULL"
        )
        same_partition = self._same_partition("a", "t", part_names)

        output_between: list[str] = []
        for alias, marker, cumulative in between_internal:
            # cumulative(target) includes the target row itself and
            # cumulative(anchor) includes the anchor row itself. Subtracting the
            # target marker and the anchor cumulative therefore leaves exactly
            # the rows strictly between anchor and target.
            delta = (
                f"t.{quote_ident(cumulative)} - t.{quote_ident(marker)} "
                f"- a.{quote_ident(cumulative)}"
            )
            output_between.append(
                f"CASE WHEN ({delta}) > 0 THEN 1 ELSE 0 END AS {quote_ident(alias)}"
            )
        extras = ", " + ", ".join(output_between) if output_between else ""

        sql = (
            f"WITH {ctes}, marked AS ({marked}), scanned AS ({scanned}), "
            f"anchors AS ({anchors}) "
            f"SELECT t.*, a.{seq} AS {quote_ident('__ta_anchor_seq')}{extras} "
            f"FROM anchors AS a JOIN scanned AS t ON {same_partition} "
            f"AND t.{seq} = a.{target_seq}"
        )
        params = base_params + anchor_params + target_params + between_params
        return sql, params


__all__ = ["OptimizedSQLCompiler"]
