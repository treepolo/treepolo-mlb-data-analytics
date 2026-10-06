from __future__ import annotations

from typing import Iterable, Mapping

from .model import Binary, Boolean, Case, Column, IsNull, Literal, NamedExpr, PITCH_GRAIN, Project

# Savant zone numbers are fixed in the catcher's view: swap left/right columns.
ZONE_MIRROR = {1: 3, 3: 1, 4: 6, 6: 4, 7: 9, 9: 7, 11: 12, 12: 11, 13: 14, 14: 13}

# field -> rule. "negate": x -> -x; "axis_360": x -> 360 - x (0 stays 0); "zone_map": ZONE_MIRROR; "none": unchanged.
# Initial decided rules; the remaining candidate fields are decided from real data in task T2.8.
MIRROR_RULES: dict[str, str] = {
    "plate_x": "negate", "pfx_x": "negate", "release_pos_x": "negate", "vx0": "negate", "ax": "negate",
    "spin_axis": "axis_360", "zone": "zone_map",
}
_VALID_RULES = {"negate", "axis_360", "zone_map", "none"}


def mirror_to_right_handed_pitcher(source, carry_fields: Iterable[str], *, rules: Mapping[str, str] | None = None):
    """Mirror every row thrown by a left-handed pitcher into the right-handed-pitcher frame.

    This is the derived "same side / opposite side" view only; the four base groups are never mirrored.
    Adds ``mirrored`` (1 if the row was mirrored) and ``frame_group`` ('same_side' / 'opposite_side' / NULL when a
    hand is unknown). Fields not in the rule table (or with rule "none") pass through unchanged.
    """

    rules = dict(MIRROR_RULES if rules is None else rules)
    bad = {k: v for k, v in rules.items() if v not in _VALID_RULES}
    if bad:
        raise ValueError(f"Unknown mirror rules: {bad}")
    carry_fields = tuple(carry_fields)
    if "pitch_uid" not in carry_fields:
        raise ValueError("carry_fields must include pitch_uid (the grain key)")
    clash = sorted({"mirrored", "frame_group"} & set(carry_fields))
    if clash:
        raise ValueError(f"carry_fields must not repeat generated columns: {clash}")

    is_left = Binary(Column("p_throws"), "=", Literal("L"))
    fields: list[NamedExpr] = []
    for name in carry_fields:
        rule = rules.get(name, "none")
        column = Column(name)
        if rule == "negate":
            expr = Case(((is_left, Binary(Literal(0.0), "-", column)),), column)
        elif rule == "axis_360":
            expr = Case(((Boolean("and", (is_left, Binary(column, ">", Literal(0)))), Binary(Literal(360.0), "-", column)),), column)
        elif rule == "zone_map":
            expr = Case(tuple(
                (Boolean("and", (is_left, Binary(column, "=", Literal(k)))), Literal(v)) for k, v in ZONE_MIRROR.items()
            ), column)
        else:
            expr = column
        fields.append(NamedExpr(name, expr))
    known = Boolean("and", (IsNull(Column("p_throws"), True), IsNull(Column("stand"), True)))
    fields.append(NamedExpr("mirrored", Case(((is_left, Literal(1)),), Literal(0))))
    fields.append(NamedExpr("frame_group", Case((
        (Boolean("and", (known, Binary(Column("p_throws"), "=", Column("stand")))), Literal("same_side")),
        (Boolean("and", (known, Binary(Column("p_throws"), "!=", Column("stand")))), Literal("opposite_side")),
    ), None)))
    return Project(source, tuple(fields), PITCH_GRAIN)
