"""Research method ``param_search``: random search of the boosted-tree settings with cross-validation over players (plan 3.4, appendix B).

Every trial is scored on players the model has not seen: K folds over pitchers and K folds over batters, level L3 (both players' descriptors), mean log-loss over groups,
folds and the two sides. The best trial's settings are what the following steps use."""
from __future__ import annotations

import itertools
import time
from typing import Any

from .config_schema import ConfigError, ConfigField
from .guards import check_purpose
from .lazy import Lazy
from .league_methods import DIGITS, GROUPS, fetch_columns
from .methods import ResearchContext, ResearchMethod, ResearchResult, register_method
from .sections import make_section
from .sequencing_methods import BUNT_POLICY, FILTERS, PURPOSE, _check_filters

np = Lazy("numpy")
lc = Lazy(".league_core")
md = Lazy(".modeling")
gbm = Lazy(".gbm")
profiles = Lazy(".profiles")
DEFAULT_GRID = {"max_depth": [3, 5, 8], "min_leaf": [200, 1000], "learning_rate": [0.05, 0.1], "l2": [1.0, 10.0]}


def sample_trials(grid: dict[str, list], n: int, seed: int) -> list[dict[str, Any]]:
    """``n`` different settings from the grid (all of them when the grid is smaller), always in the same order for the same seed."""

    import random

    names = sorted(grid)
    combos = [dict(zip(names, values)) for values in itertools.product(*(grid[k] for k in names))]
    random.Random(seed).shuffle(combos)
    return combos[: max(1, min(n, len(combos)))]


class ParamSearchMethod(ResearchMethod):
    kind = "param_search"
    version = 1
    label_zh = "模型參數搜尋（依球員分折的交叉驗證）"
    label_en = "Model setting search (cross-validation over players)"
    requires_scope = True
    fields = (
        PURPOSE, BUNT_POLICY, FILTERS,
        ConfigField("groups", "str_list", default=list(GROUPS), label_zh="分組（每個設定在所有分組上一起評分）", label_en="Groups (every setting is scored on all of them)"),
        ConfigField("trials", "int", default=8, minimum=1, maximum=100, label_zh="試幾組設定", label_en="Settings to try"),
        ConfigField("folds", "int", default=5, minimum=2, maximum=10, label_zh="依球員分折數", label_en="Folds over players"),
        ConfigField("grid", "json", default=DEFAULT_GRID, label_zh="設定格子", label_en="Grid", help_zh="max_depth、min_leaf、learning_rate、l2 各自的候選值。", help_en="Candidate values for each setting."),
        ConfigField("max_iter", "int", default=300, minimum=1, label_zh="最多棵數（xgboost 早停）", label_en="Maximum boosting rounds (early stopping with xgboost)"),
        ConfigField("early_stopping_rounds", "int", default=20, minimum=1, label_zh="早停輪數", label_en="Early-stopping rounds"),
        ConfigField("tune_rows", "int", default=400_000, minimum=1000, label_zh="每組搜尋用的列數上限（整場比賽抽樣）", label_en="Row cap per group while searching (whole games sampled)"),
        ConfigField("backend", "choice", default="hgb", choices=("xgboost", "hgb"), label_zh="訓練後端", label_en="Backend"),
        ConfigField("device", "choice", default="auto", choices=("auto", "cpu", "cuda"), label_zh="裝置", label_en="Device"),
        ConfigField("class_merge", "json", default=None, label_zh="結果類別對照（預設同 P5）", label_en="Outcome class map (default as P5)"),
        ConfigField("min_class_count", "int", default=50, minimum=1, label_zh="類別最小樣本", label_en="Minimum rows per class"),
        ConfigField("seed", "int", default=20240601, label_zh="隨機種子", label_en="Random seed"),
        ConfigField("max_rows", "int", default=1_500_000, minimum=1000, label_zh="每組每球季列數上限", label_en="Row cap per group and season"),
    )

    def validate(self, config: dict[str, Any], ctx: ResearchContext) -> None:
        if not config["groups"] or set(config["groups"]) - set(GROUPS):
            raise ConfigError(f"groups must be a non-empty subset of {list(GROUPS)} / groups 必須是 {list(GROUPS)} 的非空子集")
        grid = config["grid"]
        if not isinstance(grid, dict) or set(grid) - set(DEFAULT_GRID) or not grid or not all(isinstance(v, list) and v for v in grid.values()):
            raise ConfigError(f"grid must map {sorted(DEFAULT_GRID)} to non-empty lists / grid 必須把 {sorted(DEFAULT_GRID)} 對到非空列表")
        if config["class_merge"] is None:
            from . import modeling as md_module

            config["class_merge"] = dict(md_module.DEFAULT_CLASS_MERGE)
        _check_filters(config["filters"])
        check_purpose(ctx.scope, config["purpose"])
        from . import gbm as gbm_module

        try:
            gbm_module.resolve_device(config["backend"], config["device"])
        except ValueError as exc:
            raise ConfigError(str(exc)) from exc

    def run(self, ctx: ResearchContext, config: dict[str, Any]) -> ResearchResult:
        from .guards import scope_years

        device = gbm.resolve_device(config["backend"], config["device"])
        cols_all = fetch_columns(ctx, config, config["groups"], scope_years(ctx.scope))
        trials = sample_trials(config["grid"], config["trials"], config["seed"])
        base = dict(max_iter=config["max_iter"], early_stopping_rounds=config["early_stopping_rounds"])
        per_group = {}
        for group in config["groups"]:
            sel = np.nonzero(cols_all["group"] == group)[0]
            if len(sel) == 0:
                raise ConfigError(f"Group {group} has no rows / 分組 {group} 沒有資料")
            per_group[group] = self._prepare(lc.take(cols_all, sel), config)
        trial_rows, totals = [], [[] for _ in trials]
        for ti, params in enumerate(trials):
            for group, prep in per_group.items():
                started = time.monotonic()
                losses, iters = [], []
                for side in ("pitcher", "batter"):
                    for tr, te in prep["folds"][side]:
                        fit = lc.fit_predict_level(config["backend"], device, {**base, **params}, prep["x"][tr], prep["y"][tr], prep["games"][tr], prep["x"][te], prep["n_classes"],
                                                   reps=1, seed=config["seed"], y_eval=prep["y"][te])
                        losses.append((side, float(fit.member_loss[0]), len(te))); iters.append(fit.n_iter)
                by_side = {s: float(np.average([l for sd, l, _ in losses if sd == s], weights=[n for sd, _, n in losses if sd == s])) for s in ("pitcher", "batter")}
                mean = (by_side["pitcher"] + by_side["batter"]) / 2
                totals[ti].append(mean)
                trial_rows.append({"trial": ti, "group": group, **params, "logloss_pitcher_folds": by_side["pitcher"], "logloss_batter_folds": by_side["batter"], "logloss": mean,
                                   "rounds": float(np.mean(iters)), "seconds": time.monotonic() - started})
            if ctx.progress:
                ctx.progress("param_search", (ti + 1) / len(trials), f"trial {ti + 1}/{len(trials)}")
        scores = [float(np.mean(t)) for t in totals]
        best = int(np.argmin(scores))
        best_rows = [{"rank": rank + 1, "trial": ti, **trials[ti], "logloss": scores[ti]} for rank, ti in enumerate(sorted(range(len(trials)), key=lambda i: scores[i]))]
        sections = (
            make_section("Trials 各組設定的成績（越低越好）", ("trial", "group", "max_depth", "min_leaf", "learning_rate", "l2", "logloss_pitcher_folds", "logloss_batter_folds", "logloss", "rounds", "seconds"),
                         trial_rows, ("trial", "group"), "duckdb", DIGITS),
            make_section("Ranking 設定排名（所有分組的平均）", ("rank", "trial", "max_depth", "min_leaf", "learning_rate", "l2", "logloss"), best_rows, ("rank",), "duckdb", DIGITS),
        )
        extras = {"best_params": trials[best], "best_logloss": scores[best], "trials": len(trials), "folds": config["folds"], "backend": config["backend"], "device": device,
                  "note": "scored on level L3, players the model has not seen, mean over groups, folds over pitchers and over batters"}
        return ResearchResult(sections=sections, extras=extras)

    @staticmethod
    def _prepare(cols, config) -> dict[str, Any]:
        from .league_methods import LeagueModelMethod

        LeagueModelMethod._check_classes(cols, {**config, "target": "outcome"}, "")
        y, classes, keep = lc.encode_outcome(cols["outcome"], config["class_merge"])
        ok = keep & np.isin(np.asarray(cols["pitch_type"]).astype(str), profiles.PITCH_TYPES)
        sub = lc.take(cols, np.nonzero(ok)[0]); y = y[ok]
        profile = profiles.ProfileModel().fit(sub)
        x, _ = lc.level_matrix(profile, sub, "L3", "outcome", history=True)
        games = np.asarray(sub["game_pk"])
        if len(y) > config["tune_rows"]:           # whole games, so that games stay together
            rng = np.random.default_rng(config["seed"])
            uniq = np.unique(games); rng.shuffle(uniq)
            per_game = len(y) / len(uniq)
            chosen = set(uniq[: max(int(config["tune_rows"] / per_game), 1)].tolist())
            idx = np.nonzero(np.array([g in chosen for g in games.tolist()]))[0]
            x, y, games = x[idx], y[idx], games[idx]
            sub = lc.take(sub, idx)
        folds = {side: lc.unit_folds(np.asarray(sub[side]), config["folds"], config["seed"]) for side in ("pitcher", "batter")}
        return {"x": x, "y": y, "games": games, "folds": folds, "n_classes": len(classes)}


register_method(ParamSearchMethod())
