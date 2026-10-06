from __future__ import annotations

from ._keys import ORDER, PA
from .model import (
    Binary, Boolean, Case, Column, IsNull, Literal, NamedExpr, PITCH_GRAIN, Project, Window, WindowField, WindowFrame,
)

SEQUENCE_COLUMNS = (
    "pitch_index_in_pa", "history_key", "prev1_description", "speed_diff_prev1", "dx_prev1", "dz_prev1",
    "streak_pos", "history_complete",
)


def sequence_features(source, carry_fields, *, memory: int = 2, type_field: str = "pitch_type"):
    """Add per-pitch sequence columns (previous pitches, same-type streak position) to a pitch-grain node.

    Apply ``apply_exclusions`` first so "previous pitch" means the previous real pitch. ``carry_fields`` are the
    source columns to keep and must include ``pitch_uid`` (the grain key).
    """

    if not isinstance(memory, int) or isinstance(memory, bool) or not 1 <= memory <= 5:
        raise ValueError("memory must be an integer between 1 and 5")
    carry_fields = tuple(carry_fields)
    if "pitch_uid" not in carry_fields:
        raise ValueError("carry_fields must include pitch_uid (the grain key)")
    produced = set(SEQUENCE_COLUMNS) | {f"prev{k}_pitch_type" for k in range(1, memory + 1)}
    clash = sorted(produced & set(carry_fields))
    if clash:
        raise ValueError(f"carry_fields must not repeat generated columns: {clash}")

    C = Column
    layer1 = [WindowField("pitch_index_in_pa", "row_number", (), PA, ORDER)]
    for k in range(1, memory + 1):
        layer1.append(WindowField(f"prev{k}_pitch_type", "lag", (C(type_field), Literal(k)), PA, ORDER))
    for alias, col in (("prev1_release_speed", "release_speed"), ("prev1_plate_x", "plate_x"),
                       ("prev1_plate_z", "plate_z"), ("prev1_description", "description")):
        layer1.append(WindowField(alias, "lag", (C(col), Literal(1)), PA, ORDER))
    n1 = Window(source, tuple(layer1))

    changed = Case(((Boolean("or", (
        IsNull(C("prev1_pitch_type")), IsNull(C(type_field)),
        Binary(C("prev1_pitch_type"), "!=", C(type_field)),
    )), Literal(1)),), Literal(0))
    unknown = Case(((IsNull(C(type_field)), Literal(1)),), Literal(0))
    running = WindowFrame(None, 0)
    n2 = Window(n1, (
        WindowField("__ta_run_id", "sum", (changed,), PA, ORDER, running),
        WindowField("__ta_unknown_so_far", "sum", (unknown,), PA, ORDER, running),
    ))
    n3 = Window(n2, (WindowField("__ta_streak", "row_number", (), PA + (C("__ta_run_id"),), ORDER),))

    def element(k: int):
        return Case((
            (Binary(C("pitch_index_in_pa"), "<=", Literal(k)), Literal("^")),
            (IsNull(C(f"prev{k}_pitch_type")), Literal("?")),
        ), C(f"prev{k}_pitch_type"))

    key = element(memory)
    for k in range(memory - 1, 0, -1):
        key = Binary(Binary(key, "||", Literal(">")), "||", element(k))

    def diff(cur: str, prev: str):
        return Case(((Boolean("and", (IsNull(C(cur), True), IsNull(C(prev), True))), Binary(C(cur), "-", C(prev))),), None)

    fields = [NamedExpr(name, C(name)) for name in carry_fields]
    fields.append(NamedExpr("pitch_index_in_pa", C("pitch_index_in_pa")))
    fields += [NamedExpr(f"prev{k}_pitch_type", C(f"prev{k}_pitch_type")) for k in range(1, memory + 1)]
    fields += [
        NamedExpr("history_key", key),
        NamedExpr("prev1_description", C("prev1_description")),
        NamedExpr("speed_diff_prev1", diff("release_speed", "prev1_release_speed")),
        NamedExpr("dx_prev1", diff("plate_x", "prev1_plate_x")),
        NamedExpr("dz_prev1", diff("plate_z", "prev1_plate_z")),
        NamedExpr("streak_pos", Case(((IsNull(C(type_field), True), C("__ta_streak")),), None)),
        NamedExpr("history_complete", Case(((Binary(C("__ta_unknown_so_far"), "=", Literal(0)), Literal(1)),), Literal(0))),
    ]
    return Project(n3, tuple(fields), PITCH_GRAIN)
