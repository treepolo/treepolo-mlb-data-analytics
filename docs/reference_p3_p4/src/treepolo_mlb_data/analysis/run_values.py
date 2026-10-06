from __future__ import annotations

from typing import Mapping

from .model import (
    Aggregate, Binary, Boolean, Case, Column, Filter, Grain, IsNull, Literal, Metric, NamedExpr, OrderKey, PITCH_GRAIN,
    Project, Window, WindowField,
)

C = Column
HALF = (C("game_pk"), C("inning"), C("inning_topbot"))  # one half-inning; `inning` is TEXT, never do arithmetic on it
GAME = (C("game_pk"),)
HALF_ORDER = (OrderKey(C("at_bat_number")), OrderKey(C("pitch_number")))
STATE_GRAIN = Grain(("state_code",), "base_out_count_state")
HALF_INNING_KEYS = ("game_pk", "inning", "inning_topbot")
RUN_KEYS = HALF_INNING_KEYS + ("at_bat_number", "pitch_number")
RUN_CONTEXT_REQUIRED = (
    "pitch_uid",) + RUN_KEYS + (
    "balls", "strikes", "outs_when_up", "on_1b", "on_2b", "on_3b", "bat_score", "post_bat_score", "post_home_score",
    "post_away_score",
)
RUN_CONTEXT_FIELDS = ("state_code", "next_state_code", "runs_to_end", "runs_on_pitch", "is_walkoff_half")
STATE_COUNT = 288  # 4 balls x 3 strikes x 3 outs x 8 base states


def all_state_codes() -> list[int]:
    return [b * 1000 + s * 100 + o * 10 + bases for b in range(4) for s in range(3) for o in range(3) for bases in range(8)]


def decode_state(code: int) -> dict[str, int]:
    code = int(code)
    return {"balls": code // 1000, "strikes": (code // 100) % 10, "outs": (code // 10) % 10, "bases": code % 10}


def bases_expr():
    """Base state 0-7: bit 1 = runner on first, 2 = second, 4 = third (the columns are NULL when the base is empty)."""
    one = Case(((IsNull(C("on_1b"), True), Literal(1)),), Literal(0))
    two = Case(((IsNull(C("on_2b"), True), Literal(2)),), Literal(0))
    three = Case(((IsNull(C("on_3b"), True), Literal(4)),), Literal(0))
    return Binary(Binary(one, "+", two), "+", three)


def state_code_expr():
    """balls*1000 + strikes*100 + outs*10 + bases."""
    count = Binary(Binary(C("balls"), "*", Literal(1000)), "+", Binary(C("strikes"), "*", Literal(100)))
    return Binary(Binary(count, "+", Binary(C("outs_when_up"), "*", Literal(10))), "+", bases_expr())


def run_context(source, carry_fields):
    """Add RUN_CONTEXT_FIELDS to a pitch-grain node.

    ``source`` must contain EVERY row of each half-inning (apply exclusions only afterwards): ``next_state_code`` is the
    state of the next row in the half-inning (NULL on the last row) and ``runs_to_end`` the runs the batting team scores
    from this pitch to the end of the half-inning. ``is_walkoff_half`` is 1 for the last half-inning of a game that the
    home team wins in the bottom half (its end is cut short, so its values are censored and must not be used).
    Output columns: pitch_uid, carry_fields, RUN_KEYS, RUN_CONTEXT_FIELDS.
    """

    carry = tuple(dict.fromkeys(("pitch_uid",) + tuple(carry_fields) + RUN_KEYS))
    clash = sorted(set(carry) & set(RUN_CONTEXT_FIELDS))
    if clash:
        raise ValueError(f"carry_fields must not contain run-context field names: {clash}")
    base = tuple(dict.fromkeys(RUN_CONTEXT_REQUIRED + carry))
    n0 = Project(source, tuple(NamedExpr(f, C(f)) for f in base) + (NamedExpr("state_code", state_code_expr()),), PITCH_GRAIN)
    n1 = Window(n0, (
        WindowField("next_state_code", "lead", (C("state_code"),), HALF, HALF_ORDER),
        WindowField("__ta_end_score", "max", (C("post_bat_score"),), HALF),
        WindowField("__ta_half_last_ab", "max", (C("at_bat_number"),), HALF),
        WindowField("__ta_game_last_ab", "max", (C("at_bat_number"),), GAME),
        WindowField("__ta_final_home", "max", (C("post_home_score"),), GAME),
        WindowField("__ta_final_away", "max", (C("post_away_score"),), GAME),
    ))
    walkoff = Case(((Boolean("and", (
        Binary(C("inning_topbot"), "=", Literal("Bot")),
        Binary(C("__ta_half_last_ab"), "=", C("__ta_game_last_ab")),
        Binary(C("__ta_final_home"), ">", C("__ta_final_away")),
    )), Literal(1)),), Literal(0))
    return Project(n1, tuple(NamedExpr(f, C(f)) for f in carry) + (
        NamedExpr("state_code", C("state_code")),
        NamedExpr("next_state_code", C("next_state_code")),
        NamedExpr("runs_to_end", Binary(C("__ta_end_score"), "-", C("bat_score"))),
        NamedExpr("runs_on_pitch", Binary(C("post_bat_score"), "-", C("bat_score"))),
        NamedExpr("is_walkoff_half", walkoff),
    ), PITCH_GRAIN)


def re_table_node(context):
    """Run expectancy per state with the sums needed for a half-inning-clustered SE (see stats.cluster_mean_se).

    Two-level aggregate: level 1 per (state, half-inning), level 2 per state. Walk-off half-innings are excluded.
    Result columns: state_code, n, s, ss, sn, nn, g (re = s / n).
    """

    kept = Filter(context, Binary(C("is_walkoff_half"), "=", Literal(0)))
    keys = ("state_code",) + HALF_INNING_KEYS
    level1 = Aggregate(kept, tuple(NamedExpr(k, C(k)) for k in keys),
                       (Metric("n", "count"), Metric("s", "sum", C("runs_to_end"))), Grain(keys, "state_half_inning"))
    return Aggregate(level1, (NamedExpr("state_code", C("state_code")),), (
        Metric("n", "sum", C("n")), Metric("s", "sum", C("s")), Metric("ss", "sum", Binary(C("s"), "*", C("s"))),
        Metric("sn", "sum", Binary(C("s"), "*", C("n"))), Metric("nn", "sum", Binary(C("n"), "*", C("n"))),
        Metric("g", "count"),
    ), STATE_GRAIN)


def lookup_tree(code_expr, mapping: Mapping[int, float]):
    """Balanced CASE tree (depth about 9 for 288 keys) returning mapping[code]; NULL for a key that is not in the table."""

    items = sorted((int(k), float(v)) for k, v in mapping.items())
    if not items:
        raise ValueError("empty lookup table")

    def build(lo: int, hi: int):
        if hi - lo == 1:
            key, value = items[lo]
            return Case(((Binary(code_expr, "=", Literal(key)), Literal(value)),), None)
        mid = (lo + hi) // 2
        return Case(((Binary(code_expr, "<", Literal(items[mid][0])), build(lo, mid)),), build(mid, hi))

    return build(0, len(items))


def pitch_value_expr(re_by_state: Mapping[int, float]):
    """Batting-team run value of one pitch: RE(next state, 0 after the last pitch of the half-inning) + runs on the pitch - RE(state)."""

    nxt = Case(((IsNull(C("next_state_code")), Literal(0.0)),), lookup_tree(C("next_state_code"), re_by_state))
    return Binary(Binary(nxt, "+", C("runs_on_pitch")), "-", lookup_tree(C("state_code"), re_by_state))


def pitch_value_field(re_by_state: Mapping[int, float]) -> NamedExpr:
    """`pitch_value` column: NULL inside a walk-off half-inning (censored), NULL when a state is missing from the table."""

    return NamedExpr("pitch_value", Case(((Binary(C("is_walkoff_half"), "=", Literal(1)), Literal(None)),), pitch_value_expr(re_by_state)))
