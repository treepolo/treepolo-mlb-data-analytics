from __future__ import annotations

from typing import Iterable

from ._keys import PA
from .model import Binary, Boolean, Case, Column, Filter, InList, IsNull, Literal, Window, WindowField

NON_PITCH_DESCRIPTIONS = ("automatic_ball", "automatic_strike", "pitchout", "swinging_pitchout", "intent_ball")
NON_PITCH_TYPES = ("PO", "IN", "AB", "AS")
# Bunt attempts recorded in `description` (bunt_foul_tip was found in the real 2023-2026 data).
BUNT_DESCRIPTIONS = ("foul_bunt", "missed_bunt", "bunt_foul_tip")
BUNT_POLICIES = ("exclude_plate_appearance", "exclude_pitch", "keep")


def _flag(*terms):
    """NULL-safe 1/0 flag: NULL or false -> 0."""
    return Case(((Boolean("or", tuple(terms)), Literal(1)),), Literal(0))


def exclude_non_pitch_rows(
    source, descriptions: Iterable[str] = NON_PITCH_DESCRIPTIONS, pitch_types: Iterable[str] = NON_PITCH_TYPES
):
    """Drop rows that are not real pitches (pitch-clock automatic balls/strikes, pitchouts, intentional balls)."""

    # NULL trap: NULL NOT IN (...) is NULL and would drop the row, so test IS NULL explicitly.
    return Filter(source, Boolean("and", (
        Boolean("or", (IsNull(Column("description")), InList(Column("description"), tuple(Literal(x) for x in descriptions), True))),
        Boolean("or", (IsNull(Column("pitch_type")), InList(Column("pitch_type"), tuple(Literal(x) for x in pitch_types), True))),
    )))


def bunt_pitch_flag():
    """1 when this pitch row shows a bunt, else 0 (never NULL)."""

    return _flag(
        InList(Column("description"), tuple(Literal(x) for x in BUNT_DESCRIPTIONS)),
        InList(Column("events"), (Literal("sac_bunt"), Literal("sac_bunt_double_play"))),
        Binary(Column("des"), "LIKE", Literal("%bunt%")),
        Binary(Column("des"), "LIKE", Literal("%Bunt%")),  # DuckDB LIKE is case-sensitive
    )


def exclude_bunt_plate_appearances(source):
    """Drop every pitch of any plate appearance that contains a bunt."""

    flagged = Window(source, (WindowField("__ta_is_bunt", "max", (bunt_pitch_flag(),), PA),))
    return Filter(flagged, Binary(Column("__ta_is_bunt"), "=", Literal(0)))


def apply_exclusions(
    source, *, bunt_policy: str = "exclude_plate_appearance",
    non_pitch_descriptions: Iterable[str] = NON_PITCH_DESCRIPTIONS, non_pitch_types: Iterable[str] = NON_PITCH_TYPES,
):
    """Bunt rule first (it needs whole plate appearances), then non-pitch rows."""

    if bunt_policy == "exclude_plate_appearance":
        node = exclude_bunt_plate_appearances(source)
    elif bunt_policy == "exclude_pitch":
        node = Filter(source, Binary(bunt_pitch_flag(), "=", Literal(0)))
    elif bunt_policy == "keep":
        node = source
    else:
        raise ValueError(f"bunt_policy must be one of {BUNT_POLICIES}, got {bunt_policy!r}")
    return exclude_non_pitch_rows(node, non_pitch_descriptions, non_pitch_types)
