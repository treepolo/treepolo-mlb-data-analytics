from __future__ import annotations

from dataclasses import dataclass, field

from .model import Binary, Boolean, Column, Expr, InList, Literal


@dataclass(slots=True)
class SemanticRegistry:
    _expressions: dict[str, Expr] = field(default_factory=dict)

    def register(self, name: str, expression: Expr) -> None:
        if not name:
            raise ValueError("semantic name is required")
        self._expressions[name] = expression

    def resolve(self, name: str) -> Expr:
        try:
            return self._expressions[name]
        except KeyError as exc:
            raise KeyError(f"unknown semantic: {name}") from exc

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._expressions))


def default_registry() -> SemanticRegistry:
    registry = SemanticRegistry()
    registry.register("four_seam_fastball", Binary(Column("pitch_type"), "=", Literal("FF")))
    registry.register("sweeper", Binary(Column("pitch_type"), "=", Literal("ST")))
    registry.register("in_strike_zone", InList(Column("zone"), tuple(Literal(x) for x in range(1, 10))))
    registry.register("whiff", InList(Column("description"), tuple(Literal(x) for x in (
        "swinging_strike", "swinging_strike_blocked", "missed_bunt",
    ))))
    registry.register("swing", InList(Column("description"), tuple(Literal(x) for x in (
        "swinging_strike", "swinging_strike_blocked", "missed_bunt", "foul", "foul_bunt",
        "foul_tip", "hit_into_play",
    ))))
    registry.register("right_on_right", Boolean("and", (
        Binary(Column("p_throws"), "=", Literal("R")),
        Binary(Column("stand"), "=", Literal("R")),
    )))
    return registry
