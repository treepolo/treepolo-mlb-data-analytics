"""Research method ``policy_score``: score the pitch recommendations of one decision-engine run (chosen on one set of seasons) with a model trained on another season only.

The policies come from a ``pitch_policy`` run (the league-average ones from its situation table, the per-pitcher ones from its stored shares). The scoring season, for example 2025, took no part in
choosing them: a model with the same controls is trained on that season alone (an ``outcome_model_train`` run, ``model_run_id``), and the engine is solved on that model's outcome probabilities, keeping
the observed mix and the support rule of the run that chose the policies. Saving = value of the observed mix minus value of the policy at the start of a plate appearance, per plate appearance and
per team season; positive = fewer runs. For the per-pitcher engine every pitcher with a stored policy gets his own dynamics from the scoring model (his pitcher x pitch-type effects, merged and
shrunk by empirical Bayes on the scoring season's rows), the usual day and the league-average batter; the savings are added up weighted by the plate appearances he had in the run that chose the policies."""
from __future__ import annotations

from typing import Any

from .config_schema import ConfigError, ConfigField
from .guards import scope_years
from .league_methods import GROUPS, DIGITS, fetch_columns
from .methods import ResearchContext, ResearchMethod, ResearchResult, register_method
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
pm = _Lazy(".policy_methods")
pe = _Lazy(".pitcher_engine")
POLICIES = ("dp_trust", "dp_unrestricted")


class PolicyScoreMethod(ResearchMethod):
    kind = "policy_score"
    version = 1
    label_zh = "配球建議的評分（用另一季單獨訓練的模型）"
    label_en = "Score the pitch recommendations with a model trained on another season"
    requires_scope = True
    fields = (
        BUNT_POLICY, FILTERS, RE_SCOPE,
        ConfigField("policy_run_id", "int", default=0, minimum=0, label_zh="pitch_policy 紀錄編號（選出配球的紀錄）", label_en="pitch_policy run id (the run that chose the policies)"),
        ConfigField("model_run_id", "int", default=0, minimum=0, label_zh="評分用的 outcome_model_train 紀錄編號（只用評分季訓練）", label_en="outcome_model_train run id of the scoring model (scoring season only)"),
        ConfigField("groups", "str_list", default=list(GROUPS), label_zh="分組", label_en="Groups"),
        ConfigField("pitcher_min_pa", "int", default=1, minimum=1, label_zh="評分逐投手建議：評分季最少打席數（沒有資料的投手用聯盟平均動態）", label_en="Minimum plate appearances in the scoring season (a pitcher with none gets the league-average dynamics)"),
        ConfigField("trust_region", "float", default=0.10, minimum=0.0, maximum=1.0, label_zh="信賴區域", label_en="Trust region"),
        ConfigField("max_rows", "int", default=1_500_000, minimum=1000, label_zh="每組每球季列數上限", label_en="Row cap per group and season"),
    )

    def validate(self, config: dict[str, Any], ctx: ResearchContext) -> None:
        if not config["groups"] or set(config["groups"]) - set(GROUPS):
            raise ConfigError(f"groups must be a non-empty subset of {list(GROUPS)} / groups 必須是 {list(GROUPS)} 的非空子集")
        _check_filters(config["filters"])
        config["re_scope"] = normalize_scope(config["re_scope"], required=True)
        self._runs(ctx, config)

    @staticmethod
    def _runs(ctx: ResearchContext, config: dict[str, Any]) -> tuple[dict, dict]:
        out = []
        for key, kind in (("policy_run_id", "pitch_policy"), ("model_run_id", "outcome_model_train")):
            run = ctx.store.get_run(int(config[key])) if ctx.store is not None and config[key] else None
            if run is None or run["kind"] != kind or run["status"] != "success":
                raise ConfigError(f"{key} {config[key]} is not a successful {kind} run / {key} 不是成功的 {kind} 紀錄")
            out.append(run)
        return out[0], out[1]

    def method_inputs(self, ctx: ResearchContext, config: dict[str, Any]) -> dict[str, Any]:
        policy, model = self._runs(ctx, config)
        return {**re_method_inputs(ctx, config), "policy_run_key": policy["run_key"], "model_run_key": model["run_key"]}

    def run(self, ctx: ResearchContext, config: dict[str, Any]) -> ResearchResult:
        from .outcome_model_methods import load_model
        from .resource_guard import guard

        years = scope_years(ctx.scope)
        re = get_re_table(ctx, config["re_scope"])
        shape = pc.Shape()
        rows: list[dict[str, Any]] = []
        for group in config["groups"]:
            table_art = ctx.store.load_artifact(int(config["policy_run_id"]), f"situation_table_{group}")
            if table_art is None:
                raise ConfigError(f"the policy run has no situation table for group {group} / 配球紀錄沒有分組 {group} 的情境表")
            table = pm.unpack_arrays(table_art["rows"])
            per_art = ctx.store.load_artifact(int(config["policy_run_id"]), f"pitcher_policies_{group}")
            per = pm.unpack_arrays(per_art["rows"]) if per_art is not None else None
            model_art = ctx.store.load_artifact(int(config["model_run_id"]), f"outcome_model_{group}")
            if model_art is None:
                raise ConfigError(f"the scoring model run has no model for group {group} / 評分模型紀錄沒有分組 {group} 的模型")
            model = load_model(model_art["rows"]); model.group = group
            cols = fetch_columns(ctx, config, [group], years, re_by_state=re.re_by_state)
            env = pm.build_environment(cols, re.re_by_state, shape)
            obs, support, n = table["obs"], table["support"].astype(bool), table["n"]
            # league-average engine: the scoring model's league-average dynamics, the observed mix and support of the run that chose the policies
            tensor = pb.model_tensor(model, shape, group, effects="population")
            eng = pc.Engine(shape, pc.Dynamics(P=tensor.mean(axis=0), obs=obs, support=support, n=n), env)
            base = eng.start_value(eng.evaluate(eng.policy_observed()))
            for kind in POLICIES:
                share = np.asarray(table[kind], dtype=float)
                value = eng.start_value(eng.evaluate(share))
                rows.append({"group": group, "engine": "league", "policy": kind, "pitchers": None, "pa_share": None, "saving_pa": base - value, "saving_per_team_season": (base - value) * pm.PA_PER_TEAM_SEASON,
                             "start_observed": base, "start_policy": value})
            if per is None or not any(f"{k}_share" in per for k in POLICIES):
                continue
            # per-pitcher engine: every pitcher's own dynamics from the scoring model (merged pitcher effects, shrunk on the scoring season's rows)
            sub, _ = model._encode(cols, model.settings, fixed_classes=True)
            if pe.unavailable(model) is not None:
                continue
            beta = pe.effects_by_member(model).mean(axis=0)
            H, _ = pe.unit_information(model, sub)
            factor, _, _ = pe.shrink_factors(beta, H)
            shrunk = pe.shrunk_copy(model, factor)
            ids, pa_new, _ = pe.plate_appearances(sub)
            pa_in = dict(zip(ids.tolist(), pa_new.tolist()))
            kinds_p = [k for k in POLICIES if f"{k}_share" in per]
            seen = 0
            sav = {k: [] for k in kinds_p}; w_all = []
            for i, pid in enumerate(per["pitchers"].tolist()):
                guard.checkpoint("policy score")
                dyn = pe.pitcher_dynamics(shrunk, int(pid), shape, group, obs, support, n)
                e = pc.Engine(shape, dyn, env)
                b = e.start_value(e.evaluate(e.policy_observed()))
                w = float(per["pa"][i]); w_all.append(w)
                for kind in kinds_p:
                    s = np.asarray(per[f"{kind}_share"][i], dtype=float)
                    sav[kind].append(b - e.start_value(e.evaluate(s)))
                seen += int(pa_in.get(int(pid), 0) >= int(config["pitcher_min_pa"]))
            w_arr = np.array(w_all)
            for kind in kinds_p:
                v = np.array(sav[kind])
                mean = float(np.average(v, weights=w_arr))
                rows.append({"group": group, "engine": "pitcher", "policy": kind, "pitchers": int(len(v)), "pa_share": None, "saving_pa": mean, "saving_per_team_season": mean * pm.PA_PER_TEAM_SEASON,
                             "start_observed": None, "start_policy": None})
            rows.append({"group": group, "engine": "pitcher (note)", "policy": f"{seen} of {len(w_all)} pitchers had plate appearances in the scoring season", "pitchers": int(len(w_all)), "pa_share": None,
                         "saving_pa": None, "saving_per_team_season": None, "start_observed": None, "start_policy": None})
        sections = (make_section("Score 評分（用另一季單獨訓練的模型；每打席與每隊每季少失分，正＝比實際配球少失分；沒有區間）",
                                 ("group", "engine", "policy", "pitchers", "saving_pa", "saving_per_team_season", "start_observed", "start_policy"), rows, ("group", "engine", "policy"), "duckdb", DIGITS),)
        notes = [{"control": "score", "zh": "評分用的模型只用評分那一季訓練，那一季沒有參與選配球；觀察到的配球與支持規則沿用選出配球的那一份。引擎假設打者不會因配球改變而調整，所以少失分是上限。",
                  "en": "The scoring model was trained on the scoring season only, which took no part in choosing the policies; the observed mix and the support rule are those of the run that chose them. The engine "
                        "assumes batters do not adjust, so the savings are an upper bound."}]
        return ResearchResult(sections=sections, extras={"scope": ctx.scope, "policy_run_id": config["policy_run_id"], "model_run_id": config["model_run_id"], "notes": notes}, artifacts=())


register_method(PolicyScoreMethod())
