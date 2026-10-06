from __future__ import annotations

from typing import Any

from ..analysis import Binary, Column, Filter, IsNull, Limit, Literal, NamedExpr, OrderKey, PITCH_GRAIN, Project, Sort
from ..analysis.exclusions import BUNT_POLICIES
from ..analysis.pitch_table import RATE_NAMES, build_pitch_table, rate_terms
from .config_schema import ConfigError, ConfigField
from .guards import check_modeling_scope, check_purpose
from .methods import ResearchContext, ResearchMethod, ResearchResult, register_method
from .re_source import get_re_table, re_method_inputs
from .runner import run_checked
from .scope import normalize_scope
from .sections import make_section
from .sequencing_methods import BUNT_POLICY, FILTERS, PURPOSE, RE_SCOPE, _check_filters
from .stats import z_value

CONTROLS = ("count", "exposure", "hand", "prev1_description", "zone", "release_speed")
FIXED_EFFECTS = ("pitcher", "batter", "pitcher_game")
CLUSTERS = {"pitcher": "pitcher", "batter": "batter", "game": "game_pk"}
DIGITS = 8


class StreakRegressionMethod(ResearchMethod):
    """Fixed-effects linear probability model of a per-pitch rate on streak position (or the previous pitch type)."""

    kind = "streak_regression"
    version = 1
    label_zh = "連投迴歸（固定效應）"
    label_en = "Streak regression (fixed effects)"
    requires_scope = True
    fields = (
        PURPOSE, RE_SCOPE, BUNT_POLICY, FILTERS,
        ConfigField("confidence", "float", default=0.99, minimum=0.5, maximum=0.999, label_zh="信賴水準", label_en="Confidence level"),
        ConfigField("rate", "choice", default="whiff_per_swing", choices=RATE_NAMES, label_zh="被觀察的比率", label_en="Rate"),
        ConfigField("foul_tip_is_whiff", "bool", default=False, label_zh="擦棒算揮空", label_en="Count foul tips as whiffs"),
        ConfigField("pitch_types", "str_list", default=[], label_zh="球種（空白＝全部）", label_en="Pitch types (empty = all)"),
        ConfigField("min_type_pitches", "int", default=5000, minimum=2, label_zh="球種最小合格球數", label_en="Minimum eligible pitches per pitch type"),
        ConfigField("treatment", "choice", default="streak_position", choices=("streak_position", "prev1_pitch_type"), label_zh="處理變數", label_en="Treatment",
                    help_zh="streak_position：連投顆數；prev1_pitch_type：前一球球種（參考組＝與當前同球種）。", help_en="Reference for prev1_pitch_type is the same pitch type as the current pitch."),
        ConfigField("kmax", "int", default=4, minimum=2, maximum=10, label_zh="連投顆數上限", label_en="Largest streak position"),
        ConfigField("hand_view", "choice", default="four_groups", choices=("four_groups", "pooled"), label_zh="左右手檢視", label_en="Handedness view"),
        ConfigField("controls", "str_list", default=["count", "exposure"], label_zh="控制變數", label_en="Controls"),
        ConfigField("fixed_effects", "str_list", default=["pitcher", "batter"], label_zh="固定效應", label_en="Fixed effects"),
        ConfigField("cluster_by", "choice", default="pitcher", choices=tuple(CLUSTERS), label_zh="標準誤分群單位", label_en="Cluster unit"),
        ConfigField("min_term_pitches", "int", default=200, minimum=1, label_zh="前一球球種最小球數（不足併入 OTHER）", label_en="Minimum pitches per previous pitch type"),
        ConfigField("fe_max_iter", "int", default=200, minimum=1, label_zh="固定效應最大迭代", label_en="Fixed-effect iterations"),
        ConfigField("fe_tol", "float", default=1e-9, minimum=1e-15, label_zh="固定效應收斂門檻", label_en="Fixed-effect tolerance"),
        ConfigField("max_rows", "int", default=2_000_000, minimum=1000, label_zh="取回列數上限", label_en="Row cap"),
    )

    def validate(self, config: dict[str, Any], ctx: ResearchContext) -> None:
        check_purpose(ctx.scope, config["purpose"])
        for key, allowed in (("controls", CONTROLS), ("fixed_effects", FIXED_EFFECTS)):
            bad = sorted(set(config[key]) - set(allowed))
            if bad or len(set(config[key])) != len(config[key]):
                raise ConfigError(f"{key} must be distinct values of {list(allowed)}; got {config[key]} / {key} 只能是 {list(allowed)} 的不重複值")
        if "hand" in config["controls"] and config["hand_view"] != "pooled":
            raise ConfigError("the hand control only makes sense with hand_view=pooled / hand 控制只用於 pooled")
        _check_filters(config["filters"])
        if config["rate"] == "mean_pitch_value":
            config["re_scope"] = normalize_scope(config["re_scope"], required=True)
            check_modeling_scope(config["re_scope"], config["purpose"], "re_scope")

    def method_inputs(self, ctx: ResearchContext, config: dict[str, Any]) -> dict[str, Any]:
        return re_method_inputs(ctx, config) if config["rate"] == "mean_pitch_value" else {}

    def run(self, ctx: ResearchContext, config: dict[str, Any]) -> ResearchResult:
        import numpy as np

        from .fixed_effects import codes, dummies, fe_ols

        re = get_re_table(ctx, config["re_scope"]) if config["rate"] == "mean_pitch_value" else None
        table = build_pitch_table(ctx.source_node(), bunt_policy=config["bunt_policy"], memory=1, re_by_state=re.re_by_state if re else None,
                                  prior_same_type_count=True, filters=config["filters"])
        eligible, x = rate_terms(config["rate"], foul_tip_is_whiff=config["foul_tip_is_whiff"])
        keep = Filter(Filter(table, Binary(eligible, "=", Literal(1))), IsNull(Column("pitch_type"), True))
        keep = Filter(keep, IsNull(Column("hand_group"), True))
        if config["pitch_types"]:
            from ..analysis import InList

            keep = Filter(keep, InList(Column("pitch_type"), tuple(Literal(t) for t in config["pitch_types"])))
        cols = ("pitcher", "batter", "game_pk", "hand_group", "pitch_type", "streak_pos", "pitch_index_in_pa", "balls", "strikes",
                "prior_same_type_count", "prev1_description", "prev1_pitch_type", "zone", "release_speed", "p_throws", "stand")
        node = Limit(Sort(Project(keep, (NamedExpr("pitch_uid", Column("pitch_uid")),) + tuple(NamedExpr(c, Column(c)) for c in cols) + (NamedExpr("x", x),), PITCH_GRAIN),
                          (OrderKey(Column("game_pk")), OrderKey(Column("pitch_uid")))), config["max_rows"] + 1)
        result = run_checked(ctx, node)
        if len(result.rows) > config["max_rows"]:
            raise ConfigError(f"More than max_rows={config['max_rows']} pitch rows; narrow the scope, pitch types or filters / 逐球列數超過 max_rows")
        by_cell: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for row in result.rows:
            group = row["hand_group"] if config["hand_view"] == "four_groups" else "all"
            by_cell.setdefault((group, row["pitch_type"]), []).append(row)
        z = z_value(config["confidence"])
        coefficient_rows: list[dict[str, Any]] = []
        for (group, ptype), rows in sorted(by_cell.items()):
            base = {"hand_group": group, "pitch_type": ptype}
            if len(rows) < config["min_type_pitches"]:
                coefficient_rows.append({**base, "term": None, "n_obs": len(rows), "insufficient": 1})
                continue
            spec = self._design(rows, config, np, codes, dummies)
            if spec is None:
                coefficient_rows.append({**base, "term": None, "n_obs": len(rows), "insufficient": 1})
                continue
            y, X, terms, rows_used, dropped = spec
            fe = self._fixed_effects(rows_used, config, np, codes)
            cluster = [r[CLUSTERS[config["cluster_by"]]] for r in rows_used]
            fit = fe_ols(y, X, fe, cluster)
            fe_sizes = ";".join(f"{name}={int(g.max()) + 1}" for name, g in zip([n for n in FIXED_EFFECTS if n in config["fixed_effects"]], fe)) or "none"
            for index, term in enumerate(terms):
                beta, se = float(fit["beta"][index]), fit["se"][index]
                coefficient_rows.append({**base, "term": term, "estimate": beta, "se": se, "lo": None if se is None else beta - z * se,
                                         "hi": None if se is None else beta + z * se, "n_obs": len(rows_used), "n_informative": fit["informative"],
                                         "clusters": fit["clusters"], "fe_groups": fe_sizes, "converged": int(fit["converged"]),
                                         "rows_dropped": dropped, "low_clusters": int(fit["clusters"] < 30), "insufficient": 0})
        columns = ("hand_group", "pitch_type", "term", "estimate", "se", "lo", "hi", "n_obs", "n_informative", "clusters", "fe_groups", "converged",
                   "rows_dropped", "low_clusters", "insufficient")
        info = [{"setting": k, "value": str(config[k])} for k in ("rate", "treatment", "controls", "fixed_effects", "cluster_by", "kmax", "hand_view", "confidence")]
        return ResearchResult(
            sections=(make_section("Coefficients 係數（機率百分點；mean_pitch_value 為得分）", columns, coefficient_rows, ("hand_group", "pitch_type", "term"), result.backend, DIGITS),
                      make_section("Specification 規格", ("setting", "value"), info, ("setting",), result.backend)),
            extras={"scope": ctx.scope, "model": "linear_probability", "re_scope": config["re_scope"] if re else None, "rows": len(result.rows)})

    @staticmethod
    def _design(rows, config, np, codes, dummies):
        """(y, X with the treatment columns first, term names, rows used, rows dropped) or None when nothing is estimable."""

        used = rows
        if config["treatment"] == "prev1_pitch_type":
            used = [r for r in used if r["prev1_pitch_type"] is not None]
        else:
            used = [r for r in used if r["streak_pos"] is not None]
        if "zone" in config["controls"] or "release_speed" in config["controls"]:
            pass
        dropped = len(rows) - len(used)
        if not used:
            return None
        y = np.array([float(r["x"]) for r in used])
        if config["treatment"] == "streak_position":
            k = np.minimum(np.array([int(r["streak_pos"]) for r in used]), config["kmax"])
            terms = [f"k={i}" for i in range(2, config["kmax"] + 1) if (k == i).any()]
            treat = np.column_stack([(k == int(t[2:])).astype(float) for t in terms]) if terms else None
        else:
            prev = np.array([("same" if r["prev1_pitch_type"] == r["pitch_type"] else r["prev1_pitch_type"]) for r in used], dtype=object)
            levels, counts = np.unique(prev, return_counts=True)
            small = {lv for lv, c in zip(levels, counts) if c < config["min_term_pitches"] and lv != "same"}
            prev = np.array(["OTHER" if p in small else p for p in prev], dtype=object)
            terms = [f"prev={lv}" for lv in sorted(set(prev) - {"same"})]
            treat = np.column_stack([(prev == t[5:]).astype(float) for t in terms]) if terms else None
        if treat is None:
            return None
        blocks = [treat]
        if "count" in config["controls"]:
            blocks.append(dummies([f'{r["pitch_index_in_pa"]}-{r["balls"]}-{r["strikes"]}' for r in used]))
        if "exposure" in config["controls"]:
            blocks.append(dummies([min(int(r["prior_same_type_count"] or 0), 5) for r in used]))
        if "hand" in config["controls"]:
            blocks.append(dummies([f'{r["p_throws"]}{r["stand"]}' for r in used]))
        if "prev1_description" in config["controls"]:
            blocks.append(dummies([str(r["prev1_description"]) for r in used]))
        if "zone" in config["controls"]:
            blocks.append(dummies([str(r["zone"]) for r in used]))
        if "release_speed" in config["controls"]:
            speed = np.array([np.nan if r["release_speed"] is None else float(r["release_speed"]) for r in used])
            missing = np.isnan(speed)
            filled = np.where(missing, np.nanmean(speed) if (~missing).any() else 0.0, speed)
            std = filled.std() or 1.0
            blocks.append(np.column_stack([(filled - filled.mean()) / std, missing.astype(float)]))
        X = np.hstack([b for b in blocks if b.shape[1]])
        return y, X, terms, used, dropped

    @staticmethod
    def _fixed_effects(rows, config, np, codes):
        out = []
        for name in FIXED_EFFECTS:
            if name not in config["fixed_effects"]:
                continue
            if name == "pitcher_game":
                out.append(codes([f'{r["pitcher"]}|{r["game_pk"]}' for r in rows]))
            else:
                out.append(codes([r[name] for r in rows]))
        return out


register_method(StreakRegressionMethod())
