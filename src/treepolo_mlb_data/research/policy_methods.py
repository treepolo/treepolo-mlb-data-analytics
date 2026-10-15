"""Research method ``pitch_policy`` (P6 plan A.5): decision model, policies, situation table, marginal values and the margin, league-average query mode M0.

6.0b scope: the outcome model is the level-L0 boosted-tree model (situation and pitch type only, no player descriptors), fitted inside the method on the
whole scope with game-level bootstrap members; ``J_BIP`` is the descriptive run value of balls in play (the fixed-effects version comes with
``action_effects``). Player-specific queries (M1-M3) are the job of ``pitch_query``.
"""
from __future__ import annotations

import base64
import io
import zlib
from typing import Any

from .config_schema import ConfigError, ConfigField
from .guards import check_modeling_scope, check_purpose, scope_years
from .league_methods import GROUPS, DIGITS, LeagueModelMethod, fetch_columns
from .methods import ArtifactData, ResearchContext, ResearchMethod, ResearchResult, register_method
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
pc = _Lazy(".policy_core")
pb = _Lazy(".policy_build")
lc = _Lazy(".league_core")
gbm = _Lazy(".gbm")
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
    version = 1
    label_zh = "配球決策引擎（動態規劃、情境表、餘裕）"
    label_en = "Pitch policy engine (dynamic programming, situation table, margin)"
    requires_scope = True
    fields = (
        PURPOSE, BUNT_POLICY, FILTERS, RE_SCOPE,
        ConfigField("confidence", "float", default=0.95, minimum=0.5, maximum=0.999, label_zh="整體數字的信賴水準", label_en="Confidence level of the overall numbers"),
        ConfigField("cell_confidence", "float", default=0.99, minimum=0.5, maximum=0.999, label_zh="情境格的信賴水準", label_en="Confidence level of the situation cells"),
        ConfigField("groups", "str_list", default=list(GROUPS), label_zh="分組（各自求解，不合併）", label_en="Groups (solved separately)"),
        ConfigField("policies", "str_list", default=list(POLICY_NAMES), label_zh="政策", label_en="Policies"),
        ConfigField("trust_region", "float", default=0.10, minimum=0.0, maximum=1.0, label_zh="信賴區域（份額最多移動）", label_en="Trust region (largest share move)"),
        ConfigField("marginal_shift", "float", default=0.10, minimum=0.0, maximum=0.5, label_zh="邊際價值的移動量", label_en="Share moved for the marginal value"),
        ConfigField("lambda_grid", "json", default=[0.0, 0.5, 1.0, 2.0], label_zh="全壘打懲罰 λ 網格", label_en="Home-run penalty grid"),
        ConfigField("tau", "float", default=0.005, minimum=0.0, label_zh="實務大小 τ（分／球）", label_en="Practical size tau (runs per pitch)"),
        ConfigField("members", "int", default=30, minimum=2, maximum=200, label_zh="袋裝成員數", label_en="Bagged members"),
        ConfigField("cross_eval", "bool", default=True, label_zh="交叉評估（一季選、另一季評）", label_en="Cross evaluation (choose on one season, judge on the other)"),
        ConfigField("cross_members", "int", default=5, minimum=1, maximum=50, label_zh="交叉評估每季成員數", label_en="Members per season for the cross evaluation"),
        ConfigField("backend", "choice", default="hgb", choices=("xgboost", "hgb"), label_zh="訓練後端", label_en="Backend"),
        ConfigField("device", "choice", default="auto", choices=("auto", "cpu", "cuda"), label_zh="裝置", label_en="Device"),
        ConfigField("max_depth", "int", default=4, minimum=1, maximum=30, label_zh="樹深", label_en="Tree depth"),
        ConfigField("min_leaf", "int", default=1000, minimum=1, label_zh="葉節點最小樣本", label_en="Minimum samples per leaf"),
        ConfigField("learning_rate", "float", default=0.1, minimum=1e-4, maximum=1.0, label_zh="學習率", label_en="Learning rate"),
        ConfigField("l2", "float", default=1.0, minimum=0.0, label_zh="L2 正則化", label_en="L2 regularisation"),
        ConfigField("max_iter", "int", default=150, minimum=1, label_zh="棵數", label_en="Boosting rounds"),
        ConfigField("early_stopping_rounds", "int", default=20, minimum=1, label_zh="早停輪數（xgboost）", label_en="Early-stopping rounds (xgboost)"),
        ConfigField("min_share", "float", default=0.05, minimum=0.0, maximum=1.0, label_zh="支持規則：最小份額", label_en="Support rule: minimum share"),
        ConfigField("min_count", "int", default=300, minimum=1, label_zh="支持規則：最小球數", label_en="Support rule: minimum pitches"),
        ConfigField("class_merge", "json", default=None, label_zh="結果類別對照（預設同 P5）", label_en="Outcome class map (default as P5)"),
        ConfigField("min_class_count", "int", default=50, minimum=1, label_zh="類別最小樣本", label_en="Minimum rows per class"),
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
        if config["class_merge"] is None:
            from . import modeling as md

            config["class_merge"] = dict(md.DEFAULT_CLASS_MERGE)
        _check_filters(config["filters"])
        check_purpose(ctx.scope, config["purpose"])
        config["re_scope"] = normalize_scope(config["re_scope"], required=True)
        check_modeling_scope(config["re_scope"], config["purpose"], "re_scope")
        from . import gbm as gbm_module

        try:
            gbm_module.resolve_device(config["backend"], config["device"])
        except ValueError as exc:
            raise ConfigError(str(exc)) from exc
        if config["anchor_run_id"]:
            self._anchor_run(ctx, config)

    @staticmethod
    def _anchor_run(ctx: ResearchContext, config: dict[str, Any]) -> dict[str, Any]:
        run = ctx.store.get_run(int(config["anchor_run_id"])) if ctx.store is not None else None
        if run is None or run["kind"] != "action_effects" or run["status"] != "success":
            raise ConfigError(f"anchor_run_id {config['anchor_run_id']} is not a successful action_effects run / anchor_run_id 不是成功的 action_effects 紀錄")
        return run

    def method_inputs(self, ctx: ResearchContext, config: dict[str, Any]) -> dict[str, Any]:
        out = re_method_inputs(ctx, config)
        if config["anchor_run_id"]:
            out = {**out, "anchor_run_key": self._anchor_run(ctx, config)["run_key"]}
        return out

    # ------------------------------------------------------------------------------------------------ run
    def run(self, ctx: ResearchContext, config: dict[str, Any]) -> ResearchResult:
        from . import gbm as gbm_module

        years = scope_years(ctx.scope)
        device = gbm_module.resolve_device(config["backend"], config["device"])
        re = get_re_table(ctx, config["re_scope"])
        cols_all = fetch_columns(ctx, config, config["groups"], years, re_by_state=re.re_by_state)
        shape = pc.Shape()
        r1_rows, value_rows, risk_rows, marg_rows, support_rows, artifacts = [], [], [], [], [], []
        direction_rows, anchor_rows = [], []
        for group in config["groups"]:
            sel = np.nonzero(cols_all["group"] == group)[0]
            if len(sel) == 0:
                raise ConfigError(f"Group {group} has no rows / 分組 {group} 沒有資料")
            cols = lc.take(cols_all, sel)
            LeagueModelMethod._check_classes(cols, {**config, "target": "outcome"}, group)
            env = build_environment(cols, re.re_by_state, shape)
            anchor = self._load_anchor(ctx, config, group)
            if anchor is not None:
                env.jbip = np.where(np.isfinite(anchor["jbip"]) & (anchor["jbip"] != 0), anchor["jbip"], env.jbip)
            params = dict(max_depth=config["max_depth"], min_leaf=config["min_leaf"], learning_rate=config["learning_rate"], l2=config["l2"],
                          max_iter=config["max_iter"], early_stopping_rounds=config["early_stopping_rounds"])
            members, classes = pb.fit_outcome_members(cols, backend=config["backend"], device=device, params=params, reps=config["members"], seed=config["seed"],
                                                      merge=config["class_merge"])
            tensor = pb.outcome_tensor(members, classes, shape, group)
            obs, support, n = pb.usage_from_counts(cols, shape, min_share=config["min_share"], min_count=config["min_count"])
            dyns = pb.members_dynamics(tensor, obs, support, n)
            kinds = tuple(k for k in pc.POLICIES if k in config["policies"])
            an = pb.analyze(dyns, env, shape, kinds=kinds, trust=config["trust_region"], delta=config["marginal_shift"])
            r1_rows += self._r1(group, cols, an, env, shape)
            value_rows += self._values(group, an, config)
            if config["cross_eval"] and len(set(int(y) for y in np.unique(cols["game_year"]))) >= 2:
                value_rows += self._cross(group, cols, classes, env, shape, obs, support, n, params, config, device, kinds)
            risk_rows += self._risk(group, dyns, env, shape, config)
            m_rows, s_rows = self._marginals(group, an, config)
            marg_rows += m_rows; support_rows += s_rows
            direction_rows += self._direction(group, an, config)
            if anchor is not None:
                anchor_rows += self._anchor_rows(group, an, tensor, anchor, shape)
            if config["save_table"]:
                artifacts.append(self._table_artifact(group, an, tensor, config))
        extras = {"scope": ctx.scope, "purpose": config["purpose"], "re_scope": config["re_scope"], "classes": list(pc.OUTCOMES), "backend": config["backend"], "device": device,
                  "state_layout": "balls,strikes,outs,bases,prev(9 types + none),streak(1,2,3+)", "mode": "M0 league average (level L0)"}
        sections = (
            make_section("R1 對照（觀察到的配球重現得分期望值）", ("group", "item", "n_states", "median_abs_diff", "max_abs_diff", "value"), r1_rows, ("group", "item"), "duckdb", DIGITS),
            make_section("Policy value 政策價值（每打席）", ("group", "policy", "kind", "start_value", "saving_pa", "lo", "hi", "saving_per_pitch", "saving_per_team_season", "hr_probability",
                                                              "pitches_per_pa"), value_rows, ("group", "policy", "kind"), "duckdb", DIGITS),
            make_section("Risk frontier 取捨前緣（λ 對全壘打）", ("group", "lambda", "expected_runs", "lo", "hi", "hr_probability", "hr_lo", "hr_hi", "saving_vs_observed"), risk_rows,
                         ("group", "lambda"), "duckdb", DIGITS),
            make_section("Marginal value 邊際價值摘要（依觀察到的出現頻率加權）", ("group", "action", "advantage", "adv_lo", "adv_hi", "marginal", "m_lo", "m_hi", "detected_share", "supported_share"),
                         marg_rows, ("group", "action"), "duckdb", DIGITS),
            make_section("Direction map 方向圖（球數×前一球 × 球種；邊際價值）", ("group", "context", "balls", "strikes", "prev", "action", "marginal", "advantage", "adv_lo", "adv_hi", "n",
                                                                         "not_detected"), direction_rows, ("group", "context", "action"), "duckdb", DIGITS),
            make_section("Support 支持表", ("group", "action", "supported_share", "observed_share"), support_rows, ("group", "action"), "duckdb", DIGITS),
        )
        if anchor_rows:
            sections = sections + (make_section("Anchor 錨點一致性（計畫 3.6）", ("group", "design", "anchor_cells", "same_sign_share", "median_ratio", "model_only_cells", "model_large_cells", "passes"),
                                                anchor_rows, ("group", "design"), "duckdb", DIGITS),)
            extras["anchor_run_id"] = config["anchor_run_id"]
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

    @staticmethod
    def _cross(group, cols, classes, env, shape, obs, support, n, params, config, device, kinds):
        """Winner's-curse-free saving (plan 4.3): choose the policy on the dynamics fitted to one season, judge it on the other, both directions."""

        years = sorted(int(y) for y in np.unique(cols["game_year"]))
        sets = []
        for y in (years[0], years[-1]):
            sub = lc.take(cols, np.nonzero(np.asarray(cols["game_year"]) == y)[0])
            mem, cls = pb.fit_outcome_members(sub, backend=config["backend"], device=device, params=params, reps=max(config["cross_members"], 2), seed=config["seed"] + y,
                                              merge=config["class_merge"])
            sets.append(pb.members_dynamics(pb.outcome_tensor(mem, cls, shape, group), obs, support, n))
        rows = []
        for kind in kinds:
            if kind in ("observed",):
                continue
            v = pb.cross_saving(sets[0], sets[1], env, shape, kind, config["trust_region"])
            rows.append({"group": group, "policy": kind, "kind": f"cross {years[0]}/{years[-1]}", "start_value": float("nan"), "saving_pa": v, "lo": float("nan"), "hi": float("nan"),
                         "saving_per_pitch": float("nan"), "saving_per_team_season": v * PA_PER_TEAM_SEASON, "hr_probability": float("nan"), "pitches_per_pa": float("nan")})
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
        """Per action: the advantage over the observed mix of the others and the marginal value, weighted by how often each state occurs; share of states flagged detected."""

        shape = an.shape
        occ = an.occupancy; valid = occ > 0
        m_rows, s_rows = [], []
        conf = config["cell_confidence"]
        mean_adv, lo_adv, hi_adv = an.interval(an.advantage, conf)
        mean_m, lo_m, hi_m = an.interval(an.marginal, conf)
        det = pc.detect(mean_adv, lo_adv, hi_adv, an.support, config["tau"])
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
                           "m_hi": float(mhi[0]), "detected_share": float((det[use, a] * w).sum() / w.sum()), "supported_share": float(w.sum() / occ[valid].sum())})
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
        rows = []
        for key in sorted(k for k in anchor if k.startswith("eff_")):
            design = key[4:]
            anchor_rows = []
            for ph, pname in enumerate(anc.PHASES):
                for t in types:
                    ai = pc_types().index(t)
                    for c, cname in enumerate(pc.OUTCOMES):
                        anchor_rows.append({"phase": pname, "action": t, "class": cname, "effect": float(anchor[key][ph, ai, c]), "se": float(anchor[f"se_{design}"][ph, ai, c])})
            res = anc.anchor_consistency(model, anchor_rows)
            rows.append({"group": group, "design": design, **{k: res[k] for k in ("anchor_cells", "same_sign_share", "median_ratio", "model_only_cells", "model_large_cells")}, "passes": int(res["passes"])})
        return rows

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
                        found = bool(pc.detect(adv, alo, ahi, np.array([True]), config["tau"])[0])
                        rows.append({"group": group, "context": label, "balls": bi, "strikes": ki, "prev": "none" if pi == shape.none else PITCH_TYPES[pi], "action": name,
                                     "marginal": float(mg[0]), "advantage": float(adv[0]), "adv_lo": float(alo[0]), "adv_hi": float(ahi[0]),
                                     "n": int(an.extras["n_cell"][ctx_mask & (o == 0) & (base == 0), a].sum()) if an.extras.get("n_cell") is not None else 0,
                                     "not_detected": int(not found)})
        return rows

    @staticmethod
    def _table_artifact(group, an, tensor, config):
        conf = config["cell_confidence"]
        q, qlo, qhi = an.interval(an.q_obs, conf)
        adv, alo, ahi = an.interval(an.advantage, conf)
        mg, mlo, mhi = an.interval(an.marginal, conf)
        arrays = dict(q=q.astype(np.float32), q_lo=qlo.astype(np.float32), q_hi=qhi.astype(np.float32), advantage=adv.astype(np.float32), adv_lo=alo.astype(np.float32),
                      adv_hi=ahi.astype(np.float32), marginal=mg.astype(np.float32), m_lo=mlo.astype(np.float32), m_hi=mhi.astype(np.float32),
                      p_outcome=tensor.mean(axis=0).astype(np.float32), support=an.support, obs=an.obs.astype(np.float32), occupancy=an.occupancy.astype(np.float32),
                      dp_trust=an.policies.get("dp_trust", an.obs).astype(np.float32), dp_unrestricted=an.policies.get("dp_unrestricted", an.obs).astype(np.float32))
        return ArtifactData(f"situation_table_{group}", f"Situation table of group {group}: arrays over [state, action(, outcome)] packed as npz/zlib/base64 (state layout in extras)",
                            ("part", "payload"), tuple(pack_arrays(arrays)))


register_method(PitchPolicyMethod())
