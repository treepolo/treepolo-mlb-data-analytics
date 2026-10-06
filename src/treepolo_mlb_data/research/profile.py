from __future__ import annotations

import sqlite3
import statistics
from typing import Any, Callable, Mapping, Sequence

from ..analysis import Aggregate, Column, Grain, Metric, NamedExpr, OrderKey, Sort, outcome_category_expr
from ..schema import quote_ident
from .scope import scope_where_sql

SECTION_NAMES = (
    "rows_by_season", "description_values", "in_play_events", "pitch_type_values", "coverage_by_outcome", "bunt_share",
    "strike_zone_definition", "handedness_groups", "mirror_field_means", "zone_plate_check", "outcome_category_counts",
)
DEFAULT_TRACKED_FIELDS = (
    "launch_speed", "launch_angle", "hc_x", "hc_y", "hit_distance_sc", "bat_speed", "swing_length", "attack_angle",
    "attack_direction", "swing_path_tilt", "intercept_ball_minus_batter_pos_x_inches",
    "intercept_ball_minus_batter_pos_y_inches", "miss_distance", "plate_x", "plate_z", "sz_top", "sz_bot",
    "release_speed", "release_spin_rate", "spin_axis", "vx0", "ax",
)
MIRROR_FIELDS = (
    "plate_x", "pfx_x", "release_pos_x", "vx0", "ax", "api_break_x_arm", "api_break_x_batter_in", "attack_direction",
    "intercept_ball_minus_batter_pos_x_inches", "hc_x",
)
SECTION_COLUMNS = {
    "rows_by_season": ("game_year", "game_type", "game_pk", "game_date"),
    "description_values": ("game_year", "description"),
    "in_play_events": ("game_year", "description", "events"),
    "pitch_type_values": ("game_year", "pitch_type"),
    "coverage_by_outcome": ("game_year", "description"),
    "bunt_share": ("game_year", "game_pk", "at_bat_number", "description", "events", "des"),
    "strike_zone_definition": ("game_year", "batter", "sz_top", "sz_bot"),
    "handedness_groups": ("game_year", "p_throws", "stand"),
    "mirror_field_means": ("p_throws", "stand", "spin_axis"),
    "zone_plate_check": ("zone", "stand", "plate_x", "plate_z"),
    "outcome_category_counts": ("description", "events", "des"),
}
_OUTCOME_GROUP_SQL = (
    "CASE WHEN description IN ('swinging_strike','swinging_strike_blocked') THEN 'whiff' "
    "WHEN description = 'foul_tip' THEN 'foul_tip' WHEN description = 'foul' THEN 'foul' "
    "WHEN description = 'hit_into_play' THEN 'in_play' WHEN description = 'called_strike' THEN 'called_strike' "
    "WHEN description IN ('ball','blocked_ball') THEN 'ball' ELSE 'other' END"
)
_BUNT_SQL = (
    "CASE WHEN description IN ('foul_bunt','missed_bunt') OR events IN ('sac_bunt','sac_bunt_double_play') "
    "OR des LIKE '%bunt%' THEN 1 ELSE 0 END"
)


def _section(title: str, columns: Sequence[str], rows: list[dict[str, Any]], keys: Sequence[str], backend: str = "sqlite") -> dict[str, Any]:
    return {
        "title": title, "columns": list(columns), "rows": rows, "row_count": len(rows),
        "grain": {"keys": list(keys), "label": title}, "backend": backend,
    }


def _rows(conn: sqlite3.Connection, sql: str, params: Sequence[Any]) -> list[tuple]:
    return conn.execute(sql, list(params)).fetchall()


def table_columns(conn: sqlite3.Connection) -> set[str]:
    return {row[1] for row in conn.execute("PRAGMA table_info(pitches)")}


def _null(value: Any) -> Any:
    return "(null)" if value is None else value


def rows_by_season(conn, where, params, **_):
    data = _rows(conn, f"SELECT game_year, game_type, COUNT(*), COUNT(DISTINCT game_pk), MIN(game_date), MAX(game_date) "
                       f"FROM pitches WHERE {where} GROUP BY game_year, game_type ORDER BY game_year, game_type", params)
    cols = ("game_year", "game_type", "rows", "games", "min_date", "max_date")
    return _section("rows_by_season 各球季筆數", cols, [dict(zip(cols, r)) for r in data], cols[:2])


def _value_counts(conn, where, params, column: str, extra_where: str = "") -> list[dict[str, Any]]:
    q = quote_ident(column)
    data = _rows(conn, f"SELECT game_year, {q}, COUNT(*) FROM pitches WHERE {where}{extra_where} "
                       f"GROUP BY game_year, {q} ORDER BY game_year, COUNT(*) DESC, {q}", params)
    return [{"game_year": r[0], column: _null(r[1]), "rows": r[2]} for r in data]


def description_values(conn, where, params, **_):
    return _section("description_values description 值域", ("game_year", "description", "rows"),
                    _value_counts(conn, where, params, "description"), ("game_year", "description"))


def in_play_events(conn, where, params, **_):
    return _section("in_play_events 界內球 events 值域", ("game_year", "events", "rows"),
                    _value_counts(conn, where, params, "events", " AND description = 'hit_into_play'"), ("game_year", "events"))


def pitch_type_values(conn, where, params, **_):
    return _section("pitch_type_values 球種值域", ("game_year", "pitch_type", "rows"),
                    _value_counts(conn, where, params, "pitch_type"), ("game_year", "pitch_type"))


def coverage_by_outcome(conn, where, params, tracked_fields=(), **_):
    exprs = "".join(f", COUNT({quote_ident(f)})" for f in tracked_fields)
    data = _rows(conn, f"SELECT game_year, {_OUTCOME_GROUP_SQL} AS grp, COUNT(*){exprs} FROM pitches WHERE {where} "
                       f"GROUP BY game_year, grp ORDER BY game_year, grp", params)
    out: list[dict[str, Any]] = []
    for row in data:
        total = row[2]
        for index, field in enumerate(tracked_fields):
            non_null = row[3 + index]
            out.append({"game_year": row[0], "outcome_group": row[1], "field": field, "rows": total, "non_null": non_null,
                        "non_null_pct": round(100.0 * non_null / total, 2) if total else None})
    cols = ("game_year", "outcome_group", "field", "rows", "non_null", "non_null_pct")
    return _section("coverage_by_outcome 各結果群組的欄位覆蓋率", cols, out, cols[:3])


def bunt_share(conn, where, params, **_):
    data = _rows(conn, f"WITH pa AS (SELECT game_year, game_pk, at_bat_number, COUNT(*) AS n, MAX({_BUNT_SQL}) AS b "
                       f"FROM pitches WHERE {where} GROUP BY game_year, game_pk, at_bat_number) "
                       "SELECT game_year, COUNT(*), SUM(b), SUM(n), SUM(CASE WHEN b = 1 THEN n ELSE 0 END) FROM pa "
                       "GROUP BY game_year ORDER BY game_year", params)
    cols = ("game_year", "plate_appearances", "bunt_plate_appearances", "bunt_pa_pct", "pitches", "pitches_in_bunt_pa", "pitches_in_bunt_pa_pct")
    out = [{"game_year": r[0], "plate_appearances": r[1], "bunt_plate_appearances": int(r[2] or 0),
            "bunt_pa_pct": round(100.0 * (r[2] or 0) / r[1], 3) if r[1] else None, "pitches": r[3],
            "pitches_in_bunt_pa": int(r[4] or 0), "pitches_in_bunt_pa_pct": round(100.0 * (r[4] or 0) / r[3], 3) if r[3] else None}
           for r in data]
    return _section("bunt_share 短打比例", cols, out, ("game_year",))


def strike_zone_definition(conn, where, params, **_):
    data = _rows(conn, "SELECT game_year, batter, COUNT(DISTINCT sz_top), AVG(sz_top), AVG(sz_bot) FROM pitches "
                       f"WHERE {where} AND sz_top IS NOT NULL AND sz_bot IS NOT NULL AND sz_bot > 0 GROUP BY game_year, batter", params)
    by_year: dict[Any, list[tuple]] = {}
    for row in data:
        by_year.setdefault(row[0], []).append(row)
    cols = ("game_year", "batters", "pct_batters_constant_sz_top", "median_distinct_sz_top", "ratio_min", "ratio_median", "ratio_max")
    out = []
    for year in sorted(by_year):
        rows = by_year[year]
        ratios = [r[3] / r[4] for r in rows]
        out.append({"game_year": year, "batters": len(rows),
                    "pct_batters_constant_sz_top": round(100.0 * sum(1 for r in rows if r[2] == 1) / len(rows), 2),
                    "median_distinct_sz_top": statistics.median(r[2] for r in rows),
                    "ratio_min": round(min(ratios), 4), "ratio_median": round(statistics.median(ratios), 4), "ratio_max": round(max(ratios), 4)})
    return _section("strike_zone_definition 好球帶上下緣定義", cols, out, ("game_year",))


def handedness_groups(conn, where, params, **_):
    data = _rows(conn, f"SELECT game_year, p_throws, stand, COUNT(*) FROM pitches WHERE {where} "
                       "GROUP BY game_year, p_throws, stand ORDER BY game_year, p_throws, stand", params)
    cols = ("game_year", "p_throws", "stand", "rows")
    return _section("handedness_groups 投打左右手分組", cols, [dict(zip(cols, (r[0], _null(r[1]), _null(r[2]), r[3]))) for r in data], cols[:3])


def mirror_field_means(conn, where, params, available=frozenset(), **_):
    out: list[dict[str, Any]] = []
    for field in (f for f in MIRROR_FIELDS if f in available):
        q = quote_ident(field)
        for r in _rows(conn, f"SELECT p_throws, stand, AVG({q}), COUNT({q}) FROM pitches WHERE {where} "
                             "GROUP BY p_throws, stand ORDER BY p_throws, stand", params):
            out.append({"p_throws": _null(r[0]), "stand": _null(r[1]), "field": field, "mean": r[2], "rows_non_null": r[3]})
    for r in _rows(conn, "SELECT p_throws, stand, AVG(CASE WHEN spin_axis < 180 THEN 1.0 ELSE 0.0 END), COUNT(spin_axis) "
                         f"FROM pitches WHERE {where} AND spin_axis IS NOT NULL GROUP BY p_throws, stand ORDER BY p_throws, stand", params):
        out.append({"p_throws": _null(r[0]), "stand": _null(r[1]), "field": "spin_axis_share_below_180", "mean": r[2], "rows_non_null": r[3]})
    cols = ("p_throws", "stand", "field", "mean", "rows_non_null")
    return _section("mirror_field_means 鏡像規則用的平均值", cols, out, cols[:3])


def zone_plate_check(conn, where, params, **_):
    data = _rows(conn, f"SELECT zone, stand, COUNT(*), AVG(plate_x), AVG(plate_z) FROM pitches WHERE {where} AND zone IS NOT NULL "
                       "GROUP BY zone, stand ORDER BY zone, stand", params)
    cols = ("zone", "stand", "rows", "mean_plate_x", "mean_plate_z")
    return _section("zone_plate_check zone 與進壘位置", cols, [dict(zip(cols, (r[0], _null(r[1]), r[2], r[3], r[4]))) for r in data], cols[:2])


def outcome_category_counts(ctx) -> dict[str, Any]:
    """Run through the P2 analysis tree so the real outcome mapping (and engine backend) is exercised."""

    node = Sort(
        Aggregate(
            ctx.source_node(),
            (NamedExpr("outcome_category", outcome_category_expr()), NamedExpr("description", Column("description")), NamedExpr("events", Column("events"))),
            (Metric("rows", "count"),),
            Grain(("outcome_category", "description", "events"), "outcome_category_counts"),
        ),
        (OrderKey(Column("rows"), descending=True), OrderKey(Column("outcome_category")), OrderKey(Column("description")), OrderKey(Column("events"))),
    )
    result = ctx.engine().execute(node)
    cols = ("outcome_category", "description", "events", "rows")
    rows = [{c: row[c] for c in cols} for row in result.rows]
    return _section("outcome_category_counts 結果類別對照", cols, rows, cols[:3], result.backend)


SQL_SECTIONS: Mapping[str, Callable[..., dict[str, Any]]] = {
    "rows_by_season": rows_by_season, "description_values": description_values, "in_play_events": in_play_events,
    "pitch_type_values": pitch_type_values, "coverage_by_outcome": coverage_by_outcome, "bunt_share": bunt_share,
    "strike_zone_definition": strike_zone_definition, "handedness_groups": handedness_groups,
    "mirror_field_means": mirror_field_means, "zone_plate_check": zone_plate_check,
}


def pitch_rows_in_scope(conn, scope) -> int:
    where, params = scope_where_sql(scope)
    return int(conn.execute(f"SELECT COUNT(*) FROM pitches WHERE {where}", params).fetchone()[0])
