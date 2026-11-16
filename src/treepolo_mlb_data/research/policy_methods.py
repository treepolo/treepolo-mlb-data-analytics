"""Research method ``pitch_policy`` (P6 plan A.5): decision model, policies, situation table, marginal values and the margin, league-average query mode M0.

6.0b scope: the outcome model is the level-L0 boosted-tree model (situation and pitch type only, no player descriptors), fitted inside the method on the
whole scope with game-level bootstrap members; ``J_BIP`` is the descriptive run value of balls in play (the fixed-effects version comes with
``action_effects``). Player-specific queries (M1-M3) are the job of ``pitch_query``.
"""
from __future__ import annotations

import base64
import io
import json
import zlib
from typing import Any

from .config_schema import ConfigError, ConfigField
from .guards import scope_years
from .league_methods import GROUPS, DIGITS, fetch_columns
from .methods import ArtifactData, ResearchContext, ResearchMethod, ResearchResult, register_method
from .re_source import get_re_table, re_method_inputs
from .scope import normalize_scope
from .sections import make_section
from .sequencing_methods import BUNT_POLICY, FILTERS, RE_SCOPE, _check_filters


class _Lazy:
    def __init__(self, name: str):
        self._name = name

    def __getattr__(self, item: str):
        import importlib

        return getattr(importlib.import_module(self._name, __package__), item)


np = _Lazy("numpy")
pc = _Lazy(".policy_core")
pb = _Lazy(".policy_build")
lc = _Lazy(".league_core")
gbm = _Lazy(".gbm")
pe = _Lazy(".pitcher_engine")
POLICY_NAMES = ("observed", "const_FF", "greedy1", "dp_unrestricted", "dp_trust")
PA_PER_TEAM_SEASON = 6200


# ------------------------------------------------------------------------------------------------ environment from the data
def build_environment(cols, re_by_state, shape, *, kappa: float = 50.0):
    """Run expectancy tables, the descriptive value of balls in play by pitch type, and how plate appearances start, from the pitch columns."""

    re_cnt = np.zeros((shape.n_balls, shape.n_strikes, 3, 8))
    for b in range(shape.n_balls):
        for k in range(shape.n_strikes):
            for o in range(3):
                for B in range(8):
                    re_cnt[b, k, o, B] = re_by_state[b * 1000 + k * 100 + o * 10 + B]
    re_pa = re_cnt[0, 0].copy()
    b = np.asarray(cols["balls"]).astype(int); k = np.asarray(cols["strikes"]).astype(int)
    o = np.asarray(cols["outs_when_up"]).astype(int); B = np.asarray(cols["bases"]).astype(int)
    first = (b == 0) & (k == 0) & (np.asarray(cols["prev1_pitch_type"]).astype(str) == "none")
    start = np.zeros((3, 8)); np.add.at(start, (o[first & (o < 3)], B[first & (o < 3)]), 1.0)
    # value after a ball in play that is not a home run: the pitch's run value plus the run expectancy of its own state
    cls = np.array([{"in_play_out": 1, "in_play_single": 1, "in_play_double": 1, "in_play_triple": 1, "in_play_error_other": 1}.get(x, 0) for x in np.asarray(cols["outcome"]).astype(str)])
    value = ball_in_play_value(cols, re_cnt, shape)
    ptype = np.asarray(cols["pitch_type"]).astype(str)
    a = np.array([pc_types().index(t) if t in pc_types() else -1 for t in ptype])
    ok = (cls == 1) & np.isfinite(value) & (a >= 0) & (o < 3)
    jbip = np.zeros((shape.n_actions, 3, 8))
    if ok.sum():
        m_oB = np.zeros((3, 8)); n_oB = np.zeros((3, 8))
        np.add.at(m_oB, (o[ok], B[ok]), value[ok]); np.add.at(n_oB, (o[ok], B[ok]), 1.0)
        overall = value[ok].mean()
        m_oB = np.where(n_oB > 0, m_oB / np.maximum(n_oB, 1), overall)
        resid = value[ok] - m_oB[o[ok], B[ok]]
        eff = np.zeros(shape.n_actions); cnt = np.zeros(shape.n_actions)
        np.add.at(eff, a[ok], resid); np.add.at(cnt, a[ok], 1.0)
        eff = np.where(cnt > 0, eff / np.maximum(cnt, 1), 0.0)
        base = m_oB[None, :, :] + eff[:, None, None]
        s_a = np.zeros(jbip.shape); n_a = np.zeros(jbip.shape)
        np.add.at(s_a, (a[ok], o[ok], B[ok]), value[ok]); np.add.at(n_a, (a[ok], o[ok], B[ok]), 1.0)
        jbip = (s_a + kappa * base) / (n_a + kappa)
    else:
        jbip[:] = re_pa[None]
    return pc.Environment(re_pa=re_pa, re_cnt=re_cnt, jbip=jbip, start=np.maximum(start, 1e-9))


def ball_in_play_value(cols, re_cnt, shape):
    """Runs on the pitch plus the run expectancy afterwards, per pitch: the pitch's run value plus the run expectancy of the state it was thrown in."""

    b = np.asarray(cols["balls"]).astype(int); k = np.asarray(cols["strikes"]).astype(int)
    o = np.asarray(cols["outs_when_up"]).astype(int); B = np.asarray(cols["bases"]).astype(int)
    return np.asarray(cols["pitch_value"], dtype=float) + re_cnt[np.minimum(b, shape.n_balls - 1), np.minimum(k, shape.n_strikes - 1), np.minimum(o, 2), B]


def pc_types():
    from .profiles import PITCH_TYPES

    return list(PITCH_TYPES)


def pack_arrays(arrays: dict[str, Any]) -> list[dict[str, Any]]:
    buf = io.BytesIO(); np.savez_compressed(buf, **arrays)
    blob = base64.b64encode(zlib.compress(buf.getvalue())).decode("ascii")
    return [{"part": i // 400_000, "payload": blob[i:i + 400_000]} for i in range(0, len(blob), 400_000)]


def unpack_arrays(rows) -> dict[str, Any]:
    blob = "".join(r["payload"] for r in sorted(rows, key=lambda r: r["part"]))
    with np.load(io.BytesIO(zlib.decompress(base64.b64decode(blob)))) as z:
        return {k: z[k] for k in z.files}


class PitchPolicyMethod(ResearchMethod):
    kind = "pitch_policy"
    version = 12     # 12: the day and batter effects are shrunk by empirical Bayes before they are averaged. 11: the per-pitcher engine averages the day and the batter effects over his plate appearances on the probability scale (pitcher_context). 10: one shrinkage factor per (pitcher, type) unit for all classes. 9: the per-pitcher check compares with what each pitcher really did, the stored per-pitcher policies hold the full shares and the plate appearances. 8: the per-pitcher engine merges the pitcher's overall effect into the shrunk effect, a check section, the recommendations are stored. 7: the cross-season evaluation has intervals (every bagged member chooses on one season and is judged on the other), cross rows come before the same-data rows, a per-pitcher engine. 6: 6: support judged by count and pitch type only (shared by every state of the count), min_share and min_count documented, a "Support coverage" section. 5: states with no supported action keep the observed behaviour (they were left at value 0) and every policy plays it there. 4: the outcome model comes from an outcome_model_train run (model_run_id); no reserved-season rule. 3: no verdicts or tau, comparison counts, per-year replication
    label_zh = "配球決策引擎（動態規劃、情境表、餘裕）"
    label_en = "Pitch policy engine (dynamic programming, situation table, margin)"
    requires_scope = True
    fields = (
        BUNT_POLICY, FILTERS, RE_SCOPE,
        ConfigField("model_run_id", "int", default=0, minimum=0, label_zh="outcome_model_train 紀錄編號（被拿來算期望失分的模型）", label_en="outcome_model_train run id (the model behind the expected runs)",
                    help_zh="需要用 save_package 執行過。模型用了哪些偏差控制，情境表就是在那些假設下算的。", help_en="Needs a run made with save_package. The situation table is computed under the controls that model used."),
        ConfigField("confidence", "float", default=0.95, minimum=0.5, maximum=0.999, label_zh="整體數字的信賴水準", label_en="Confidence level of the overall numbers"),
        ConfigField("cell_confidence", "float", default=0.99, minimum=0.5, maximum=0.999, label_zh="情境格的信賴水準", label_en="Confidence level of the situation cells"),
        ConfigField("groups", "str_list", default=list(GROUPS), label_zh="分組（各自求解，不合併）", label_en="Groups (solved separately)"),
        ConfigField("policies", "str_list", default=list(POLICY_NAMES), label_zh="政策", label_en="Policies"),
        ConfigField("trust_region", "float", default=0.10, minimum=0.0, maximum=1.0, label_zh="信賴區域（份額最多移動）", label_en="Trust region (largest share move)"),
        ConfigField("marginal_shift", "float", default=0.10, minimum=0.0, maximum=0.5, label_zh="邊際價值的移動量", label_en="Share moved for the marginal value"),
        ConfigField("lambda_grid", "json", default=[0.0, 0.5, 1.0, 2.0], label_zh="全壘打懲罰 λ 網格", label_en="Home-run penalty grid"),
        ConfigField("replication_members", "int", default=3, minimum=0, maximum=50, label_zh="換年份重看：每個年份單獨訓練的成員數（0＝不做）", label_en="Per-year replication: members per single year (0 = skip)",
                    help_zh="資料有兩個以上年份時，每個年份各自單獨訓練一份模型，並排列出各年份的估計；只並排數字，不做同號就算重現的判定。",
                    help_en="With two or more years, a model is trained on each year alone and the estimates are listed side by side; numbers only."),
        ConfigField("cross_eval", "bool", default=True, label_zh="交叉評估（一季選、另一季評）", label_en="Cross evaluation (choose on one season, judge on the other)"),
        ConfigField("cross_members", "int", default=5, minimum=1, maximum=50, label_zh="交叉評估每季成員數", label_en="Members per season for the cross evaluation"),
        ConfigField("min_share", "float", default=0.05, minimum=0.0, maximum=1.0, label_zh="支持規則：最小份額", label_en="Support rule: minimum share",
                    help_zh="只依「球數 × 球種」判斷：這個球種在該球數的所有投球（不分前一球、連投、出局、壘包）裡至少占這個比例，才算被支持；同一球數的所有狀態共用。",
                    help_en="Judged by count and pitch type only: the type must be at least this share of the pitches of the count (any previous type, streak, outs, bases); every state of the count shares it."),
        ConfigField("min_count", "int", default=300, minimum=1, label_zh="支持規則：最小球數", label_en="Support rule: minimum pitches",
                    help_zh="只依「球數 × 球種」判斷：這個球種在該球數至少要投這麼多球，才算被支持；同一球數的所有狀態共用。",
                    help_en="Judged by count and pitch type only: the type must have been thrown at least this many times in the count; every state of the count shares it."),
        ConfigField("pitcher_engine", "bool", default=True, label_zh="逐投手引擎（每位投手用自己的投手×球種效應）", label_en="Per-pitcher engine (every pitcher with his own pitcher x pitch-type effects)",
                    help_zh="模型要有投手×球種效應（control_pitcher_pitch 開）。效應先做事後經驗貝氏收縮（資料少的往平均拉），當天狀態設為平常、打者是聯盟平均打者；每位投手各解一次引擎，按出場打席數加權加總。",
                    help_en="The model needs pitcher x pitch-type effects. The effects are shrunk after the fit by empirical Bayes, the day is the usual one and the batter the league average; the engine is solved once per pitcher and added up weighted by plate appearances."),
        ConfigField("pitcher_context", "choice", default="both", choices=("none", "day", "batter", "both"), label_zh="逐投手引擎：當天與打者怎麼處理", label_en="Per-pitcher engine: the day and the batter",
                    help_zh="none＝平常的一天、聯盟平均打者；day＝用他每一場的效應；batter＝用他面對過的打者；both＝兩者都用。都是對他的打席在機率尺度上平均。",
                    help_en="none = the usual day and the league-average batter; day = his effect of every game; batter = the batters he faced; both = both. Averaged over his plate appearances on the probability scale."),
        ConfigField("pitcher_min_pa", "int", default=200, minimum=1, label_zh="逐投手引擎：投手最少出場打席數（資料量門檻）", label_en="Per-pitcher engine: minimum plate appearances of a pitcher",
                    help_zh="只算打席數達到門檻的投手；結果會報告這些投手涵蓋了多少比例的打席。", help_en="Only pitchers with at least this many plate appearances; the result says what share of all plate appearances they cover."),
        ConfigField("anchor_run_id", "int", default=0, minimum=0, label_zh="錨點（action_effects）紀錄編號，0＝不用", label_en="Anchor run id (action_effects), 0 = none",
                    help_zh="用該紀錄的 J_BIP 取代描述性估計，並做錨點一致性檢查（計畫 3.6）。", help_en="Uses its J_BIP instead of the descriptive estimate and runs the anchor consistency check."),
        ConfigField("seed", "int", default=20240601, label_zh="隨機種子", label_en="Random seed"),
        ConfigField("save_table", "bool", default=True, label_zh="保存情境表附件", label_en="Save the situation table"),
        ConfigField("max_rows", "int", default=1_500_000, minimum=1000, label_zh="每組每球季列數上限", label_en="Row cap per group and season"),
    )

    def validate(self, config: dict[str, Any], ctx: ResearchContext) -> None:
        if not config["groups"] or set(config["groups"]) - set(GROUPS):
            raise ConfigError(f"groups must be a non-empty subset of {list(GROUPS)} / groups 必須是 {list(GROUPS)} 的非空子集")
        if not config["policies"] or set(config["policies"]) - set(POLICY_NAMES) or "observed" not in config["policies"]:
            raise ConfigError(f"policies must be a subset of {list(POLICY_NAMES)} containing observed / policies 必須含 observed 且是 {list(POLICY_NAMES)} 的子集")
        grid = config["lambda_grid"]
        if not isinstance(grid, list) or not grid or not all(isinstance(x, (int, float)) and x >= 0 for x in grid):
            raise ConfigError("lambda_grid must be a non-empty list of non-negative numbers / lambda_grid 必須是非負數的非空列表")
        _check_filters(config["filters"])
        config["re_scope"] = normalize_scope(config["re_scope"], required=True)
        model_run = self._model_run(ctx, config)
        if set(config["groups"]) - set(model_run.get("groups") or config["groups"]):
            raise ConfigError(f"the model run has no model for groups {sorted(set(config['groups']) - set(model_run['groups']))} / 模型紀錄沒有這些分組的模型")
        if config["anchor_run_id"]:
            self._anchor_run(ctx, config)

    @staticmethod
    def _model_run(ctx: ResearchContext, config: dict[str, Any]) -> dict[str, Any]:
        """The ``outcome_model_train`` run behind the situation table (and the groups it has a stored model for)."""

        if not config["model_run_id"]:
            raise ConfigError("model_run_id is required: train a model with outcome_model_train first / 需要 model_run_id：請先用 outcome_model_train 訓練模型")
        run = ctx.store.get_run(int(config["model_run_id"])) if ctx.store is not None else None
        if run is None or run["kind"] != "outcome_model_train" or run["status"] != "success":
            raise ConfigError(f"model_run_id {config['model_run_id']} is not a successful outcome_model_train run / model_run_id 不是成功的 outcome_model_train 紀錄")
        if ctx.store.load_result(int(config["model_run_id"]))["extras"].get("model") == "sequence":
            raise ConfigError("the sequence model cannot be the model behind the decision engine: its predictions need the earlier pitches of the plate appearance, which the situation grid "
                              "does not have / 序列模型不能當決策引擎用的模型：它的預測需要打席內的前序投球，情境格沒有")
        groups = [g for g in GROUPS if ctx.store.load_artifact(int(config["model_run_id"]), f"outcome_model_{g}") is not None]
        if not groups:
            raise ConfigError("the model run stored no model: run it with save_package / 模型紀錄沒有保存模型，請用 save_package 執行")
        return {**run, "groups": groups}

    @staticmethod
    def _anchor_run(ctx: ResearchContext, config: dict[str, Any]) -> dict[str, Any]:
        run = ctx.store.get_run(int(config["anchor_run_id"])) if ctx.store is not None else None
        if run is None or run["kind"] != "action_effects" or run["status"] != "success":
            raise ConfigError(f"anchor_run_id {config['anchor_run_id']} is not a successful action_effects run / anchor_run_id 不是成功的 action_effects 紀錄")
        return run

    def method_inputs(self, ctx: ResearchContext, config: dict[str, Any]) -> dict[str, Any]:
        out = {**re_method_inputs(ctx, config), "model_run_key": self._model_run(ctx, config)["run_key"]}
        if config["anchor_run_id"]:
            out = {**out, "anchor_run_key": self._anchor_run(ctx, config)["run_key"]}
        return out

    # ------------------------------------------------------------------------------------------------ run
    def run(self, ctx: ResearchContext, config: dict[str, Any]) -> ResearchResult:
        from .outcome_model_methods import load_model

        years = scope_years(ctx.scope)
        re = get_re_table(ctx, config["re_scope"])
        cols_all = fetch_columns(ctx, config, config["groups"], years, re_by_state=re.re_by_state)
        shape = pc.Shape()
        r1_rows, value_rows, risk_rows, marg_rows, support_rows, artifacts = [], [], [], [], [], []
        direction_rows, anchor_rows, anchor_cell_rows, replication_rows, coverage_rows = [], [], [], [], []
        pitcher_summary, pitcher_detail, pitcher_cov, pitcher_info = [], [], [], {}
        progress = ctx.progress
        comparisons: dict[str, dict[str, Any]] = {"marginal": {}, "direction": {}, "anchor": {}}
        for group in config["groups"]:
            sel = np.nonzero(cols_all["group"] == group)[0]
            if len(sel) == 0:
                raise ConfigError(f"Group {group} has no rows / 分組 {group} 沒有資料")
            cols = lc.take(cols_all, sel)
            env = build_environment(cols, re.re_by_state, shape)
            if progress:
                progress("pitch_policy", None, f"group {group}")
            year_models = {"need": max(int(config["cross_members"]) if config["cross_eval"] else 1, int(config["replication_members"]), 1)}      # the model of each season alone, trained once for both uses
            anchor = self._load_anchor(ctx, config, group)
            if anchor is not None:
                env.jbip = np.where(np.isfinite(anchor["jbip"]) & (anchor["jbip"] != 0), anchor["jbip"], env.jbip)
            model = load_model(ctx.store.load_artifact(int(config["model_run_id"]), f"outcome_model_{group}")["rows"])           # the model the user trained, with its controls
            model.group = group
            tensor = pb.model_tensor(model, shape, group, effects="population")
            obs, support, n = pb.usage_from_counts(cols, shape, min_share=config["min_share"], min_count=config["min_count"])
            dyns = pb.members_dynamics(tensor, obs, support, n)
            coverage_rows.append({"group": group, **pb.support_coverage(cols, shape, support, n)})
            kinds = tuple(k for k in pc.POLICIES if k in config["policies"])
            an = pb.analyze(dyns, env, shape, kinds=kinds, trust=config["trust_region"], delta=config["marginal_shift"])
            r1_rows += self._r1(group, cols, an, env, shape)
            if config["cross_eval"] and len(set(int(y) for y in np.unique(cols["game_year"]))) >= 2:
                value_rows += self._cross(group, cols, model, env, shape, obs, support, n, config, kinds, year_models)           # the cross-season rows come first
            value_rows += self._values(group, an, config)
            risk_rows += self._risk(group, dyns, env, shape, config)
            m_rows, s_rows = self._marginals(group, an, config)
            marg_rows += m_rows; support_rows += s_rows
            d_rows = self._direction(group, an, config)
            direction_rows += d_rows
            comparisons["marginal"][group] = pc.chance_summary([r["adv_lo"] for r in m_rows], [r["adv_hi"] for r in m_rows], config["cell_confidence"])
            comparisons["direction"][group] = pc.chance_summary([r["adv_lo"] for r in d_rows], [r["adv_hi"] for r in d_rows], config["cell_confidence"])
            if anchor is not None:
                summary_rows, cell_rows = self._anchor_rows(group, an, tensor, anchor, shape)
                anchor_rows += summary_rows; anchor_cell_rows += cell_rows
                comparisons["anchor"][group] = pc.chance_row(sum(r["cells"] for r in summary_rows), 0.95, sum(r["outside_anchor_interval"] for r in summary_rows), "outside_anchor_interval")
            if config["replication_members"] and len(set(int(y) for y in np.unique(cols["game_year"]))) >= 2:
                replication_rows += self._replicate(group, cols, model, env, shape, obs, support, n, config, year_models)
            if config["pitcher_engine"]:
                why = pe.unavailable(model)
                if why:
                    pitcher_info[group] = {"enabled": False, "reason": why}
                else:
                    sub_g, _ = model._encode(cols, model.settings, fixed_classes=True)
                    league = {k: float(np.mean(an.saving_pa[k])) for k in kinds}
                    s_rows, d_rows, cov, pol = pe.run(group, sub_g, model, env, shape, obs, support, n, kinds=kinds, trust=config["trust_region"], min_pa=int(config["pitcher_min_pa"]), context=config["pitcher_context"],
                                                 pa_per_team_season=PA_PER_TEAM_SEASON, league_saving=league, league_start=float(np.mean(an.start["observed"])), league_hr=float(np.mean(an.hr_pa["observed"])),
                                                 progress=(lambda i, total, g=group: progress("pitch_policy", None, f"group {g}: pitcher engine {i}/{total}")) if progress else None)
                    pitcher_summary += s_rows; pitcher_detail += d_rows
                    if pol["pitchers"].size:
                        artifacts.append(ArtifactData(f"pitcher_policies_{group}", f"The recommended pitch type of every pitcher with enough plate appearances, per state, for dp_trust and dp_unrestricted (group {group}; npz)",
                                                      ("part", "payload"), tuple(pack_arrays(pol))))
                    pitcher_cov.append({**cov, "tau_by_class": json.dumps(cov["tau_by_class"], ensure_ascii=False)})
                    pitcher_info[group] = {"enabled": True, "pitchers_used": cov["pitchers_used"], "pitchers_total": cov["pitchers_total"], "pa_share": cov["pa_share"]}
                    del sub_g
            if config["save_table"]:
                artifacts.append(self._table_artifact(group, an, tensor, config, env))
        model_extras = ctx.store.load_result(int(config["model_run_id"]))["extras"]
        extras = {"scope": ctx.scope, "re_scope": config["re_scope"], "classes": list(pc.OUTCOMES), "model_run_id": config["model_run_id"], "model": model_extras.get("model"),
                  "model_controls": model_extras.get("controls"), "state_layout": "balls,strikes,outs,bases,prev(9 types + none),streak(1,2,3+)",
                  "mode": f"M0 league average player of the {model_extras.get('model')} model", "comparisons": {k: v for k, v in comparisons.items() if v},
                  "notes": policy_notes(config, bool(pitcher_summary)), "pitcher_engine": {"enabled": bool(config["pitcher_engine"]), "min_pa": int(config["pitcher_min_pa"]), "groups": pitcher_info}}
        sections = (
            make_section("R1 對照（觀察到的配球重現得分期望值）", ("group", "item", "n_states", "median_abs_diff", "max_abs_diff", "value"), r1_rows, ("group", "item"), "duckdb", DIGITS),
            make_section("Policy value 政策價值（每打席；換年份交叉在前，同資料在後：同資料的數字含挑選的樂觀偏差）", ("group", "policy", "kind", "start_value", "saving_pa", "lo", "hi", "saving_per_pitch", "saving_per_team_season", "hr_probability",
                                                              "pitches_per_pa"), value_rows, ("group", "policy", "kind"), "duckdb", DIGITS),
            make_section("Risk frontier 取捨前緣（λ 對全壘打）", ("group", "lambda", "expected_runs", "lo", "hi", "hr_probability", "hr_lo", "hr_hi", "saving_vs_observed"), risk_rows,
                         ("group", "lambda"), "duckdb", DIGITS),
            make_section("Marginal value 邊際價值摘要（依觀察到的出現頻率加權）", ("group", "action", "advantage", "adv_lo", "adv_hi", "marginal", "m_lo", "m_hi", "supported_share"),
                         marg_rows, ("group", "action"), "duckdb", DIGITS),
            make_section("Direction map 方向圖（球數×前一球 × 球種；邊際價值；全部列出）", ("group", "context", "balls", "strikes", "prev", "action", "marginal", "advantage", "adv_lo", "adv_hi", "n"),
                         direction_rows, ("group", "context", "action"), "duckdb", DIGITS),
            make_section("Support 支持表", ("group", "action", "supported_share", "observed_share"), support_rows, ("group", "action"), "duckdb", DIGITS),
            make_section("Support coverage 支持涵蓋（狀態數、無支持狀態數、實際投球落在不被支持球種的比例；只有數字）",
                         ("group", "states", "supported_states", "no_support_states", "seen_states", "seen_no_support_states", "pitches", "pitch_share_unsupported", "supported_types"),
                         coverage_rows, ("group",), "duckdb", DIGITS),
        )
        if replication_rows:
            sections = sections + (make_section("Replication 換年份重看（每個年份單獨訓練的估計；context＝ALL 是整個球種的彙總）",
                                                ("group", "year", "context", "action", "advantage", "adv_lo", "adv_hi", "marginal", "m_lo", "m_hi", "n"),
                                                replication_rows, ("group", "year", "context", "action"), "duckdb", DIGITS),)
            extras["replication"] = {"members_per_year": int(config["replication_members"]), "years": sorted({int(r["year"]) for r in replication_rows})}
        if anchor_rows:
            sections = sections + (make_section("Anchor 錨點對照彙總（模型效果對固定效應效果；只有數字）", ("group", "design", "cells", "same_sign_share", "median_ratio", "outside_anchor_interval"),
                                                anchor_rows, ("group", "design"), "duckdb", DIGITS),
                                   make_section("Anchor cells 錨點逐格對照（全部列出）", ("group", "design", "phase", "action", "class", "model_effect", "anchor_effect", "anchor_lo", "anchor_hi",
                                                                                     "difference", "same_sign"), anchor_cell_rows, ("group", "design", "phase", "action", "class"), "duckdb", DIGITS))
            extras["anchor_run_id"] = config["anchor_run_id"]
        if pitcher_summary:
            kinds_p = [r["policy"] for r in pitcher_summary if r["group"] == pitcher_summary[0]["group"]]
            sections = sections + (
                make_section("Pitcher engine 逐投手引擎彙總（每位投手用自己的投手×球種效應，效應已事後做經驗貝氏收縮；當天狀態＝平常，打者＝聯盟平均；按出場打席加權；沒有區間）",
                             ("group", "policy", "pitchers", "pa_share", "saving_pa", "saving_p10", "saving_median", "saving_p90", "saving_per_team_season", "start_value", "hr_probability",
                              "league_saving_pa"), pitcher_summary, ("group", "policy"), "duckdb", DIGITS),
                make_section("Pitcher engine coverage 逐投手引擎涵蓋（打席數門檻、涵蓋的投手與打席比例；tau＝各結果類別的真實效應標準差估計）",
                             ("group", "min_pa", "pitchers_total", "pitchers_used", "pa_total", "pa_used", "pa_share", "pitch_share", "units", "units_pulled_over_half", "tau_by_class"),
                             pitcher_cov, ("group",), "duckdb", DIGITS),
                make_section("Pitcher engine check 逐投手引擎檢查（照實際配球打：引擎算的每位投手每打席失分（起點期望失分減聯盟的）和全壘打率，對他自己在這些資料裡的實際值（每打席各球得分期望值變化加總）；最後一列是按打席加權平均）",
                             ("group", "pitcher", "pa", "predicted_excess_runs", "actual_excess_runs", "hr_predicted", "hr_actual", "shrink_mean"),
                             [{"group": d["group"], "pitcher": str(d["pitcher"]), "pa": d["pa"], "predicted_excess_runs": d.get("predicted_excess_runs"), "actual_excess_runs": d.get("actual_excess_runs"),
                               "hr_predicted": d["hr_observed"], "hr_actual": d.get("actual_hr"), "shrink_mean": d["shrink_mean"]} for d in pitcher_detail]
                             + [{"group": c["group"], "pitcher": "ALL (weighted by plate appearances)", "pa": c["pa_used"], "predicted_excess_runs": c.get("predicted_excess_runs"),
                                 "actual_excess_runs": c.get("actual_excess_runs"), "hr_predicted": c.get("hr_predicted"), "hr_actual": c.get("hr_actual"), "shrink_mean": None}
                                for c in pitcher_cov if "predicted_excess_runs" in c], ("group", "pitcher"), "duckdb", DIGITS),
                make_section("Pitcher engine detail 逐投手引擎明細（每位投手一列；saving＝比實際配球每打席少失分）",
                             ("group", "pitcher", "pa", "pitches", "units", "shrink_mean", "start_observed", "hr_observed") + tuple(f"saving_{k}" for k in kinds_p if k != "observed"),
                             pitcher_detail, ("group", "pitcher"), "duckdb", DIGITS))
        return ResearchResult(sections=sections, extras=extras, artifacts=tuple(artifacts))

    # ------------------------------------------------------------------------------------------------ sections
    @staticmethod
    def _r1(group, cols, an, env, shape):
        """The observed behaviour in the model should reproduce the run expectancy of every (count, outs, bases) state (plan 4.1)."""

        # value of the observed policy per state, averaged over the members, then over (previous type, streak) by how often they occur
        b = np.asarray(cols["balls"]).astype(int); k = np.asarray(cols["strikes"]).astype(int)
        o = np.asarray(cols["outs_when_up"]).astype(int); B = np.asarray(cols["bases"]).astype(int)
        ok = (b < shape.n_balls) & (k < shape.n_strikes) & (o < 3)
        n_state = np.zeros((shape.n_balls, shape.n_strikes, 3, 8)); np.add.at(n_state, (b[ok], k[ok], o[ok], B[ok]), 1.0)
        V = an.extras["v_observed"]
        bb, kk, oo, bs, pp, rr = shape.coords()
        occ = an.occupancy
        model = np.zeros(n_state.shape)
        for bi in range(shape.n_balls):
            for ki in range(shape.n_strikes):
                for oi in range(3):
                    for Bi in range(8):
                        m = (bb == bi) & (kk == ki) & (oo == oi) & (bs == Bi)
                        w = occ[m]
                        model[bi, ki, oi, Bi] = float((V[m] * w).sum() / w.sum()) if w.sum() > 0 else float(V[m].mean())
        diff = np.abs(model - env.re_cnt)[n_state >= 1000]
        start_model = float((env.start * np.array([[model[0, 0, oi, Bi] for Bi in range(8)] for oi in range(3)])).sum() / env.start.sum())
        start_re = float((env.start * env.re_cnt[0, 0]).sum() / env.start.sum())
        rows = [{"group": group, "item": "states with n >= 1000", "n_states": int(diff.size), "median_abs_diff": float(np.median(diff)) if diff.size else float("nan"),
                 "max_abs_diff": float(diff.max()) if diff.size else float("nan"), "value": float("nan")},
                {"group": group, "item": "start of plate appearance, weighted", "n_states": 24, "median_abs_diff": abs(start_model - start_re), "max_abs_diff": abs(start_model - start_re),
                 "value": start_model}]
        return rows

    @staticmethod
    def _values(group, an, config):
        rows = []
        conf = config["confidence"]
        for kind in an.kinds:
            mean, lo, hi = an.interval(an.saving_pa[kind], conf)
            hr = float(np.mean(an.hr_pa[kind]))
            rows.append({"group": group, "policy": kind, "kind": "same data", "start_value": float(np.mean(an.start[kind])), "saving_pa": float(mean), "lo": float(lo), "hi": float(hi),
                         "saving_per_pitch": float(mean) / an.pitches_per_pa, "saving_per_team_season": float(mean) * PA_PER_TEAM_SEASON, "hr_probability": hr,
                         "pitches_per_pa": an.pitches_per_pa})
        return rows

    def _year_model(self, cache, group, cols, model, config, year, members):
        """The model trained on one season alone, with ``members`` bagged members. It is trained once per season with as many members as either use needs (``cache['need']``) and shared
        between the cross-season evaluation and the per-year replication."""

        key = (group, int(year))
        have = cache.get(key)
        need = max(int(cache.get("need", members)), int(members))
        if have is None or len(have.members) < need:
            sub = lc.take(cols, np.nonzero(np.asarray(cols["game_year"]) == year)[0])
            have = self._train_like(model, sub, need, config["seed"] + int(year))
            cache[key] = have
        return self._prefix(have, int(members))

    @staticmethod
    def _prefix(model, k: int):
        """The same model with only its first ``k`` bagged members."""

        if len(model.members) <= k:
            return model
        import copy

        view = copy.copy(model)
        view.members = list(model.members[:k])
        return view

    def _cross(self, group, cols, model, env, shape, obs, support, n, config, kinds, cache):
        """Winner's-curse-free saving (plan 4.3) with intervals: every bagged member of the model of the first season chooses the policy and the same-numbered member of the model of the last
        season judges it, and the other way round (the mean of the two directions is that member's saving); the interval is the spread of the members. One member gives no interval."""

        years = sorted(int(y) for y in np.unique(cols["game_year"]))
        k = max(int(config["cross_members"]), 1)
        sets = []
        for y in (years[0], years[-1]):
            alone = self._year_model(cache, group, cols, model, config, y, k)
            sets.append(pb.members_dynamics(pb.model_tensor(alone, shape, group, effects="population"), obs, support, n))
        rows = []
        for kind in kinds:
            if kind in ("observed",):
                continue
            per_member = []
            for m in range(k):
                guard_checkpoint("cross-season evaluation")
                per_member.append(pb.cross_saving([sets[0][m]], [sets[1][m]], env, shape, kind, config["trust_region"]))
            mean, lo, hi = pc.member_interval(np.array(per_member)[:, None], config["confidence"])
            rows.append({"group": group, "policy": kind, "kind": f"cross {years[0]}/{years[-1]}", "start_value": float("nan"), "saving_pa": float(mean[0]), "lo": float(lo[0]), "hi": float(hi[0]),
                         "saving_per_pitch": float("nan"), "saving_per_team_season": float(mean[0]) * PA_PER_TEAM_SEASON, "hr_probability": float("nan"), "pitches_per_pa": float("nan")})
        return rows

    @staticmethod
    def _risk(group, dyns, env, shape, config):
        rows = []
        conf = config["confidence"]
        base_start = None
        for lam in config["lambda_grid"]:
            exp = np.zeros(len(dyns)); hr = np.zeros(len(dyns)); obs_exp = np.zeros(len(dyns))
            for m, dyn in enumerate(dyns):
                plain = pc.Engine(shape, dyn, env, lam=0.0)
                eng = pc.Engine(shape, dyn, env, lam=float(lam))
                _, pi = eng.solve("dp_trust", config["trust_region"])
                exp[m] = plain.start_value(plain.evaluate(pi)); hr[m] = plain.hr_probability(pi)
                obs_exp[m] = plain.start_value(plain.evaluate(plain.policy_observed()))
            me, lo, hi = pc.member_interval(exp[:, None], conf); mh, hlo, hhi = pc.member_interval(hr[:, None], conf)
            rows.append({"group": group, "lambda": float(lam), "expected_runs": float(me[0]), "lo": float(lo[0]), "hi": float(hi[0]), "hr_probability": float(mh[0]), "hr_lo": float(hlo[0]),
                         "hr_hi": float(hhi[0]), "saving_vs_observed": float(np.mean(obs_exp - exp))})
        return rows

    @staticmethod
    def _marginals(group, an, config):
        """Per action: the advantage over the observed mix of the others and the marginal value, weighted by how often each state occurs."""

        shape = an.shape
        occ = an.occupancy; valid = occ > 0
        m_rows, s_rows = [], []
        conf = config["cell_confidence"]
        mean_adv = an.interval(an.advantage, conf)[0]                  # the estimate per (state, action): which cells have a number at all
        from .profiles import PITCH_TYPES

        for a, name in enumerate(PITCH_TYPES):
            use = valid & an.support[:, a] & np.isfinite(mean_adv[:, a])
            w = occ[use]
            if w.sum() <= 0:
                s_rows.append({"group": group, "action": name, "supported_share": 0.0, "observed_share": float((an.obs[valid, a] * occ[valid]).sum() / occ[valid].sum())})
                continue
            per_member_adv = np.array([(np.nan_to_num(an.advantage[m][use, a]) * w).sum() / w.sum() for m in range(an.advantage.shape[0])])[:, None]
            per_member_m = np.array([(np.nan_to_num(an.marginal[m][use, a]) * w).sum() / w.sum() for m in range(an.marginal.shape[0])])[:, None]
            adv, alo, ahi = pc.member_interval(per_member_adv, conf); mg, mlo, mhi = pc.member_interval(per_member_m, conf)
            m_rows.append({"group": group, "action": name, "advantage": float(adv[0]), "adv_lo": float(alo[0]), "adv_hi": float(ahi[0]), "marginal": float(mg[0]), "m_lo": float(mlo[0]),
                           "m_hi": float(mhi[0]), "supported_share": float(w.sum() / occ[valid].sum())})
            s_rows.append({"group": group, "action": name, "supported_share": float(w.sum() / occ[valid].sum()), "observed_share": float((an.obs[valid, a] * occ[valid]).sum() / occ[valid].sum())})
        return m_rows, s_rows

    @staticmethod
    def _load_anchor(ctx, config, group):
        if not config["anchor_run_id"]:
            return None
        art = ctx.store.load_artifact(int(config["anchor_run_id"]), f"anchor_{group}")
        if art is None:
            raise ConfigError(f"the anchor run has no table for group {group} / 錨點紀錄沒有分組 {group} 的資料")
        return unpack_arrays(art["rows"])

    @staticmethod
    def _anchor_rows(group, an, tensor, anchor, shape):
        from . import anchor as anc

        types = [str(t) for t in anchor["types"]]
        model = anc.model_contrasts(tensor.mean(axis=0), an.obs, an.support, an.occupancy, shape, types)
        rows, cell_rows = [], []
        for key in sorted(k for k in anchor if k.startswith("eff_")):
            design = key[4:]
            anchor_rows = []
            for ph, pname in enumerate(anc.PHASES):
                for t in types:
                    ai = pc_types().index(t)
                    for c, cname in enumerate(pc.OUTCOMES):
                        anchor_rows.append({"phase": pname, "action": t, "class": cname, "effect": float(anchor[key][ph, ai, c]), "se": float(anchor[f"se_{design}"][ph, ai, c])})
            res = anc.anchor_consistency(model, anchor_rows)
            rows.append({"group": group, "design": design, **res["summary"]})
            cell_rows += [{"group": group, "design": design, **c} for c in res["cells"]]
        return rows, cell_rows

    @staticmethod
    def _direction(group, an, config):
        """Marginal value by (count, previous type) context and action: the occupancy-weighted mean over outs, bases and streak, with the member interval of that mean."""

        from .profiles import PITCH_TYPES

        shape = an.shape; occ = an.occupancy
        b, k, o, base, p, r = shape.coords()
        conf = config["cell_confidence"]
        rows = []
        M = an.advantage.shape[0]
        for bi in range(shape.n_balls):
            for ki in range(shape.n_strikes):
                for pi in range(shape.n_actions + 1):
                    if (pi == shape.none) != (bi == 0 and ki == 0):
                        continue
                    ctx_mask = (b == bi) & (k == ki) & (p == pi)
                    label = f"{bi}-{ki} | {'none' if pi == shape.none else PITCH_TYPES[pi]}"
                    for a, name in enumerate(PITCH_TYPES):
                        use = ctx_mask & an.support[:, a] & (occ > 0)
                        w = occ[use]
                        if w.sum() <= 0:
                            continue
                        adv_m = np.array([(np.nan_to_num(an.advantage[m][use, a]) * w).sum() / w.sum() for m in range(M)])[:, None]
                        mg_m = np.array([(np.nan_to_num(an.marginal[m][use, a]) * w).sum() / w.sum() for m in range(M)])[:, None]
                        adv, alo, ahi = pc.member_interval(adv_m, conf); mg = pc.member_interval(mg_m, conf)[0]
                        rows.append({"group": group, "context": label, "balls": bi, "strikes": ki, "prev": "none" if pi == shape.none else PITCH_TYPES[pi], "action": name,
                                     "marginal": float(mg[0]), "advantage": float(adv[0]), "adv_lo": float(alo[0]), "adv_hi": float(ahi[0]),
                                     "n": int(an.extras["n_cell"][ctx_mask & (o == 0) & (base == 0), a].sum()) if an.extras.get("n_cell") is not None else 0})
        return rows

    @staticmethod
    def _train_like(model, rows, members: int, seed: int):
        """A new model of the same kind, with the same settings and controls as ``model``, trained on ``rows`` alone with ``members`` bagged members."""

        from .outcome_models import new_model

        return new_model(model.name).fit(rows, None, {**model.settings, "bagging_reps": int(members), "seed": int(seed)}, None, classes=model.classes, reference=model)

    def _replicate(self, group, cols, model, env, shape, obs, support, n, config, cache):
        """Per year: an outcome model trained on that year alone (``replication_members`` bagged members), the same marginal and direction numbers from it, side by side.
        The usage shares and the support rule come from all the years (what pitchers did), only the outcome model is per year. Numbers only: nothing is judged to have replicated."""

        years = sorted(int(y) for y in np.unique(cols["game_year"]))
        out = []
        for y in years:
            alone = self._year_model(cache, group, cols, model, config, y, max(int(config["replication_members"]), 1))
            dyns = pb.members_dynamics(pb.model_tensor(alone, shape, group, effects="population"), obs, support, n)
            an = pb.analyze(dyns, env, shape, kinds=("observed",), trust=config["trust_region"], delta=config["marginal_shift"])
            m_rows, _ = self._marginals(group, an, config)
            for r in m_rows:
                out.append({"group": group, "year": y, "context": "ALL", "action": r["action"], "advantage": r["advantage"], "adv_lo": r["adv_lo"], "adv_hi": r["adv_hi"],
                            "marginal": r["marginal"], "m_lo": r["m_lo"], "m_hi": r["m_hi"], "n": None})
            for r in self._direction(group, an, config):
                out.append({"group": group, "year": y, "context": r["context"], "action": r["action"], "advantage": r["advantage"], "adv_lo": r["adv_lo"], "adv_hi": r["adv_hi"],
                            "marginal": r["marginal"], "m_lo": None, "m_hi": None, "n": r["n"]})
        return out

    @staticmethod
    def _table_artifact(group, an, tensor, config, env):
        return ArtifactData(f"situation_table_{group}", f"Situation table of group {group}: arrays over [state, action(, outcome)] packed as npz/zlib/base64 (state layout in extras)",
                            ("part", "payload"), tuple(pack_arrays(situation_arrays(group, an, tensor, env, config))))


def guard_checkpoint(label: str) -> None:
    from .resource_guard import guard

    guard.checkpoint(label)


NOTE_SAME_DATA = {"zh": "「同資料」那欄的數字，政策是在同一份資料上挑出來、又在同一份資料上評價，含挑選的樂觀偏差（贏家的詛咒）；「換年份交叉」是一年挑、另一年評價（每個重抽樣成員各做一次），沒有這個偏差，所以放在前面。",
                  "en": "The 'same data' numbers choose a policy on the data and judge it on the same data, so they contain the optimism of the selection (winner's curse); the cross-season numbers choose on one "
                        "season and judge on the other (once per bagged member), which does not, and come first."}


def policy_notes(config, with_pitcher_engine: bool) -> list[dict[str, str]]:
    from .pitcher_engine import ASSUMPTION_EN, ASSUMPTION_ZH

    notes = [{"control": "same_data", **NOTE_SAME_DATA}, {"control": "upper_bound", "zh": ASSUMPTION_ZH, "en": ASSUMPTION_EN}]
    if with_pitcher_engine:
        notes.append({"control": "pitcher_engine", "zh": "逐投手引擎：每位投手用自己的投手×球種效應（先事後經驗貝氏收縮，資料少的往平均拉），當天狀態設為平常、打者是聯盟平均打者；觀察到的配球與支持規則仍是聯盟的；"
                                                         f"只算至少 {int(config['pitcher_min_pa'])} 個打席的投手，每位各解一次引擎，按出場打席數加權；沒有區間。",
                      "en": "Per-pitcher engine: every pitcher with his own pitcher x pitch-type effects (shrunk after the fit by empirical Bayes), the usual day and the league-average batter; the observed mix "
                            f"and the support rule are the league's; only pitchers with at least {int(config['pitcher_min_pa'])} plate appearances, the engine solved once per pitcher and added up "
                            "weighted by plate appearances; no interval."})
    return notes


def situation_arrays(group, an, tensor, env, config) -> dict:
    """The arrays of the situation table (everything ``query_core.SituationTable`` reads), from the analysis of the bagged members."""

    import json

    conf = config["cell_confidence"]
    q, qlo, qhi = an.interval(an.q_obs, conf)
    adv, alo, ahi = an.interval(an.advantage, conf)
    mg, mlo, mhi = an.interval(an.marginal, conf)
    hr_mean, hr_lo, hr_hi = an.interval(tensor[:, :, :, pc.OC["in_play_home_run"]], conf)
    n = an.extras.get("n_cell")
    meta = {"group": group, "delta": config["marginal_shift"], "confidence": conf, "min_count": config["min_count"], "min_share": config["min_share"],
            "trust": config["trust_region"], "classes": list(pc.OUTCOMES), "members": int(an.extras["members"]), "mode": "M0 league average (level L0)"}
    return dict(q=q, q_lo=qlo, q_hi=qhi, advantage=adv, adv_lo=alo, adv_hi=ahi, marginal=mg, m_lo=mlo, m_hi=mhi, p_outcome=tensor.mean(axis=0), hr_lo=hr_lo, hr_hi=hr_hi,
                support=an.support, obs=an.obs, occupancy=an.occupancy, n=(np.zeros(an.obs.shape) if n is None else n),
                dp_trust=an.policies.get("dp_trust", an.obs), dp_unrestricted=an.policies.get("dp_unrestricted", an.obs),
                re_pa=env.re_pa, re_cnt=env.re_cnt, jbip=env.jbip, start=env.start, meta=np.array(json.dumps(meta)))


register_method(PitchPolicyMethod())
