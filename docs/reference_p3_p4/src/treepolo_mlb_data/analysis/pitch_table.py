from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

from . import run_values as rv
from ._keys import ORDER, PA
from .exclusions import apply_exclusions
from .handedness import mirror_to_right_handed_pitcher
from .model import (
    Aggregate, Binary, Boolean, Case, Column, Filter, Grain, InList, IsNull, Literal, Metric, NamedExpr, PITCH_GRAIN, Project,
    Window, WindowField, WindowFrame,
)
from .outcomes import outcome_category_expr
from .sequence import SEQUENCE_COLUMNS, sequence_features

C = Column
BASE_COLUMNS = (
    "pitch_uid", "game_pk", "at_bat_number", "pitch_number", "game_year", "pitcher", "batter", "pitch_type", "description",
    "events", "des", "p_throws", "stand", "balls", "strikes", "outs_when_up", "on_1b", "on_2b", "on_3b", "zone", "plate_x",
    "plate_z", "release_speed", "spin_axis", "delta_run_exp",
)
SWING = ("whiff", "foul_tip", "foul", "in_play_home_run", "in_play_triple", "in_play_double", "in_play_single", "in_play_out",
         "in_play_error_other")
IN_PLAY = tuple(c for c in SWING if c.startswith("in_play"))
TAKE = ("ball", "called_strike", "hit_by_pitch")
# Discrete columns that may define outcome-table cells (continuous columns such as plate_x have to be binned first).
CELL_FIELD_CHOICES = (
    "balls", "strikes", "outs_when_up", "bases", "pitch_index_in_pa", "pitch_type", "zone", "loc_x_bin", "loc_z_bin",
    "prev1_pitch_type", "prev2_pitch_type", "prev3_pitch_type", "prev4_pitch_type", "prev5_pitch_type", "history_key",
    "streak_pos", "prev1_description", "prior_same_type_count", "game_year", "pitcher", "batter", "p_throws", "stand", "state_code",
)
# Strata for same-point streak comparisons. Anything that describes the previous pitches (prev*, history_key, streak_pos)
# is excluded on purpose: it would put k=1 and k>=2 pitches into different strata and nothing would be comparable.
STRATA_FIELD_CHOICES = (
    "pitch_index_in_pa", "balls", "strikes", "outs_when_up", "bases", "prior_same_type_count", "game_year", "p_throws", "stand",
    "pitcher", "batter",
)
FILTER_OPS = ("eq", "ne", "in", "not_in", "gt", "gte", "lt", "lte")
_BINARY = {"eq": "=", "ne": "!=", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}


def apply_filters(node, filters: Sequence[Mapping[str, Any]]):
    """AND together simple conditions ({"field","op","value"}) on columns of the pitch table."""

    terms = []
    for item in filters or ():
        field, op, value = item.get("field"), item.get("op"), item.get("value")
        if not isinstance(field, str) or op not in FILTER_OPS:
            raise ValueError(f"filter needs a field and an op in {FILTER_OPS}: {item!r}")
        if op in ("in", "not_in"):
            if not isinstance(value, (list, tuple)) or not value:
                raise ValueError(f"filter op {op} needs a non-empty list: {item!r}")
            terms.append(InList(C(field), tuple(Literal(v) for v in value), op == "not_in"))
        else:
            if isinstance(value, (list, tuple, dict)) or value is None:
                raise ValueError(f"filter op {op} needs a single value: {item!r}")
            terms.append(Binary(C(field), _BINARY[op], Literal(value)))
    return Filter(node, Boolean("and", tuple(terms))) if terms else node


def build_pitch_table(
    source, *, bunt_policy: str = "exclude_plate_appearance", memory: int = 2, re_by_state: Mapping[int, float] | None = None,
    mirror: bool = False, prior_same_type_count: bool = False, loc_x_edges: Sequence[float] = (), loc_z_edges: Sequence[float] = (),
    extra_columns: Iterable[str] = (), filters: Sequence[Mapping[str, Any]] = (),
):
    """One row per kept pitch (grain pitch_uid) with everything P3 and P4 need.

    Order: run context (needs whole half-innings) -> exclusions -> [prior_same_type_count] -> [handedness mirror]
    -> sequence features -> derived columns -> filters. Filters come last on purpose: streaks and histories are
    computed on complete plate appearances, then the pitches are selected.

    Output columns: BASE_COLUMNS + extra_columns, sequence columns (pitch_index_in_pa, history_key, prev{k}_pitch_type,
    prev1_description, speed_diff_prev1, dx_prev1, dz_prev1, streak_pos, history_complete), outcome (the 14 categories),
    hand_group (e.g. RvL), bases (0-7), loc_x_bin / loc_z_bin when edges are given, and with re_by_state also
    state_code, next_state_code, runs_to_end, runs_on_pitch, is_walkoff_half and pitch_value; with mirror also
    mirrored and frame_group; with prior_same_type_count also that column.
    """

    carry = tuple(dict.fromkeys(tuple(BASE_COLUMNS) + tuple(extra_columns)))
    node = source
    if re_by_state is not None:
        node = rv.run_context(node, carry)
        carry = carry + tuple(f for f in ("game_pk", "inning", "inning_topbot") if f not in carry) + rv.RUN_CONTEXT_FIELDS
    node = apply_exclusions(node, bunt_policy=bunt_policy)
    if prior_same_type_count:
        node = Window(node, (WindowField("prior_same_type_count", "count", (), PA + (C("pitch_type"),), ORDER, WindowFrame(None, -1)),))
        carry = carry + ("prior_same_type_count",)
    if mirror:
        node = mirror_to_right_handed_pitcher(node, carry)
        carry = carry + ("mirrored", "frame_group")
    node = sequence_features(node, carry, memory=memory)
    produced = tuple(SEQUENCE_COLUMNS) + tuple(f"prev{k}_pitch_type" for k in range(1, memory + 1))
    fields = [NamedExpr(f, C(f)) for f in carry + produced]
    fields.append(NamedExpr("outcome", outcome_category_expr()))
    fields.append(NamedExpr("hand_group", Binary(Binary(C("p_throws"), "||", Literal("v")), "||", C("stand"))))
    fields.append(NamedExpr("bases", rv.bases_expr()))
    if re_by_state is not None:
        fields.append(rv.pitch_value_field(re_by_state))
    for name, edges, column in (("loc_x_bin", loc_x_edges, "plate_x"), ("loc_z_bin", loc_z_edges, "plate_z")):
        if edges:
            col = C(column)
            branches = [(IsNull(col), Literal(None))] + [(Binary(col, "<", Literal(float(e))), Literal(i)) for i, e in enumerate(edges)]
            fields.append(NamedExpr(name, Case(tuple(branches), Literal(len(edges)))))
    return apply_filters(Project(node, tuple(fields), PITCH_GRAIN), filters)


def pitch_table_columns(*, memory: int = 2, with_value: bool = False, mirror: bool = False, prior_same_type_count: bool = False,
                        loc_x: bool = False, loc_z: bool = False, extra_columns: Iterable[str] = ()) -> set[str]:
    """Names of the columns build_pitch_table produces (for validating field lists before running)."""

    names = set(BASE_COLUMNS) | set(extra_columns) | set(SEQUENCE_COLUMNS) | {f"prev{k}_pitch_type" for k in range(1, memory + 1)}
    names |= {"outcome", "hand_group", "bases"}
    if with_value:
        names |= set(rv.RUN_CONTEXT_FIELDS) | {"pitch_value", "inning", "inning_topbot"}
    if mirror:
        names |= {"mirrored", "frame_group"}
    if prior_same_type_count:
        names.add("prior_same_type_count")
    if loc_x:
        names.add("loc_x_bin")
    if loc_z:
        names.add("loc_z_bin")
    return names


# ---------------------------------------------------------------------------------------------- rates (streak curves)
RATE_NAMES = (
    "whiff_per_swing", "swing_rate", "called_strike_per_take", "foul_per_swing", "in_play_per_swing", "hr_per_pitch",
    "hr_per_in_play", "mean_pitch_value",
)


def rate_terms(rate: str, *, foul_tip_is_whiff: bool = False):
    """(eligible, x): 1/0 flag of the pitches the rate is computed over, and the per-pitch value x (a 1/0 flag or a number)."""

    o = C("outcome")

    def isin(values):
        return InList(o, tuple(Literal(v) for v in values))

    def flag(predicate):
        return Case(((predicate, Literal(1)),), Literal(0))

    whiff = ("whiff", "foul_tip") if foul_tip_is_whiff else ("whiff",)
    table = {
        "whiff_per_swing": (flag(isin(SWING)), flag(isin(whiff))),
        "swing_rate": (Literal(1), flag(isin(SWING))),
        "called_strike_per_take": (flag(isin(TAKE)), flag(Binary(o, "=", Literal("called_strike")))),
        "foul_per_swing": (flag(isin(SWING)), flag(isin(("foul", "foul_tip")))),
        "in_play_per_swing": (flag(isin(SWING)), flag(isin(IN_PLAY))),
        "hr_per_pitch": (Literal(1), flag(Binary(o, "=", Literal("in_play_home_run")))),
        "hr_per_in_play": (flag(isin(IN_PLAY)), flag(Binary(o, "=", Literal("in_play_home_run")))),
        "mean_pitch_value": (flag(IsNull(C("pitch_value"), True)), C("pitch_value")),
    }
    if rate not in table:
        raise ValueError(f"rate must be one of {RATE_NAMES}")
    return table[rate]


def streak_cells_node(
    table, *, group_fields: Sequence[str], strata_fields: Sequence[str], rate: str, kmax: int, pitch_types: Sequence[str] = (),
    foul_tip_is_whiff: bool = False, cluster_field: str | None = None,
):
    """Per (group, pitch type, strata, streak bucket k [, cluster]): n, sx, sxx of the rate's per-pitch value x.

    k = streak_pos, with kmax standing for "kmax or more". Rows without pitch type or streak position are dropped.
    """

    eligible, x = rate_terms(rate, foul_tip_is_whiff=foul_tip_is_whiff)
    bucket = Case(((Binary(C("streak_pos"), ">=", Literal(kmax)), Literal(kmax)),), C("streak_pos"))
    keep_terms = [IsNull(C("pitch_type"), True), IsNull(C("streak_pos"), True)]
    if pitch_types:
        keep_terms.append(InList(C("pitch_type"), tuple(Literal(t) for t in pitch_types)))
    keys = tuple(group_fields) + ("pitch_type",) + tuple(strata_fields)
    extra = ((NamedExpr("cluster", C(cluster_field)),) if cluster_field else ())
    projected = Project(Filter(table, Boolean("and", tuple(keep_terms))),
                        (NamedExpr("pitch_uid", C("pitch_uid")),) + tuple(NamedExpr(f, C(f)) for f in keys) + extra + (
        NamedExpr("k", bucket), NamedExpr("__e", eligible),
        NamedExpr("__x", Case(((Binary(eligible, "=", Literal(1)), x),), Literal(0)))), PITCH_GRAIN)
    group_keys = keys + ("k",) + (("cluster",) if cluster_field else ())
    return Aggregate(projected, tuple(NamedExpr(f, C(f)) for f in group_keys), (
        Metric("n", "sum", C("__e")), Metric("sx", "sum", Binary(C("__e"), "*", C("__x"))),
        Metric("sxx", "sum", Binary(Binary(C("__e"), "*", C("__x")), "*", C("__x"))),
    ), Grain(group_keys, "streak_cell"))


# ---------------------------------------------------------------------------------------------- outcome cells
def outcome_cells_node(table, *, cell_fields: Sequence[str], cluster_fields: Sequence[str], categories: Sequence[str]):
    """Per cell: n, outcome counts c_<category>, and pitch-value sums for a cluster-robust SE of the mean value.

    Result columns: cell fields, n, nv (pitches with a value), sv (sum of values), ssv, snv, nnv, g (clusters with a
    value) and c_<category> for every category. Two-level aggregate: level 1 per (cell, cluster), level 2 per cell.
    """

    keys = tuple(cell_fields) + tuple(cluster_fields)
    level1 = Aggregate(table, tuple(NamedExpr(f, C(f)) for f in keys), (
        Metric("n", "count"), Metric("nv", "count", C("pitch_value")), Metric("sv", "sum", C("pitch_value")),
    ) + tuple(Metric(f"c_{c}", "sum", Case(((Binary(C("outcome"), "=", Literal(c)), Literal(1)),), Literal(0))) for c in categories),
        Grain(keys, "cell_cluster"))
    sv0 = Case(((IsNull(C("sv"), True), C("sv")),), Literal(0.0))
    return Aggregate(level1, tuple(NamedExpr(f, C(f)) for f in cell_fields), (
        Metric("n", "sum", C("n")), Metric("nv", "sum", C("nv")), Metric("sv", "sum", sv0),
        Metric("ssv", "sum", Binary(sv0, "*", sv0)), Metric("snv", "sum", Binary(sv0, "*", C("nv"))),
        Metric("nnv", "sum", Binary(C("nv"), "*", C("nv"))),
        Metric("g", "sum", Case(((Binary(C("nv"), ">", Literal(0)), Literal(1)),), Literal(0))),
    ) + tuple(Metric(f"c_{c}", "sum", C(f"c_{c}")) for c in categories), Grain(tuple(cell_fields), "cell"))
