"""Research method ``action_effects`` (P6 plan A.3): the fixed-effects anchor of the league model and the value of balls in play by pitch type."""
from __future__ import annotations

from typing import Any

from .config_schema import ConfigError, ConfigField
from .guards import check_modeling_scope, check_purpose, scope_years
from .league_methods import DIGITS, GROUPS, fetch_columns
from .methods import ArtifactData, ResearchContext, ResearchMethod, ResearchResult, register_method
from .policy_methods import ball_in_play_value, pack_arrays, pc_types
from .re_source import get_re_table, re_method_inputs
from .scope import normalize_scope
from .sections import make_section
from .sequencing_methods import BUNT_POLICY, FILTERS, PURPOSE, RE_SCOPE, _check_filters


class _Lazy:
    def __init__(self, name: str):
        self._name = name

    def __getattr__(self, item: str):
        import importlib

        return getattr(importlib.import_module(self._name, __package__), item)


np = _Lazy("numpy")
anc = _Lazy(".anchor")
pc = _Lazy(".policy_core")
lc = _Lazy(".league_core")


class ActionEffectsMethod(ResearchMethod):
    kind = "action_effects"
    version = 1
    label_zh = "球種效應錨點（固定效應迴歸）與界內價值 J_BIP"
    label_en = "Pitch-type effects anchor (fixed-effects regression) and ball-in-play value J_BIP"
    requires_scope = True
    fields = (
        PURPOSE, BUNT_POLICY, FILTERS, RE_SCOPE,
        ConfigField("groups", "str_list", default=list(GROUPS), label_zh="分組（各自擬合）", label_en="Groups (fitted separately)"),
        ConfigField("designs", "str_list", default=["S3", "S4"], label_zh="固定效應設計", label_en="Fixed-effects designs",
                    help_zh="S3：投手＋打者；S4：投手×比賽＋打者（擋掉當天狀態）。", help_en="S3: pitcher and batter; S4: pitcher-game and batter (removes the form of the day)."),
        ConfigField("pitch_types", "str_list", default=[], label_zh="球種（空＝依支持規則）", label_en="Pitch types (empty = by the support rule)",
                    help_zh="第一個是參照球種。空表示依份額與球數選出，FF 在前。", help_en="The first is the reference. Empty picks the supported types, FF first."),
        ConfigField("support_min_share", "float", default=0.05, minimum=0.0, maximum=1.0, label_zh="支持規則：最小份額", label_en="Support rule: minimum share"),
        ConfigField("support_min_n", "int", default=300, minimum=1, label_zh="支持規則：最小球數", label_en="Support rule: minimum pitches"),
        ConfigField("confidence", "float", default=0.99, minimum=0.5, maximum=0.999, label_zh="信賴水準", label_en="Confidence level"),
        ConfigField("jbip_kappa", "float", default=50.0, minimum=0.0, label_zh="J_BIP 向主效應收縮的虛擬樣本數", label_en="J_BIP pseudo-count towards the additive fit"),
        ConfigField("max_rows", "int", default=1_500_000, minimum=1000, label_zh="每組每球季列數上限", label_en="Row cap per group and season"),
    )

    def validate(self, config: dict[str, Any], ctx: ResearchContext) -> None:
        if not config["groups"] or set(config["groups"]) - set(GROUPS):
            raise ConfigError(f"groups must be a non-empty subset of {list(GROUPS)} / groups 必須是 {list(GROUPS)} 的非空子集")
        if not config["designs"] or set(config["designs"]) - {"S3", "S4"}:
            raise ConfigError("designs must be a non-empty subset of S3, S4 / designs 必須是 S3、S4 的非空子集")
        if set(config["pitch_types"]) - set(pc_types()):
            raise ConfigError(f"pitch_types must be frozen types {pc_types()} / pitch_types 必須是凍結的球種")
        _check_filters(config["filters"])
        check_purpose(ctx.scope, config["purpose"])
        config["re_scope"] = normalize_scope(config["re_scope"], required=True)
        check_modeling_scope(config["re_scope"], config["purpose"], "re_scope")

    def method_inputs(self, ctx: ResearchContext, config: dict[str, Any]) -> dict[str, Any]:
        return re_method_inputs(ctx, config)

    def run(self, ctx: ResearchContext, config: dict[str, Any]) -> ResearchResult:
        years = scope_years(ctx.scope)
        re = get_re_table(ctx, config["re_scope"])
        cols_all = fetch_columns(ctx, config, config["groups"], years, re_by_state=re.re_by_state)
        shape = pc.Shape()
        fit_rows, contrast_rows, streak_rows, jbip_rows, artifacts = [], [], [], [], []
        re_cnt = np.zeros((shape.n_balls, shape.n_strikes, 3, 8))
        for b in range(shape.n_balls):
            for k in range(shape.n_strikes):
                for o in range(3):
                    for B in range(8):
                        re_cnt[b, k, o, B] = re.re_by_state[b * 1000 + k * 100 + o * 10 + B]
        for group in config["groups"]:
            sel = np.nonzero(cols_all["group"] == group)[0]
            if len(sel) == 0:
                raise ConfigError(f"Group {group} has no rows / 分組 {group} 沒有資料")
            cols = lc.take(cols_all, sel)
            types = list(config["pitch_types"]) or self._supported_types(cols, config)
            if len(types) < 2:
                raise ConfigError(f"Group {group}: fewer than two supported pitch types / 分組 {group} 的可用球種少於兩種")
            arrays = {"types": np.array(types)}
            for design in config["designs"]:
                fit = anc.fit_effects(cols, types=types, design=design)
                for c, cname in enumerate(pc.OUTCOMES):
                    fit_rows.append({"group": group, "design": design, "class": cname, "n": fit["n"], "clusters": fit["clusters"], "outside_01_share": float(fit["outside01"][c]),
                                     "converged": int(fit["converged"]), "types": ",".join(types)})
                rows = anc.action_contrasts(fit, config["confidence"])
                contrast_rows += [{"group": group, **r} for r in rows]
                streak_rows += [{"group": group, **r} for r in anc.streak_effects(fit, config["confidence"])]
                eff = np.full((3, shape.n_actions, pc.C), np.nan); se = np.full_like(eff, np.nan)
                for r in rows:
                    eff[anc.PHASES.index(r["phase"]), pc_types().index(r["action"]), pc.OC[r["class"]]] = r["effect"]
                    se[anc.PHASES.index(r["phase"]), pc_types().index(r["action"]), pc.OC[r["class"]]] = r["se"]
                arrays[f"eff_{design}"] = eff; arrays[f"se_{design}"] = se
            jb, info = anc.jbip_table(cols, types=types, value=ball_in_play_value(cols, re_cnt, shape), design=config["designs"][0], kappa=config["jbip_kappa"], n_actions=shape.n_actions)
            arrays["jbip"] = jb
            for ai, t in enumerate(pc_types()):
                for o in range(3):
                    for B in range(8):
                        if t in types:
                            jbip_rows.append({"group": group, "action": t, "outs": o, "bases": B, "value": float(jb[ai, o, B]), "n_in_play": info["n"]})
            artifacts.append(ArtifactData(f"anchor_{group}", f"Anchor of group {group}: contrasts [phase, action, class] and J_BIP [action, outs, bases] (npz/zlib/base64)", ("part", "payload"),
                                          tuple(pack_arrays(arrays))))
        sections = (
            make_section("Fit summary 擬合摘要（超出 [0,1] 的預測比例）", ("group", "design", "class", "n", "clusters", "outside_01_share", "converged", "types"), fit_rows, ("group", "design", "class"), "duckdb", DIGITS),
            make_section("Action contrasts 球種效應（相對同一球數階段的一般配球）", ("group", "design", "phase", "action", "class", "effect", "se", "lo", "hi", "share"), contrast_rows,
                         ("group", "design", "phase", "action", "class"), "duckdb", DIGITS),
            make_section("Streak effects 連投效應（第二、三顆以上相對第一顆）", ("group", "design", "term", "class", "effect", "se", "lo", "hi"), streak_rows, ("group", "design", "term", "class"), "duckdb", DIGITS),
            make_section("J_BIP 界內非全壘打的價值", ("group", "action", "outs", "bases", "value", "n_in_play"), jbip_rows, ("group", "action", "outs", "bases"), "duckdb", DIGITS),
        )
        return ResearchResult(sections=sections, extras={"scope": ctx.scope, "purpose": config["purpose"], "re_scope": config["re_scope"], "designs": config["designs"]}, artifacts=tuple(artifacts))

    @staticmethod
    def _supported_types(cols, config) -> list[str]:
        ptype = np.asarray(cols["pitch_type"]).astype(str)
        names = pc_types()
        counts = np.array([(ptype == t).sum() for t in names], dtype=float)
        share = counts / max(counts.sum(), 1.0)
        keep = [t for t, c, s in zip(names, counts, share) if c >= config["support_min_n"] and s >= config["support_min_share"]]
        return keep


register_method(ActionEffectsMethod())
