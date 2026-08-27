from __future__ import annotations

from typing import Any

from .analysis import Aggregate, Binary, Boolean, Column, Grain, Join, Metric, NamedExpr, SetOperation
from .web_analysis_common import RequestError


class ArsenalChangeSemanticsMixin:
    """Arsenal-change semantics that compare only entities observed in both periods."""

    @staticmethod
    def _arsenal_change_join_predicate(fields: tuple[str, ...]):
        terms = tuple(Binary(Column(field, "left"), "=", Column(field, "right")) for field in fields)
        if not terms:
            raise RequestError("Arsenal change requires at least one entity field")
        return terms[0] if len(terms) == 1 else Boolean("and", terms)

    def _period_entity_presence(
        self,
        filters: list[dict[str, Any]] | None,
        start: str,
        end: str,
        entities: tuple[str, ...],
    ):
        period_filters = list(filters or []) + [
            {"field": "game_date", "op": "ge", "value": start},
            {"field": "game_date", "op": "le", "value": end},
        ]
        source = self._filter_source(period_filters)
        return Aggregate(
            source,
            tuple(NamedExpr(field, Column(field)) for field in entities),
            (Metric("__period_pitch_rows", "count"),),
            Grain(entities, "period_entity_presence"),
        )

    def _common_period_entities(
        self,
        filters: list[dict[str, Any]] | None,
        period_a: dict[str, Any],
        period_b: dict[str, Any],
        entities: tuple[str, ...],
    ):
        first = self._period_entity_presence(
            filters, str(period_a["start"]), str(period_a["end"]), entities
        )
        second = self._period_entity_presence(
            filters, str(period_b["start"]), str(period_b["end"]), entities
        )
        return Join(
            first,
            second,
            self._arsenal_change_join_predicate(entities),
            tuple(NamedExpr(field, Column(field, "left")) for field in entities),
            Grain(entities, "common_period_entity"),
            "inner",
        )

    def _restrict_arsenal_set_to_common_entities(self, pitch_set, common_entities, entities: tuple[str, ...]):
        fields = tuple(NamedExpr(field, Column(field, "left")) for field in entities) + (
            NamedExpr("pitch_type", Column("pitch_type", "left")),
        )
        return Join(
            pitch_set,
            common_entities,
            self._arsenal_change_join_predicate(entities),
            fields,
            Grain(entities + ("pitch_type",), "arsenal_pitch"),
            "inner",
        )

    def _arsenal_change(self, payload: dict[str, Any]) -> dict[str, Any]:
        entities = self._entity_fields(payload)
        min_usage = float(payload.get("min_usage", 0.05))
        period_a = payload.get("period_a") or {}
        period_b = payload.get("period_b") or {}
        for label, period in (("A", period_a), ("B", period_b)):
            if not period.get("start") or not period.get("end"):
                raise RequestError(f"Period {label} requires start and end dates")

        filters = payload.get("filters")
        first = self._period_pitch_set(
            filters, str(period_a["start"]), str(period_a["end"]), entities, min_usage
        )
        second = self._period_pitch_set(
            filters, str(period_b["start"]), str(period_b["end"]), entities, min_usage
        )
        common_entities = self._common_period_entities(filters, period_a, period_b, entities)
        first = self._restrict_arsenal_set_to_common_entities(first, common_entities, entities)
        second = self._restrict_arsenal_set_to_common_entities(second, common_entities, entities)

        allowed = entities + ("pitch_type",)
        added = self._apply_result_sort(SetOperation(second, first, "except"), payload, allowed)
        removed = self._apply_result_sort(SetOperation(first, second, "except"), payload, allowed)
        return {
            "sections": [
                {"title": "新增球種 Added Pitches", **self._execute(added)},
                {"title": "移除球種 Removed Pitches", **self._execute(removed)},
            ]
        }
