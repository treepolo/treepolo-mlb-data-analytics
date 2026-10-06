from __future__ import annotations

import math
import re
from collections import defaultdict
from typing import Any, Sequence

from ..analysis import (
    OUTCOME_CATEGORIES, Aggregate, Binary, Case, Column, Filter, Grain, IsNull, Limit, Literal, Metric, NamedExpr, OrderKey, Sort,
)
from ..analysis.exclusions import BUNT_POLICIES
from ..analysis.model import PITCH_GRAIN, Project, SCALAR_GRAIN
from ..analysis.pitch_table import (
    CELL_FIELD_CHOICES, IN_PLAY, SWING_METRIC_FIELDS, RATE_NAMES, STRATA_FIELD_CHOICES, SWING, build_pitch_table, outcome_cells_node, pitch_table_columns,
    rate_terms, streak_cells_node,
)
from ..analysis.run_values import (
    STATE_GRAIN, all_state_codes, decode_state, pitch_value_expr, run_context,
)
from .config_schema import ConfigError, ConfigField
from .guards import PURPOSES, check_modeling_scope, check_purpose
from .methods import ResearchContext, ResearchMethod, ResearchResult, register_method
from .re_source import DEFAULT_RE_SCOPE, get_re_table, re_method_inputs
from .runner import run_checked
from .scope import normalize_scope, scope_filter_expr
from .sections import clean, make_section
from .stats import cluster_mean_se, mean_interval, two_proportion_z, wilson_interval, z_value
from .placebo import placebo_same_point
from .streak import cluster_bootstrap_same_point, same_point_curve

CLUSTER_FIELDS = {
    "plate_appearance": ("game_pk", "at_bat_number"), "pitcher": ("pitcher",), "batter": ("batter",), "game": ("game_pk",),
}
VIEW_GROUP = {"RvR": "same_side", "LvL": "same_side", "RvL": "opposite_side", "LvR": "opposite_side"}
SYMMETRY_PAIRS = (("RvR", "LvL"), ("RvL", "LvR"))
_PREV = re.compile(r"^prev(\d)_pitch_type$")
MAX_PLACEBO_ROWS = 3_000_000

# ---- shared setting definitions --------------------------------------------------------------------------------------
PURPOSE = ConfigField(
    "purpose", "choice", default="tuning", choices=PURPOSES, label_zh="用途", label_en="Purpose",
    help_zh="tuning：只能用 2023–2024；final_test：只能用 2025／2026；extra_study：任意範圍（沒有乾淨的檢驗集）。",
    help_en="tuning: modeling seasons only; final_test: test seasons only; extra_study: any scope (no clean test set).",
)
RE_SCOPE = ConfigField(
    "re_scope", "json", default=DEFAULT_RE_SCOPE, label_zh="得分期望值表的資料範圍", label_en="Data scope of the run-expectancy table",
    help_zh="得分期望值表只由這個範圍計算並固定（預設建模期 2023–2024 例行賽）。", help_en="The run-expectancy table is learned from this scope only (default: modeling seasons).",
)
BUNT_POLICY = ConfigField(
    "bunt_policy", "choice", default="exclude_plate_appearance", choices=BUNT_POLICIES, label_zh="短打處理", label_en="Bunt policy",
    help_zh="預設排除含短打的整個打席。", help_en="Default: drop every plate appearance that contains a bunt.",
)
CONFIDENCE = ConfigField("confidence", "float", default=0.95, minimum=0.5, maximum=0.999, label_zh="信賴水準", label_en="Confidence level")
FILTERS = ConfigField(
    "filters", "json", default=[], label_zh="條件過濾", label_en="Filters",
    help_zh='列表，每項 {"field","op","value"}，op 為 eq/ne/in/not_in/gt/gte/lt/lte；在計算序列欄位之後才篩選。',
    help_en='List of {"field","op","value"}; applied after sequence features are computed.',
)


def _sort_key(row: dict[str, Any], fields: Sequence[str]):
    return tuple((row.get(f) is None, str(row.get(f))) for f in fields)


def _check_column_list(name: str, values: Sequence[str], allowed: Sequence[str]) -> None:
    unknown = sorted(set(values) - set(allowed))
    if unknown:
        raise ConfigError(f"{name} has unknown fields {unknown}; allowed: {list(allowed)} / {name} 含未知欄位 {unknown}")


def _check_edges(name: str, edges: Any) -> list[float]:
    if not isinstance(edges, list) or any(isinstance(e, bool) or not isinstance(e, (int, float)) for e in edges):
        raise ConfigError(f"{name} must be a list of numbers / {name} 必須是數字列表")
    if edges != sorted(edges) or len(set(edges)) != len(edges):
        raise ConfigError(f"{name} must be strictly increasing / {name} 必須嚴格遞增")
    return [float(e) for e in edges]


def _check_filters(filters: Any) -> None:
    if not isinstance(filters, list):
        raise ConfigError("filters must be a list / filters 必須是列表")
    from ..analysis.pitch_table import apply_filters

    try:
        apply_filters(None, filters)  # builds the tree only to validate the shape of every condition
    except ValueError as exc:
        raise ConfigError(f"{exc} / 條件過濾格式錯誤") from exc


def _check_prev_fields(fields: Sequence[str], memory: int) -> None:
    for name in fields:
        match = _PREV.match(name)
        if match and int(match.group(1)) > memory:
            raise ConfigError(f"{name} needs memory >= {match.group(1)} / {name} 需要 memory >= {match.group(1)}")


# ========================================================================================================================
class RunExpectancyMethod(ResearchMethod):
    """Run-expectancy table per base-out-count state, per-outcome run values, and a comparison with Savant's delta_run_exp."""

    kind = "run_expectancy"
    version = 1
    label_zh = "得分期望值表與結果價值"
    label_en = "Run expectancy and outcome values"
    requires_scope = True
    fields = (
        PURPOSE, CONFIDENCE, BUNT_POLICY,
        ConfigField("min_state_n", "int", default=30, minimum=1, label_zh="狀態最小樣本數", label_en="Minimum pitches per state",
                    help_zh="低於此數的狀態標示 low_n，不填補。", help_en="States below this are flagged low_n and never filled in."),
        ConfigField("outcome_values", "bool", default=True, label_zh="計算各結果的價值", label_en="Compute outcome values"),
        ConfigField("savant_comparison", "bool", default=True, label_zh="與 Savant delta_run_exp 對照", label_en="Compare with Savant delta_run_exp"),
    )

    def validate(self, config: dict[str, Any], ctx: ResearchContext) -> None:
        check_purpose(ctx.scope, config["purpose"])

    def run(self, ctx: ResearchContext, config: dict[str, Any]) -> ResearchResult:
        z = z_value(config["confidence"])
        re = get_re_table(ctx, ctx.scope, require_complete=False)
        rows = []
        for code in all_state_codes():
            info = re.states.get(code)
            state = decode_state(code)
            if info is None:
                rows.append({"state_code": code, **state, "n": 0, "half_innings": 0, "re": None, "se": None, "lo": None, "hi": None, "low_n": 1})
                continue
            lo, hi = mean_interval(info["re"], info["se"], config["confidence"])
            rows.append({"state_code": code, **state, "n": info["n"], "half_innings": info["g"], "re": info["re"], "se": info["se"],
                         "lo": lo, "hi": hi, "low_n": int(info["n"] < config["min_state_n"])})
        backend = "duckdb"
        sections = [make_section("Run expectancy by state 得分期望值表",
                             ("state_code", "balls", "strikes", "outs", "bases", "n", "half_innings", "re", "se", "lo", "hi", "low_n"),
                             rows, ("state_code",), backend)]
        if config["outcome_values"]:
            sections.append(self._outcome_values(ctx, config, re))
        extras: dict[str, Any] = {"scope": ctx.scope, "states_missing": len(re.missing_states()), "confidence": config["confidence"], "z": z}
        if config["savant_comparison"]:
            section, stats = self._savant(ctx, re)
            sections.append(section)
            extras.update(stats)
        return ResearchResult(sections=tuple(sections), extras=extras)

    def _outcome_values(self, ctx, config, re) -> dict[str, Any]:
        table = build_pitch_table(ctx.source_node(), bunt_policy=config["bunt_policy"], memory=1, re_by_state=re.re_by_state)
        valued = Project(Filter(table, IsNull(Column("pitch_value"), True)), (
            NamedExpr("pitch_uid", Column("pitch_uid")), NamedExpr("state_code", Column("state_code")),
            NamedExpr("outcome", Column("outcome")), NamedExpr("pitch_value", Column("pitch_value"))), PITCH_GRAIN)
        node = Aggregate(valued, (NamedExpr("state_code", Column("state_code")), NamedExpr("outcome", Column("outcome"))), (
            Metric("n", "count"), Metric("mean_value", "avg", Column("pitch_value")), Metric("sd_value", "stddev_samp", Column("pitch_value")),
        ), Grain(("state_code", "outcome"), "state_outcome"))
        result = run_checked(ctx, node)
        rows = []
        for r in result.rows:
            rows.append({"state_code": int(r["state_code"]), **{k: v for k, v in decode_state(int(r["state_code"])).items()},
                         "outcome": r["outcome"], "n": int(r["n"]), "mean_value": r["mean_value"], "sd_value": r["sd_value"],
                         "low_n": int(int(r["n"]) < config["min_state_n"])})
        rows.sort(key=lambda r: (r["state_code"], r["outcome"]))
        return make_section("Outcome values by state 各狀態下各結果的價值",
                        ("state_code", "balls", "strikes", "outs", "bases", "outcome", "n", "mean_value", "sd_value", "low_n"),
                        rows, ("state_code", "outcome"), result.backend)

    def _savant(self, ctx, re) -> tuple[dict[str, Any], dict[str, Any]]:
        context = run_context(ctx.source_node(), ("description", "delta_run_exp"))
        kept = Filter(context, Binary(Column("is_walkoff_half"), "=", Literal(0)))
        paired = Project(Filter(kept, IsNull(Column("delta_run_exp"), True)), (
            NamedExpr("pitch_uid", Column("pitch_uid")), NamedExpr("description", Column("description")),
            NamedExpr("a", pitch_value_expr(re.re_by_state)), NamedExpr("b", Column("delta_run_exp"))), PITCH_GRAIN)
        by_description = Aggregate(paired, (NamedExpr("description", Column("description")),), (
            Metric("n", "count"), Metric("mean_pitch_value", "avg", Column("a")), Metric("mean_delta_run_exp", "avg", Column("b")),
        ), Grain(("description",), "description"))
        overall = Aggregate(paired, (), (
            Metric("n", "count"), Metric("sa", "sum", Column("a")), Metric("sb", "sum", Column("b")),
            Metric("saa", "sum", Binary(Column("a"), "*", Column("a"))), Metric("sbb", "sum", Binary(Column("b"), "*", Column("b"))),
            Metric("sab", "sum", Binary(Column("a"), "*", Column("b"))),
        ), SCALAR_GRAIN)
        result = run_checked(ctx, by_description)
        rows = [{"description": r["description"], "n": int(r["n"]), "mean_pitch_value": r["mean_pitch_value"],
                 "mean_delta_run_exp": r["mean_delta_run_exp"],
                 "mean_difference": None if r["mean_pitch_value"] is None else r["mean_pitch_value"] - r["mean_delta_run_exp"]}
                for r in result.rows]
        rows.sort(key=lambda r: -r["n"])
        total = run_checked(ctx, overall).rows[0]
        n = int(total["n"]); stats: dict[str, Any] = {"savant_pairs": n}
        if n > 1:
            cov = total["sab"] / n - (total["sa"] / n) * (total["sb"] / n)
            va = total["saa"] / n - (total["sa"] / n) ** 2; vb = total["sbb"] / n - (total["sb"] / n) ** 2
            stats["savant_correlation"] = cov / math.sqrt(va * vb) if va > 0 and vb > 0 else None
            stats["savant_mean_pitch_value"] = total["sa"] / n; stats["savant_mean_delta_run_exp"] = total["sb"] / n
        return make_section("Comparison with Savant delta_run_exp 與 Savant 對照",
                        ("description", "n", "mean_pitch_value", "mean_delta_run_exp", "mean_difference"), rows, ("description",), result.backend), {
            k: clean(v) for k, v in stats.items()}


# ========================================================================================================================
class OutcomeTableMethod(ResearchMethod):
    """Counts, probabilities (Wilson intervals), home-run rate and mean run value per situation x choice cell."""

    kind = "outcome_table"
    version = 1
    label_zh = "結果機率表（直接數次數）"
    label_en = "Outcome table (direct counting)"
    requires_scope = True
    fields = (
        PURPOSE, RE_SCOPE, BUNT_POLICY, CONFIDENCE, FILTERS,
        ConfigField("use_values", "bool", default=True, label_zh="計算結果價值", label_en="Compute run values",
                    help_zh="關閉則不需要得分期望值表，也沒有 mean_value 欄。", help_en="Off: no run-expectancy table and no mean_value columns."),
        ConfigField("memory", "int", default=2, minimum=1, maximum=5, label_zh="序列記憶長度", label_en="Sequence memory"),
        ConfigField("situation_fields", "str_list", default=["balls", "strikes"], label_zh="情境欄位", label_en="Situation fields"),
        ConfigField("choice_fields", "str_list", default=["pitch_type"], label_zh="配球選擇欄位", label_en="Choice fields"),
        ConfigField("hand_views", "str_list", default=["four_groups"], unique_sorted=True, label_zh="左右手檢視", label_en="Handedness views",
                    help_zh="four_groups（四組分開）與／或 same_opposite（同側／異側合併）。", help_en="four_groups and/or same_opposite."),
        ConfigField("location_frame", "choice", default="raw", choices=("raw", "mirrored"), label_zh="位置座標系", label_en="Location frame",
                    help_zh="raw：原樣；mirrored：左投手資料鏡像成右投手視角（same_opposite 必須用）。", help_en="mirrored: left-handed pitchers flipped (required for same_opposite)."),
        ConfigField("loc_x_edges", "json", default=[-0.83, -0.28, 0.28, 0.83], label_zh="水平位置分箱邊界（英尺）", label_en="Horizontal location bin edges (ft)"),
        ConfigField("loc_z_edges", "json", default=[1.5, 2.0, 2.5, 3.0, 3.5], label_zh="垂直位置分箱邊界（英尺）", label_en="Vertical location bin edges (ft)"),
        ConfigField("cluster_by", "choice", default="pitcher", choices=tuple(CLUSTER_FIELDS), label_zh="標準誤的分群單位", label_en="Cluster unit for standard errors"),
        ConfigField("min_samples", "int", default=100, minimum=1, label_zh="最小樣本數", label_en="Minimum pitches per cell",
                    help_zh="低於此數標示 low_n；不填補。", help_en="Cells below this are flagged low_n; never filled in."),
        ConfigField("max_cells", "int", default=20000, minimum=1, maximum=500000, label_zh="格子數上限", label_en="Maximum cells",
                    help_zh="超過就報錯（不截斷）；請減少欄位或加條件。", help_en="More cells is an error (never truncated); use fewer fields or add filters."),
        ConfigField("swing_metrics", "str_list", default=[], label_zh="結果分項（擊球資料與球棒追蹤）", label_en="Swing and batted-ball breakdowns",
                    help_zh="每格輸出有值球數與平均；缺值不填補、不計入。", help_en="Per cell: count and mean of non-NULL values; never imputed."),
        ConfigField("symmetry_top", "int", default=20, minimum=0, label_zh="對稱性檢查列出最差格數", label_en="Worst cells listed by the symmetry check"),
    )

    def validate(self, config: dict[str, Any], ctx: ResearchContext) -> None:
        check_purpose(ctx.scope, config["purpose"])
        fields = list(config["situation_fields"]) + list(config["choice_fields"])
        if len(set(fields)) != len(fields):
            raise ConfigError("situation_fields and choice_fields must not repeat a field / 欄位不得重複")
        _check_column_list("situation_fields/choice_fields", fields, CELL_FIELD_CHOICES)
        _check_prev_fields(fields, config["memory"])
        views = config["hand_views"]
        if not views or set(views) - {"four_groups", "same_opposite"}:
            raise ConfigError("hand_views must be a non-empty subset of four_groups, same_opposite / hand_views 必須是 four_groups、same_opposite 的非空子集")
        if "same_opposite" in views:
            if config["location_frame"] != "mirrored":
                raise ConfigError("same_opposite needs location_frame=mirrored / same_opposite 需要 location_frame=mirrored")
            if config["cluster_by"] not in ("plate_appearance", "pitcher"):
                raise ConfigError("same_opposite supports cluster_by plate_appearance or pitcher only / same_opposite 只支援以打席或投手分群")
        _check_column_list("swing_metrics", config["swing_metrics"], SWING_METRIC_FIELDS)
        config["loc_x_edges"] = _check_edges("loc_x_edges", config["loc_x_edges"])
        config["loc_z_edges"] = _check_edges("loc_z_edges", config["loc_z_edges"])
        for name, edges in (("loc_x_bin", config["loc_x_edges"]), ("loc_z_bin", config["loc_z_edges"])):
            if name in fields and not edges:
                raise ConfigError(f"{name} needs non-empty edges / {name} 需要非空的邊界")
        _check_filters(config["filters"])
        if config["use_values"]:
            config["re_scope"] = normalize_scope(config["re_scope"], required=True)
            check_modeling_scope(config["re_scope"], config["purpose"], "re_scope")

    def method_inputs(self, ctx: ResearchContext, config: dict[str, Any]) -> dict[str, Any]:
        return re_method_inputs(ctx, config) if config["use_values"] else {}

    def run(self, ctx: ResearchContext, config: dict[str, Any]) -> ResearchResult:
        fields = tuple(config["situation_fields"]) + tuple(config["choice_fields"])
        re = get_re_table(ctx, config["re_scope"]) if config["use_values"] else None
        table = build_pitch_table(
            ctx.source_node(), bunt_policy=config["bunt_policy"], memory=config["memory"], re_by_state=re.re_by_state if re else None,
            mirror=config["location_frame"] == "mirrored", loc_x_edges=config["loc_x_edges"] if "loc_x_bin" in fields else (),
            loc_z_edges=config["loc_z_edges"] if "loc_z_bin" in fields else (), filters=config["filters"],
            extra_columns=config["swing_metrics"])
        keep = Filter(table, IsNull(Column("hand_group"), True))
        cell_fields = ("hand_group",) + fields
        node = Limit(outcome_cells_node(keep, cell_fields=cell_fields, cluster_fields=CLUSTER_FIELDS[config["cluster_by"]],
                                        categories=OUTCOME_CATEGORIES, swing_metrics=config["swing_metrics"], with_values=re is not None), config["max_cells"] + 1)
        result = run_checked(ctx, node)
        if len(result.rows) > config["max_cells"]:
            raise ConfigError(f"More than max_cells={config['max_cells']} cells; use fewer fields or add filters / 格子數超過 max_cells")
        rows = [dict(r) for r in result.rows]
        present = [c for c in OUTCOME_CATEGORIES if any(int(r[f"c_{c}"] or 0) for r in rows)]
        sections: list[dict[str, Any]] = []
        for view in config["hand_views"]:
            merged = rows if view == "four_groups" else _merge_views(rows, fields)
            cells, probabilities = _cell_rows(merged, fields, present, config, with_values=re is not None)
            sections.append(make_section(f"Cells ({view}) 格子", _cell_columns(fields, present, re is not None, config["swing_metrics"]), cells, ("hand_group",) + fields, result.backend))
            sections.append(make_section(f"Outcome probabilities ({view}) 結果機率",
                                     ("hand_group",) + fields + ("outcome", "count", "n", "p", "lo", "hi", "low_n"), probabilities,
                                     ("hand_group",) + fields + ("outcome",), result.backend))
        extras: dict[str, Any] = {
            "scope": ctx.scope, "re_scope": config["re_scope"] if re else None, "categories_present": present, "cells": len(rows),
            "pitches": sum(int(r["n"]) for r in rows), "pitches_with_value": sum(int(r["nv"] or 0) for r in rows),
            "cluster_by": config["cluster_by"], "location_frame": config["location_frame"],
        }
        if config["swing_metrics"]:
            total = max(extras["pitches"], 1)
            extras["swing_metric_coverage"] = {f: sum(int(r[f"w_{f}_n"] or 0) for r in rows) / total for f in config["swing_metrics"]}
        if "same_opposite" in config["hand_views"]:
            summary, worst = _symmetry(rows, fields, present, config)
            sections.append(make_section("Symmetry check 對稱性檢查", ("pair", "outcome", "cells_compared", "mean_z", "mean_abs_z", "share_abs_z_above_critical"),
                                     summary, ("pair", "outcome"), result.backend))
            sections.append(make_section("Symmetry check, worst cells 對稱性檢查最差格", ("pair", "outcome") + fields + ("n_a", "p_a", "n_b", "p_b", "z"),
                                     worst, ("pair", "outcome") + fields, result.backend))
        return ResearchResult(sections=tuple(sections), extras=extras)


def _merge_views(rows: list[dict[str, Any]], fields: Sequence[str]) -> list[dict[str, Any]]:
    """Add the four hand groups into same_side / opposite_side. Valid because every cluster belongs to one source group."""

    additive = [k for k in rows[0] if k in ("n", "nv", "sv", "ssv", "snv", "nnv", "g") or k.startswith(("c_", "w_"))] if rows else []
    merged: dict[tuple, dict[str, Any]] = {}
    for row in rows:
        key = (VIEW_GROUP.get(row["hand_group"], row["hand_group"]),) + tuple(row[f] for f in fields)
        target = merged.get(key)
        if target is None:
            target = {"hand_group": key[0], **{f: row[f] for f in fields}, **{k: 0 for k in additive}}
            merged[key] = target
        for k in additive:
            target[k] += row[k] or 0
    return list(merged.values())


def _cell_columns(fields: Sequence[str], present: Sequence[str], with_values: bool, swing_metrics: Sequence[str] = ()) -> tuple[str, ...]:
    columns = ["hand_group", *fields, "n", "n_swing", "swing_rate", "swing_lo", "swing_hi", "whiff_per_swing", "whiff_lo", "whiff_hi",
               "hr_n", "hr_rate", "hr_lo", "hr_hi"]
    if with_values:
        columns += ["n_value", "clusters", "mean_value", "se_value", "value_lo", "value_hi"]
    columns += [f"{f}_{k}" for f in swing_metrics for k in ("n", "mean")]
    columns += [f"c_{c}" for c in present] + ["low_n"]
    return tuple(columns)


def _cell_rows(merged, fields, present, config, *, with_values):
    confidence = config["confidence"]; cells = []; probabilities = []
    for row in sorted(merged, key=lambda r: _sort_key(r, ("hand_group",) + tuple(fields))):
        n = int(row["n"]); counts = {c: int(row[f"c_{c}"] or 0) for c in present}
        swings = sum(counts.get(c, 0) for c in SWING); whiffs = counts.get("whiff", 0); hrs = counts.get("in_play_home_run", 0)
        low_n = int(n < config["min_samples"])
        out = {"hand_group": row["hand_group"], **{f: row[f] for f in fields}, "n": n, "n_swing": swings,
               "swing_rate": swings / n if n else None, "whiff_per_swing": whiffs / swings if swings else None,
               "hr_n": hrs, "hr_rate": hrs / n if n else None, "low_n": low_n}
        out["swing_lo"], out["swing_hi"] = wilson_interval(swings, n, confidence)
        out["whiff_lo"], out["whiff_hi"] = wilson_interval(whiffs, swings, confidence)
        out["hr_lo"], out["hr_hi"] = wilson_interval(hrs, n, confidence)
        if with_values:
            nv = int(row["nv"] or 0)
            mean = float(row["sv"]) / nv if nv else None
            se = cluster_mean_se(nv, float(row["sv"] or 0), float(row["ssv"] or 0), float(row["snv"] or 0), float(row["nnv"] or 0), float(row["g"] or 0))
            out.update(n_value=nv, clusters=int(row["g"] or 0), mean_value=mean, se_value=se)
            out["value_lo"], out["value_hi"] = mean_interval(mean, se, confidence)
        out.update({f"c_{c}": counts[c] for c in present})
        for f in config["swing_metrics"]:
            wn = int(row[f"w_{f}_n"] or 0)
            out[f"{f}_n"] = wn
            out[f"{f}_mean"] = float(row[f"w_{f}_s"]) / wn if wn else None
        cells.append(out)
        for c in present:
            lo, hi = wilson_interval(counts[c], n, confidence)
            probabilities.append({"hand_group": row["hand_group"], **{f: row[f] for f in fields}, "outcome": c, "count": counts[c], "n": n,
                                  "p": counts[c] / n if n else None, "lo": lo, "hi": hi, "low_n": low_n})
    return cells, probabilities


def _symmetry(rows, fields, present, config):
    """Compare mirrored source groups cell by cell (RvR vs LvL, RvL vs LvR); only cells with enough pitches on both sides."""

    z_crit = z_value(config["confidence"])
    index = {(r["hand_group"],) + tuple(r[f] for f in fields): r for r in rows}
    summary, found = [], []
    for a, b in SYMMETRY_PAIRS:
        pair = f"{a} vs {b}"
        for outcome in present:
            zs = []
            for key, ra in index.items():
                if key[0] != a:
                    continue
                rb = index.get((b,) + key[1:])
                if rb is None or int(ra["n"]) < config["min_samples"] or int(rb["n"]) < config["min_samples"]:
                    continue
                ka, kb = int(ra[f"c_{outcome}"] or 0), int(rb[f"c_{outcome}"] or 0)
                z = two_proportion_z(ka, int(ra["n"]), kb, int(rb["n"]))
                if z is None:
                    continue
                zs.append(z)
                found.append({"pair": pair, "outcome": outcome, **{f: ra[f] for f in fields}, "n_a": int(ra["n"]), "p_a": ka / int(ra["n"]),
                              "n_b": int(rb["n"]), "p_b": kb / int(rb["n"]), "z": z})
            summary.append({"pair": pair, "outcome": outcome, "cells_compared": len(zs),
                            "mean_z": sum(zs) / len(zs) if zs else None, "mean_abs_z": sum(abs(z) for z in zs) / len(zs) if zs else None,
                            "share_abs_z_above_critical": sum(abs(z) > z_crit for z in zs) / len(zs) if zs else None})
    found.sort(key=lambda r: -abs(r["z"]))
    return summary, found[: config["symmetry_top"]]


# ========================================================================================================================
class StreakCurveMethod(ResearchMethod):
    """Streak (same-type run) curve: naive pooled curve versus the same-point comparison at fixed strata."""

    kind = "streak_curve"
    version = 2  # 2: placebo_mode (default pairs)
    label_zh = "連投曲線（走到同一點再比較）"
    label_en = "Streak curve (same-point comparison)"
    requires_scope = True
    fields = (
        PURPOSE, RE_SCOPE, BUNT_POLICY, CONFIDENCE, FILTERS,
        ConfigField("rate", "choice", default="whiff_per_swing", choices=RATE_NAMES, label_zh="被觀察的比率", label_en="Rate"),
        ConfigField("foul_tip_is_whiff", "bool", default=False, label_zh="擦棒算揮空", label_en="Count foul tips as whiffs"),
        ConfigField("pitch_types", "str_list", default=[], label_zh="球種（空白＝全部）", label_en="Pitch types (empty = all)"),
        ConfigField("min_type_pitches", "int", default=5000, minimum=1, label_zh="球種最小球數", label_en="Minimum eligible pitches per pitch type",
                    help_zh="合格球數低於此數的球種不輸出。", help_en="Pitch types with fewer eligible pitches are not reported."),
        ConfigField("kmax", "int", default=5, minimum=2, maximum=10, label_zh="連投顆數上限", label_en="Largest streak position",
                    help_zh="kmax 代表「kmax 顆以上」。", help_en="kmax stands for 'kmax or more'."),
        ConfigField("strata_fields", "str_list", default=["pitch_index_in_pa", "balls", "strikes"], label_zh="同一點的定義（分層欄位）", label_en="Strata that define 'the same point'"),
        ConfigField("hand_view", "choice", default="four_groups", choices=("four_groups", "same_opposite", "pooled"), label_zh="左右手檢視", label_en="Handedness view"),
        ConfigField("min_stratum_n", "int", default=30, minimum=2, label_zh="每層最小樣本數", label_en="Minimum pitches per stratum and streak position"),
        ConfigField("se_method", "choice", default="analytic", choices=("analytic", "cluster_bootstrap"), label_zh="標準誤算法", label_en="Standard error method"),
        ConfigField("cluster_by", "choice", default="pitcher", choices=("pitcher", "batter", "game"), label_zh="Bootstrap 分群單位", label_en="Bootstrap cluster unit"),
        ConfigField("bootstrap_reps", "int", default=200, minimum=20, maximum=5000, label_zh="Bootstrap 次數", label_en="Bootstrap replicates"),
        ConfigField("seed", "int", default=20240601, label_zh="隨機種子", label_en="Random seed"),
        ConfigField("placebo_shuffles", "int", default=0, minimum=0, maximum=1000, label_zh="安慰劑打亂次數（0＝不做）", label_en="Placebo shuffles (0 = off)"),
        ConfigField("placebo_mode", "choice", default="pairs", choices=("pairs", "labels"), label_zh="安慰劑模式", label_en="Placebo mode",
                    help_zh="pairs：球種、是否合格、數值一起打亂（預設，診斷用）；labels：只打亂球種標籤（球種危險率不同時會假陽性）。", help_en="pairs keeps type-specific outcome levels; labels breaks them. Diagnostic only."),
    )

    def validate(self, config: dict[str, Any], ctx: ResearchContext) -> None:
        check_purpose(ctx.scope, config["purpose"])
        _check_column_list("strata_fields", config["strata_fields"], STRATA_FIELD_CHOICES)
        if len(set(config["strata_fields"])) != len(config["strata_fields"]):
            raise ConfigError("strata_fields must not repeat a field / 欄位不得重複")
        _check_filters(config["filters"])
        if config["rate"] == "mean_pitch_value":
            config["re_scope"] = normalize_scope(config["re_scope"], required=True)
            check_modeling_scope(config["re_scope"], config["purpose"], "re_scope")

    def method_inputs(self, ctx: ResearchContext, config: dict[str, Any]) -> dict[str, Any]:
        return re_method_inputs(ctx, config) if config["rate"] == "mean_pitch_value" else {}

    def run(self, ctx: ResearchContext, config: dict[str, Any]) -> ResearchResult:
        strata = tuple(config["strata_fields"])
        group_fields = {"four_groups": ("hand_group",), "same_opposite": ("frame_group",), "pooled": ()}[config["hand_view"]]
        re = get_re_table(ctx, config["re_scope"]) if config["rate"] == "mean_pitch_value" else None
        table = build_pitch_table(
            ctx.source_node(), bunt_policy=config["bunt_policy"], memory=1, re_by_state=re.re_by_state if re else None,
            mirror=config["hand_view"] == "same_opposite", prior_same_type_count="prior_same_type_count" in strata, filters=config["filters"])
        if group_fields:
            table = Filter(table, IsNull(Column(group_fields[0]), True))

        def cells(cluster_field: str | None):
            node = streak_cells_node(table, group_fields=group_fields, strata_fields=strata, rate=config["rate"], kmax=config["kmax"],
                                     pitch_types=config["pitch_types"], foul_tip_is_whiff=config["foul_tip_is_whiff"], cluster_field=cluster_field)
            return run_checked(ctx, node)

        result = cells(None)
        rows = [dict(r) for r in result.rows]
        totals: dict[tuple, int] = defaultdict(int)
        for r in rows:
            totals[tuple(r[f] for f in group_fields) + (r["pitch_type"],)] += int(r["n"] or 0)
        rows = [r for r in rows if totals[tuple(r[f] for f in group_fields) + (r["pitch_type"],)] >= config["min_type_pitches"]]
        naive, same = same_point_curve(rows, group_fields=group_fields, strata_fields=strata, kmax=config["kmax"],
                                       min_n=config["min_stratum_n"], confidence=config["confidence"])
        same_columns = ["pitch_type", "k", "estimate", "se", "lo", "hi", "n_strata", "n_k_used", "n_k_total", "coverage"]
        if config["se_method"] == "cluster_bootstrap":
            cluster_field = config["cluster_by"] if config["cluster_by"] != "game" else "game_pk"
            boot_rows = [dict(r) for r in cells(cluster_field).rows
                         if totals[tuple(r[f] for f in group_fields) + (r["pitch_type"],)] >= config["min_type_pitches"]]
            boot = cluster_bootstrap_same_point(boot_rows, group_fields=group_fields, strata_fields=strata, kmax=config["kmax"],
                                                min_n=config["min_stratum_n"], reps=config["bootstrap_reps"], seed=config["seed"],
                                                confidence=config["confidence"])
            lookup = {tuple(b[f] for f in group_fields) + (b["pitch_type"], b["k"]): b for b in boot}
            for row in same:
                b = lookup.get(tuple(row[f] for f in group_fields) + (row["pitch_type"], row["k"]))
                row.update(boot_se=b["boot_se"] if b else None, boot_lo=b["boot_lo"] if b else None, boot_hi=b["boot_hi"] if b else None)
            same_columns += ["boot_se", "boot_lo", "boot_hi"]
        sections = [
            make_section("Naive curve by streak position 依連投顆數的原始曲線（含存活偏誤）", (*group_fields, "pitch_type", "k", "n", "mean", "se", "lo", "hi"),
                     naive, (*group_fields, "pitch_type", "k"), result.backend),
            make_section("Same-point comparison versus k=1 同一點比較（相對第 1 顆）", (*group_fields, *same_columns), same,
                     (*group_fields, "pitch_type", "k"), result.backend),
        ]
        extras: dict[str, Any] = {"scope": ctx.scope, "rate": config["rate"], "strata_fields": list(strata), "kmax": config["kmax"],
                                  "re_scope": config["re_scope"] if re else None}
        if config["placebo_shuffles"]:
            placebo = self._placebo(ctx, config, table, group_fields, strata, same)
            sections.append(make_section("Placebo: pitch types shuffled inside each plate appearance 安慰劑（打席內打亂球種）",
                                     (*group_fields, "pitch_type", "k", "observed", "placebo_mean", "placebo_sd", "placebo_lo", "placebo_hi", "placebo_n", "p_two_sided"),
                                     placebo, (*group_fields, "pitch_type", "k"), result.backend))
        return ResearchResult(sections=tuple(sections), extras=extras)

    def _placebo(self, ctx, config, table, group_fields, strata, same):
        eligible, x = rate_terms(config["rate"], foul_tip_is_whiff=config["foul_tip_is_whiff"])
        order = (OrderKey(Column("game_pk")), OrderKey(Column("at_bat_number")), OrderKey(Column("pitch_index_in_pa")))
        keys = ("game_pk", "at_bat_number", "pitch_index_in_pa", "pitch_type") + tuple(group_fields) + tuple(strata)
        rows_node = Sort(Project(Filter(table, IsNull(Column("pitch_type"), True)), (NamedExpr("pitch_uid", Column("pitch_uid")),)
                                 + tuple(NamedExpr(f, Column(f)) for f in dict.fromkeys(keys))
                                 + (NamedExpr("e", eligible), NamedExpr("x", Case(((Binary(eligible, "=", Literal(1)), x),), Literal(0)))),
                                 PITCH_GRAIN), order)
        result = run_checked(ctx, Limit(rows_node, MAX_PLACEBO_ROWS + 1))
        if len(result.rows) > MAX_PLACEBO_ROWS:
            raise ConfigError(f"Placebo needs more than {MAX_PLACEBO_ROWS} pitch rows; narrow the scope or filters / 安慰劑需要的逐球列數過多")
        wanted = {row["pitch_type"] for row in same}
        observed = {tuple(row[f] for f in group_fields) + (row["pitch_type"], row["k"]): row["estimate"] for row in same if row["estimate"] is not None}
        return placebo_same_point(list(result.rows), group_fields=group_fields, strata_fields=strata, pitch_types=sorted(wanted),
                                  kmax=config["kmax"], shuffles=config["placebo_shuffles"], seed=config["seed"],
                                  min_n=config["min_stratum_n"], observed=observed, mode=config["placebo_mode"])


for _method in (RunExpectancyMethod(), OutcomeTableMethod(), StreakCurveMethod()):
    register_method(_method)
