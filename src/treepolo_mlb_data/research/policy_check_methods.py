"""Research method ``policy_check``: a direct check of the decision engine's recommendations on the actual pitches of another season, with no model at all.

Every pitch of the checked season (for example the 2025 regular season, which took no part in choosing the policies) is put in its state (count, outs, bases, previous pitch, streak). The
recommendation of a policy in that state is the pitch type with the largest share of the policy there (``dp_trust`` or ``dp_unrestricted``; the league-average engine, or the per-pitcher
engine for the pitchers it was solved for). A pitch whose type is the recommendation is 'followed', any other is 'not followed'. The actual run value of the pitch (the batting team's
run expectancy change on that pitch, times 100: runs per 100 pitches; lower is better for the pitcher) is averaged for the two groups, with the difference and its 95% interval (clustered by
game), for all counts together and for each of the 12 counts. Nothing is adjusted for the state or the batter, so this is a plain comparison of what happened, not a causal estimate."""
from __future__ import annotations

from typing import Any

from .config_schema import ConfigError, ConfigField
from .guards import scope_years
from .league_methods import DIGITS, GROUPS, fetch_columns
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
lc = _Lazy(".league_core")

POLICIES = ("dp_trust", "dp_unrestricted")
COUNTS = ["all"] + [f"{b}-{k}" for b in range(4) for k in range(3)]


def within_state(value, followed, state, game, confidence: float = 0.95) -> dict[str, Any]:
    """The same comparison inside the same situation: ``value ~ followed | situation`` (pyfixest, the situation as a fixed effect, standard errors clustered by ``game``). Only the situations in which
    pitches were followed AND not followed count (the others say nothing about the difference); ``share_used`` is the share of the given pitches that are in such situations. The plain difference
    on those same pitches (``diff_plain``, nothing adjusted) is given next to the coefficient, so the effect of comparing inside the situation can be seen."""

    value = np.asarray(value, dtype=float); followed = np.asarray(followed, dtype=bool); state = np.asarray(state, dtype=np.int64); game = np.asarray(game)
    out: dict[str, Any] = {"n_used": 0, "share_used": 0.0, "states_used": 0, "states_total": int(len(np.unique(state))), "n_followed_used": 0, "n_not_followed_used": 0, "coef": None, "lo": None, "hi": None, "diff_plain": None}
    if len(value) == 0:
        return out
    codes = np.unique(state, return_inverse=True)[1]
    n_all = np.bincount(codes); n_fol = np.bincount(codes, weights=followed.astype(float))
    both = (n_fol > 0) & (n_fol < n_all)
    keep = both[codes]
    out.update(n_used=int(keep.sum()), share_used=float(keep.mean()), states_used=int(both.sum()), n_followed_used=int(followed[keep].sum()), n_not_followed_used=int((~followed[keep]).sum()))
    if out["n_followed_used"] < 2 or out["n_not_followed_used"] < 2 or len(np.unique(game[keep])) < 2:
        return out
    import pandas as pd
    import pyfixest as pf

    df = pd.DataFrame({"y": value[keep], "followed": followed[keep].astype(float), "state": state[keep], "game": game[keep]})
    fit = pf.feols("y ~ followed | state", data=df, vcov={"CRV1": "game"})
    tidy = fit.confint(alpha=1.0 - float(confidence)).loc["followed"]
    out.update(coef=float(fit.coef()["followed"]), lo=float(tidy.iloc[0]), hi=float(tidy.iloc[1]))
    out["diff_plain"] = float(value[keep][followed[keep]].mean() - value[keep][~followed[keep]].mean())
    return out


def compare(value, followed, game, z: float) -> dict[str, Any]:
    """Mean of ``value`` for followed and not followed pitches, the difference (followed minus not followed) and its interval, clustered by ``game``."""

    value = np.asarray(value, dtype=float); followed = np.asarray(followed, dtype=bool)
    n1, n0 = int(followed.sum()), int((~followed).sum())
    out: dict[str, Any] = {"n_followed": n1, "n_not_followed": n0, "share_followed": n1 / max(n1 + n0, 1), "mean_followed": None, "mean_not_followed": None, "diff": None, "lo": None, "hi": None}
    if n1:
        out["mean_followed"] = float(value[followed].mean())
    if n0:
        out["mean_not_followed"] = float(value[~followed].mean())
    if n1 < 2 or n0 < 2:
        return out
    codes = np.unique(np.asarray(game), return_inverse=True)[1]
    G = int(codes.max()) + 1
    X = np.column_stack([np.ones(len(value)), followed.astype(float)])
    bread = np.linalg.inv(X.T @ X)
    beta = bread @ (X.T @ value)
    resid = value - X @ beta
    scores = np.stack([np.bincount(codes, weights=X[:, j] * resid, minlength=G) for j in range(2)], axis=1)
    cov = bread @ (scores.T @ scores) @ bread * (G / max(G - 1, 1)) * ((len(value) - 1) / max(len(value) - 2, 1))
    se = float(np.sqrt(max(cov[1, 1], 0.0)))
    out["diff"] = float(beta[1]); out["lo"] = float(beta[1] - z * se); out["hi"] = float(beta[1] + z * se)
    return out


class PolicyCheckMethod(ResearchMethod):
    kind = "policy_check"
    version = 3     # 3: a within-situation version of the check (situation fixed effects, pyfixest). 2: also checks the league-average engine solved for every pitcher
    label_zh = "配球建議的直接檢查（用另一季的實際投球，不用任何模型）"
    label_en = "Direct check of the pitch recommendations on another season (actual pitches, no model)"
    requires_scope = True
    fields = (
        BUNT_POLICY, FILTERS, RE_SCOPE,
        ConfigField("policy_run_id", "int", default=0, minimum=0, label_zh="pitch_policy 紀錄編號（配球建議）", label_en="pitch_policy run id (the recommendations)",
                    help_zh="需要有情境表（聯盟平均建議）；有逐投手引擎的紀錄才會檢查逐投手建議。", help_en="Needs the situation tables; the per-pitcher recommendations are checked when the run has them."),
        ConfigField("groups", "str_list", default=list(GROUPS), label_zh="分組", label_en="Groups"),
        ConfigField("confidence", "float", default=0.95, minimum=0.5, maximum=0.999, label_zh="信賴水準", label_en="Confidence level"),
        ConfigField("max_rows", "int", default=1_500_000, minimum=1000, label_zh="每組每球季列數上限", label_en="Row cap per group and season"),
    )

    def validate(self, config: dict[str, Any], ctx: ResearchContext) -> None:
        if not config["groups"] or set(config["groups"]) - set(GROUPS):
            raise ConfigError(f"groups must be a non-empty subset of {list(GROUPS)} / groups 必須是 {list(GROUPS)} 的非空子集")
        _check_filters(config["filters"])
        config["re_scope"] = normalize_scope(config["re_scope"], required=True)
        self._policy_run(ctx, config)

    @staticmethod
    def _policy_run(ctx: ResearchContext, config: dict[str, Any]) -> dict[str, Any]:
        run = ctx.store.get_run(int(config["policy_run_id"])) if ctx.store is not None and config["policy_run_id"] else None
        if run is None or run["kind"] != "pitch_policy" or run["status"] != "success":
            raise ConfigError(f"policy_run_id {config['policy_run_id']} is not a successful pitch_policy run / policy_run_id 不是成功的 pitch_policy 紀錄")
        return run

    def method_inputs(self, ctx: ResearchContext, config: dict[str, Any]) -> dict[str, Any]:
        return {**re_method_inputs(ctx, config), "policy_run_key": self._policy_run(ctx, config)["run_key"]}

    def run(self, ctx: ResearchContext, config: dict[str, Any]) -> ResearchResult:
        from scipy import stats

        z = float(stats.norm.ppf(0.5 + float(config["confidence"]) / 2.0))
        years = scope_years(ctx.scope)
        re = get_re_table(ctx, config["re_scope"])
        shape = pc.Shape()
        rows: list[dict[str, Any]] = []
        within_rows: list[dict[str, Any]] = []
        coverage: list[dict[str, Any]] = []
        for group in config["groups"]:
            art = ctx.store.load_artifact(int(config["policy_run_id"]), f"situation_table_{group}")
            if art is None:
                raise ConfigError(f"the policy run has no situation table for group {group} / 配球紀錄沒有分組 {group} 的情境表")
            table = pm.unpack_arrays(art["rows"])
            per = ctx.store.load_artifact(int(config["policy_run_id"]), f"pitcher_policies_{group}")
            pitcher_policies = pm.unpack_arrays(per["rows"]) if per is not None else None
            cols = fetch_columns(ctx, config, [group], years, re_by_state=re.re_by_state)
            A = shape.n_actions
            ptype = np.asarray(cols["pitch_type"]).astype(str)
            action = np.array([pb.PITCH_TYPES.index(t) if t in pb.PITCH_TYPES else -1 for t in ptype])
            prev = np.array([pb.PITCH_TYPES.index(t) if t in pb.PITCH_TYPES else A for t in np.asarray(cols["prev1_pitch_type"]).astype(str)])
            b = np.asarray(cols["balls"]).astype(int); k = np.asarray(cols["strikes"]).astype(int)
            o = np.asarray(cols["outs_when_up"]).astype(int); base = np.asarray(cols["bases"]).astype(int)
            value = np.asarray(cols["pitch_value"], dtype=float) * 100.0
            ok = (action >= 0) & (b < shape.n_balls) & (k < shape.n_strikes) & (o < 3) & np.isfinite(value)
            idx = np.nonzero(ok)[0]
            state = shape.index(b[idx], k[idx], o[idx], base[idx], prev[idx], pb.state_streak(cols["prior_streak"])[idx])
            act, val, game = action[idx], value[idx], np.asarray(cols["game_pk"])[idx]
            count = np.array([f"{x}-{y}" for x, y in zip(b[idx], k[idx])])
            pitcher = np.asarray(cols["pitcher"])[idx].astype(np.int64)
            coverage.append({"group": group, "pitches": int(len(idx)), "years": ",".join(str(y) for y in years)})
            for policy in POLICIES:
                league_rec = np.argmax(table[policy], axis=1)[state]
                variants = [("league", np.ones(len(idx), dtype=bool), league_rec)]
                if pitcher_policies is not None and policy in pitcher_policies:
                    row_of = {int(p): i for i, p in enumerate(pitcher_policies["pitchers"])}
                    known = np.array([int(p) in row_of for p in pitcher])
                    rec = np.zeros(len(idx), dtype=int)
                    rec[known] = pitcher_policies[policy][[row_of[int(p)] for p in pitcher[known]], state[known]]
                    variants.append(("pitcher", known, rec))
                lp = ctx.store.load_artifact(int(config["policy_run_id"]), f"league_pitcher_policies_{group}")
                if lp is not None:
                    lpp = pm.unpack_arrays(lp["rows"])
                    if policy in lpp:
                        row_l = {int(p): i for i, p in enumerate(lpp["pitchers"])}
                        known_l = np.array([int(p) in row_l for p in pitcher])
                        rec_l = league_rec.copy()                                                                   # a pitcher with no training pitches keeps the league recommendation
                        rec_l[known_l] = lpp[policy][[row_l[int(p)] for p in pitcher[known_l]], state[known_l]]
                        variants.append(("league_by_pitcher (pitchers with training pitches)", known_l, rec_l))
                        variants.append(("league (same pitches)", known_l, league_rec))
                        variants.append(("league_by_pitcher (all pitches; pitchers without training pitches keep the league)", np.ones(len(idx), dtype=bool), rec_l))
                        if policy == POLICIES[0]:
                            coverage.append({"group": group, "pitches": int((~known_l).sum()), "years": "pitches of pitchers with no training pitches (" + ",".join(str(y) for y in years) + ")"})
                for engine, use, rec in variants:
                    followed = act == rec
                    for cell in COUNTS:
                        m = use if cell == "all" else use & (count == cell)
                        if not m.any():
                            continue
                        rows.append({"group": group, "engine": engine, "policy": policy, "count": cell, **compare(val[m], followed[m], game[m], z)})
                    within_rows.append({"group": group, "engine": engine, "policy": policy, "pitches_of_engine": int(use.sum()), "pitches_all": int(len(idx)), **within_state(val[use], followed[use], state[use], game[use], float(config["confidence"]))})
        columns = ("group", "engine", "policy", "count", "n_followed", "n_not_followed", "share_followed", "mean_followed", "mean_not_followed", "diff", "lo", "hi")
        sections = (
            make_section("Direct check 直接檢查（實際投球的每球失分變化，每 100 球幾分；照建議投 vs 沒照建議投；diff＝照建議減沒照建議，區間按比賽分群；不用任何模型，也沒有調整球數以外的任何條件）",
                         columns, rows, ("group", "engine", "policy", "count"), "duckdb", DIGITS),
            make_section("Within situation 同一情況內比較（每 100 球失分對「照建議」迴歸，情況＝好壞球數、出局、壘上、上一球、連投顆數，當固定效果；標準誤按比賽分群；只用照建議與沒照建議都有的情況；coef＝照建議減沒照建議，負＝照建議較好；share_of_all＝用到的投球占這一季全部投球的比例）",
                         ("group", "engine", "policy", "pitches_of_engine", "n_used", "share_of_engine", "share_of_all", "states_used", "states_total", "n_followed_used", "n_not_followed_used", "coef", "lo", "hi", "diff_plain"),
                         [{**r, "share_of_engine": r["n_used"] / max(r["pitches_of_engine"], 1), "share_of_all": r["n_used"] / max(r["pitches_all"], 1)} for r in within_rows], ("group", "engine", "policy"), "duckdb", DIGITS),
            make_section("Coverage 檢查的投球數", ("group", "pitches", "years"), coverage, ("group",), "duckdb", DIGITS),
        )
        notes = [{"control": "direct_check", "zh": "照建議投＝實際投的球種是該狀態（球數、出局、壘包、前一球、連投）下配球建議裡份額最大的球種；沒有用任何模型，只是把實際發生的事分成兩組比較，沒有調整球數以外的條件。",
                  "en": "Followed = the pitch type thrown is the type with the largest share of the policy in that state; no model is used and nothing is adjusted except by listing every count."},
                 {"control": "upper_bound", "zh": "引擎假設打者不會因配球改變而調整，所以引擎算出的少失分是上限。", "en": "The engine assumes batters do not adjust to a change in pitch selection, so the savings it computes are an upper bound."}]
        return ResearchResult(sections=sections, extras={"scope": ctx.scope, "policy_run_id": config["policy_run_id"], "policies": list(POLICIES), "notes": notes}, artifacts=())


register_method(PolicyCheckMethod())
