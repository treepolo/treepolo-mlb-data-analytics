from __future__ import annotations

from typing import Mapping

from .model import Binary, Boolean, Case, Column, InList, Literal

OUTCOME_CATEGORIES = (
    "ball", "called_strike", "whiff", "foul_tip", "foul", "hit_by_pitch", "in_play_home_run", "in_play_triple",
    "in_play_double", "in_play_single", "in_play_out", "in_play_error_other", "bunt", "unclassified",
)
IN_PLAY_OUTS = (
    "field_out", "force_out", "grounded_into_double_play", "double_play", "sac_fly", "sac_fly_double_play",
    "fielders_choice_out", "triple_play",
)
IN_PLAY_OTHER = ("field_error", "fielders_choice", "catcher_interf")


def outcome_category_expr(merge: Mapping[str, str] | None = None):
    """Single-pitch outcome category (mutually exclusive). Unmapped values become ``unclassified``.

    ``merge`` renames/merges categories, e.g. {"in_play_single": "in_play_hit", "in_play_double": "in_play_hit"}.
    The expression only classifies; exclusion of bunts / non-pitch rows is done by ``apply_exclusions``.
    """

    merge = dict(merge or {})
    for key, value in merge.items():
        if key not in OUTCOME_CATEGORIES:
            raise ValueError(f"Unknown outcome category in merge: {key!r}")
        if not isinstance(value, str) or not value:
            raise ValueError(f"merge target for {key!r} must be a non-empty string")

    def name(category: str) -> Literal:
        return Literal(merge.get(category, category))

    d, e = Column("description"), Column("events")

    def lit_in(col, values):
        return InList(col, tuple(Literal(v) for v in values))

    in_play = Binary(d, "=", Literal("hit_into_play"))

    def play(event_values, category):
        return (Boolean("and", (in_play, lit_in(e, event_values))), name(category))

    return Case((
        (lit_in(d, ("foul_bunt", "missed_bunt", "bunt_foul_tip")), name("bunt")),
        (Boolean("and", (in_play, Boolean("or", (
            lit_in(e, ("sac_bunt", "sac_bunt_double_play")),
            Binary(Column("des"), "LIKE", Literal("%bunt%")),
            Binary(Column("des"), "LIKE", Literal("%Bunt%")),
        )))), name("bunt")),
        (lit_in(d, ("ball", "blocked_ball")), name("ball")),
        (Binary(d, "=", Literal("called_strike")), name("called_strike")),
        (lit_in(d, ("swinging_strike", "swinging_strike_blocked")), name("whiff")),
        (Binary(d, "=", Literal("foul_tip")), name("foul_tip")),
        (Binary(d, "=", Literal("foul")), name("foul")),
        (Binary(d, "=", Literal("hit_by_pitch")), name("hit_by_pitch")),
        play(("home_run",), "in_play_home_run"),
        play(("triple",), "in_play_triple"),
        play(("double",), "in_play_double"),
        play(("single",), "in_play_single"),
        play(IN_PLAY_OUTS, "in_play_out"),
        play(IN_PLAY_OTHER, "in_play_error_other"),
    ), name("unclassified"))
