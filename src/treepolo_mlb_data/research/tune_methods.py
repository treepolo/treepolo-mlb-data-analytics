"""Research method ``param_search``: random search of the settings of any outcome model, scored on data the model has not seen.

Every trial is a full setting of the chosen model (with the bias controls the user chose). It is scored on unseen players (folds over pitchers and over batters) and/or on a year
the model was not trained on (one fold per year), mean log-loss over groups and over the chosen scorings. The best trial's settings are what the following steps use; the search
only ranks, it does not decide anything for the user: every trial's score is listed.
"""
from __future__ import annotations

import itertools
import time
from typing import Any

from .config_schema import ConfigError, ConfigField
from .lazy import Lazy
from .league_methods import DIGITS, GROUPS, fetch_columns
from .methods import ResearchContext, ResearchMethod, ResearchResult, register_method
from .outcome_models import controls as ctl
from .sections import make_section
from .sequencing_methods import BUNT_POLICY, FILTERS, _check_filters

np = Lazy("numpy")
lc = Lazy(".league_core")
md = Lazy(".modeling")
gbm = Lazy(".gbm")
om = Lazy(".outcome_models")
dz = Lazy(".outcome_models.design")

SCORINGS = ("pitcher", "batter", "year")
DEFAULT_GRIDS: dict[str, dict[str, list]] = {
    "gbm": {"max_depth": [3, 5, 8], "min_leaf": [200, 1000], "learning_rate": [0.05, 0.1], "l2": [1.0, 10.0]},
    "logistic": {"l2": [0.3, 1.0, 3.0, 10.0], "effect_penalty_pitcher": [5.0, 20.0, 80.0], "effect_penalty_batter": [5.0, 20.0, 80.0]},
}
FALLBACK_GRID = {"learning_rate": [0.003, 0.01, 0.03], "l2": [0.3, 1.0, 3.0]}
DEFAULT_GRID = DEFAULT_GRIDS["gbm"]               # kept for callers that import it
TUNABLE = {f.name for f in ctl.PARAM_FIELDS + ctl.CONTROL_FIELDS if f.type in ("int", "float")} - {"bagging_reps", "location_samples"}


def sample_trials(grid: dict[str, list], n: int, seed: int) -> list[dict[str, Any]]:
    """``n`` different settings from the grid (all of them when the grid is smaller), always in the same order for the same seed."""

    import random

    names = sorted(grid)
    combos = [dict(zip(names, values)) for values in itertools.product(*(grid[k] for k in names))]
    random.Random(seed).shuffle(combos)
    return combos[: max(1, min(n, len(combos)))]


class ParamSearchMethod(ResearchMethod):
    kind = "param_search"
    version = 2     # 2: any outcome model, the bias controls, scoring on unseen players and/or a year not trained on; no reserved-season rule
    label_zh = "模型參數搜尋（用沒見過的球員或沒訓練過的年份打分）"
    label_en = "Model setting search (scored on unseen players and/or an unseen year)"
    requires_scope = True
    fields = (
        BUNT_POLICY, FILTERS,
        ConfigField("groups", "str_list", default=list(GROUPS), label_zh="分組（每個設定在所有分組上一起評分）", label_en="Groups (every setting is scored on all of them)"),
        ConfigField("model", "choice", default="gbm", choices=ctl.MODEL_KINDS, label_zh="模型", label_en="Model"),
    ) + ctl.CONTROL_FIELDS + ctl.PARAM_FIELDS + (
        ConfigField("trials", "int", default=8, minimum=1, maximum=100, label_zh="試幾組設定", label_en="Settings to try"),
        ConfigField("folds", "int", default=5, minimum=2, maximum=10, label_zh="依球員分折數", label_en="Folds over players"),
        ConfigField("scoring", "str_list", default=["pitcher", "batter"], label_zh="打分方式（可複選）", label_en="Scoring (any of them)",
                    help_zh="pitcher：沒見過的投手；batter：沒見過的打者；year：留出一整個年份（需要兩個以上年份）。", help_en="Unseen pitchers, unseen batters, a held-out year (needs two or more years)."),
        ConfigField("grid", "json", default=None, label_zh="設定格子", label_en="Grid",
                    help_zh="設定名稱對候選值列表；空白＝依模型用預設格子。名稱可以是樹深、學習率、L2、各效應的收縮強度等數值設定。", help_en="Setting name to candidate values; empty uses the default grid of the model."),
        ConfigField("tune_rows", "int", default=400_000, minimum=1000, label_zh="每組搜尋用的列數上限（整場比賽抽樣）", label_en="Row cap per group while searching (whole games sampled)"),
        ConfigField("class_merge", "json", default=None, label_zh="結果類別對照（預設同前）", label_en="Outcome class map (default as before)"),
        ConfigField("min_class_count", "int", default=50, minimum=1, label_zh="類別最小樣本", label_en="Minimum rows per class"),
        ConfigField("seed", "int", default=20240601, label_zh="隨機種子", label_en="Random seed"),
        ConfigField("max_rows", "int", default=1_500_000, minimum=1000, label_zh="每組每球季列數上限", label_en="Row cap per group and season"),
    )

    def validate(self, config: dict[str, Any], ctx: ResearchContext) -> None:
        if not config["groups"] or set(config["groups"]) - set(GROUPS):
            raise ConfigError(f"groups must be a non-empty subset of {list(GROUPS)} / groups 必須是 {list(GROUPS)} 的非空子集")
        if not config["scoring"] or set(config["scoring"]) - set(SCORINGS):
            raise ConfigError(f"scoring must be a non-empty subset of {list(SCORINGS)} / scoring 必須是 {list(SCORINGS)} 的非空子集")
        om.get_model_class(config["model"])
        ctl.validate_controls(config, model=config["model"], backend=config["backend"])
        grid = config["grid"]
        if grid is None:
            config["grid"] = grid = {k: list(v) for k, v in DEFAULT_GRIDS.get(config["model"], FALLBACK_GRID).items()}
        if not isinstance(grid, dict) or not grid or set(grid) - TUNABLE or not all(isinstance(v, list) and v for v in grid.values()):
            raise ConfigError(f"grid must map settings among {sorted(TUNABLE)} to non-empty lists / grid 必須把 {sorted(TUNABLE)} 之中的設定對到非空列表")
        if config["class_merge"] is None:
            from . import modeling as md_module

            config["class_merge"] = dict(md_module.DEFAULT_CLASS_MERGE)
        _check_filters(config["filters"])
        if "year" in config["scoring"] and len(self._years(ctx)) < 2:
            raise ConfigError("scoring 'year' needs two or more years in the scope / 打分方式 year 需要範圍內有兩個以上年份")
        if config["model"] == "gbm":
            try:
                gbm.resolve_device(config["backend"], config["device"])
            except ValueError as exc:
                raise ConfigError(str(exc)) from exc

    @staticmethod
    def _years(ctx) -> list[int]:
        from .guards import scope_years

        return scope_years(ctx.scope)

    def run(self, ctx: ResearchContext, config: dict[str, Any]) -> ResearchResult:
        cols_all = fetch_columns(ctx, config, config["groups"], self._years(ctx))
        trials = sample_trials(config["grid"], config["trials"], config["seed"])
        base = {k: config[k] for k in (f.name for f in ctl.CONTROL_FIELDS + ctl.PARAM_FIELDS)}
        base.update(class_merge=config["class_merge"], seed=config["seed"], bagging_reps=1)
        per_group = {}
        for group in config["groups"]:
            sel = np.nonzero(cols_all["group"] == group)[0]
            if len(sel) == 0:
                raise ConfigError(f"Group {group} has no rows / 分組 {group} 沒有資料")
            per_group[group] = self._prepare(lc.take(cols_all, sel), config)
        grid_keys = sorted(config["grid"])
        trial_rows, totals = [], [[] for _ in trials]
        for ti, params in enumerate(trials):
            for group, prep in per_group.items():
                started = time.monotonic()
                by_side: dict[str, float] = {}; iters = []
                for side in config["scoring"]:
                    losses, weights = [], []
                    for tr, te in prep["folds"].get(side, []):
                        model = om.new_model(config["model"]).fit(lc.take(prep["sub"], tr), None, {**base, **params}, None, classes=prep["classes"])
                        proba = model.predict_proba(lc.take(prep["sub"], te), effects="specific")
                        losses.append(float(md.row_logloss(prep["y"][te], proba).mean())); weights.append(len(te)); iters.append(model.info.get("n_iter", np.nan))
                    if losses:
                        by_side[side] = float(np.average(losses, weights=weights))
                if not by_side:
                    raise ConfigError(f"no fold could be built for group {group} / 分組 {group} 無法建立任何分折")
                mean = float(np.mean(list(by_side.values())))
                totals[ti].append(mean)
                trial_rows.append({"trial": ti, "group": group, **{k: params[k] for k in grid_keys}, **{f"logloss_{s}_folds": by_side.get(s) for s in SCORINGS}, "logloss": mean,
                                   "rounds": float(np.nanmean(iters)) if iters else None, "seconds": time.monotonic() - started})
            if ctx.progress:
                ctx.progress("param_search", (ti + 1) / len(trials), f"trial {ti + 1}/{len(trials)}")
        scores = [float(np.mean(t)) for t in totals]
        best = int(np.argmin(scores))
        best_rows = [{"rank": rank + 1, "trial": ti, **{k: trials[ti][k] for k in grid_keys}, "logloss": scores[ti]} for rank, ti in enumerate(sorted(range(len(trials)), key=lambda i: scores[i]))]
        score_cols = tuple(f"logloss_{s}_folds" for s in SCORINGS)
        sections = (
            make_section("Trials 各組設定的成績（越低越好）", ("trial", "group", *grid_keys, *score_cols, "logloss", "rounds", "seconds"), trial_rows, ("trial", "group"), "duckdb", DIGITS),
            make_section("Ranking 設定排名（所有分組的平均）", ("rank", "trial", *grid_keys, "logloss"), best_rows, ("rank",), "duckdb", DIGITS),
        )
        extras = {"best_params": trials[best], "best_logloss": scores[best], "trials": len(trials), "folds": config["folds"], "model": config["model"], "scoring": list(config["scoring"]),
                  "backend": config["backend"], "device": config["device"],
                  "note": "scored on data the model has not seen, mean over groups and the chosen scorings; the controls of the settings are in effect while searching"}
        return ResearchResult(sections=sections, extras=extras)

    @staticmethod
    def _prepare(cols, config) -> dict[str, Any]:
        from .league_methods import LeagueModelMethod

        LeagueModelMethod._check_classes(cols, {**config, "target": "outcome"}, "")
        y_all, classes, keep = lc.encode_outcome(cols["outcome"], config["class_merge"])
        ok = keep & (dz.type_index(cols["pitch_type"]) >= 0)
        sub = lc.take(cols, np.nonzero(ok)[0]); y = y_all[ok]
        games = np.asarray(sub["game_pk"])
        if len(y) > config["tune_rows"]:           # whole games, so that games stay together
            rng = np.random.default_rng(config["seed"])
            uniq = np.unique(games); rng.shuffle(uniq)
            per_game = len(y) / len(uniq)
            chosen = set(uniq[: max(int(config["tune_rows"] / per_game), 1)].tolist())
            idx = np.nonzero(np.array([g in chosen for g in games.tolist()]))[0]
            y = y[idx]; sub = lc.take(sub, idx)
        folds: dict[str, list] = {}
        for side in config["scoring"]:
            if side in ("pitcher", "batter"):
                folds[side] = lc.unit_folds(np.asarray(sub[side]), config["folds"], config["seed"])
            else:
                year = np.asarray(sub["game_year"])
                folds[side] = [(np.nonzero(year != yv)[0], np.nonzero(year == yv)[0]) for yv in np.unique(year) if (year == yv).any() and (year != yv).any()]
        return {"sub": sub, "y": y, "classes": classes, "folds": folds}


register_method(ParamSearchMethod())
