"""Research method ``model_compare``: several outcome models trained on the same data, with the same split and the same seed, listed side by side.

Every model sees the same training rows, is tested on the same held-out rows and the same players left out of the folds, and its pitch effects are asked on the same sample of
rows. The output has the numbers of every model next to each other and the difference between every two models; the rows can be ordered by a column the user picks. Nothing
says which model is best: that is for the user to read.
"""
from __future__ import annotations

import time
from typing import Any

from .config_schema import ConfigError, ConfigField
from .guards import scope_years
from .lazy import Lazy
from .league_methods import DIGITS, GROUPS, LeagueModelMethod, fetch_columns
from .methods import ResearchContext, ResearchMethod, ResearchResult, register_method
from .outcome_model_methods import OutcomeModelTrainMethod, SETTING_NAMES
from .outcome_models import controls as ctl
from .sections import make_section
from .sequencing_methods import BUNT_POLICY, FILTERS, _check_filters

np = Lazy("numpy")
lc = Lazy(".league_core")
md = Lazy(".modeling")
pc = Lazy(".policy_core")
om = Lazy(".outcome_models")
gbm = Lazy(".gbm")

SORT_COLUMNS = ("logloss", "brier", "calibration_error", "fit_seconds")


def model_settings_for(config: dict[str, Any], name: str) -> dict[str, Any]:
    """The settings of one model: the shared ones, then what the user wrote for that model alone."""

    st = {k: config[k] for k in SETTING_NAMES}
    st.update(class_merge=config["class_merge"], seed=config["seed"])
    st.update((config["overrides"] or {}).get(name, {}))
    return st


class ModelCompareMethod(ResearchMethod):
    kind = "model_compare"
    version = 1
    label_zh = "比較多種模型（同一份資料並排）"
    label_en = "Compare several models (the same data, side by side)"
    requires_scope = True
    fields = (
        BUNT_POLICY, FILTERS,
        ConfigField("groups", "str_list", default=list(GROUPS), label_zh="分組（各自訓練）", label_en="Groups (trained separately)"),
        ConfigField("models", "str_list", default=["logistic", "gbm"], label_zh="要比較的模型", label_en="Models to compare",
                    help_zh="logistic、gbm、mixed、nn_embed、sequence 之中勾選；後三種需要 PyTorch。", help_en="Any of logistic, gbm, mixed, nn_embed, sequence (the last three need PyTorch)."),
        ConfigField("overrides", "json", default={}, label_zh="各模型自己的設定", label_en="Settings of single models",
                    help_zh="模型名稱對設定，例如 {\"gbm\": {\"max_depth\": 8}}；沒寫的用下面共用的設定。", help_en="Model name to settings; what is not written there uses the shared settings."),
    ) + ctl.CONTROL_FIELDS + ctl.PARAM_FIELDS + (
        ConfigField("train_years", "int_list", default=[2023], unique_sorted=True, label_zh="訓練年份", label_en="Training years"),
        ConfigField("test_years", "int_list", default=[2024], unique_sorted=True, label_zh="留出年份", label_en="Held-out years"),
        ConfigField("cold_by", "str_list", default=["pitcher"], label_zh="沒見過的球員的評估", label_en="Unseen-player evaluation", help_zh="pitcher、batter；空＝不做。", help_en="pitcher, batter; empty = skip."),
        ConfigField("cold_folds", "int", default=3, minimum=2, maximum=20, label_zh="依球員分折數", label_en="Folds over players"),
        ConfigField("class_merge", "json", default=None, label_zh="結果類別對照（預設同前）", label_en="Outcome class map (default as before)"),
        ConfigField("min_class_count", "int", default=50, minimum=1, label_zh="類別最小訓練樣本", label_en="Minimum training rows per class"),
        ConfigField("effect_sample", "int", default=20000, minimum=100, label_zh="算球種效果用的列數", label_en="Rows used for the pitch effects"),
        ConfigField("support_min_share", "float", default=0.05, minimum=0.0, maximum=1.0, label_zh="支持規則：最小份額", label_en="Support rule: minimum share"),
        ConfigField("support_min_n", "int", default=300, minimum=1, label_zh="支持規則：最小球數", label_en="Support rule: minimum pitches"),
        ConfigField("confidence", "float", default=0.95, minimum=0.5, maximum=0.999, label_zh="區間的信賴水準", label_en="Confidence level"),
        ConfigField("sort_by", "choice", default="logloss", choices=SORT_COLUMNS, label_zh="排序欄位", label_en="Order the rows by",
                    help_zh="只是列的順序，不是「哪個最好」的判斷。", help_en="Only the order of the rows, not a verdict."),
        ConfigField("seed", "int", default=20240601, label_zh="隨機種子（所有模型共用）", label_en="Random seed (shared by all models)"),
        ConfigField("max_rows", "int", default=1_500_000, minimum=1000, label_zh="每組每球季列數上限", label_en="Row cap per group and season"),
    )

    def validate(self, config: dict[str, Any], ctx: ResearchContext) -> None:
        if not config["groups"] or set(config["groups"]) - set(GROUPS):
            raise ConfigError(f"groups must be a non-empty subset of {list(GROUPS)} / groups 必須是 {list(GROUPS)} 的非空子集")
        if not config["models"] or len(set(config["models"])) != len(config["models"]):
            raise ConfigError("models must be a non-empty list without repeats / models 必須是不重複的非空列表")
        if set(config["cold_by"]) - {"pitcher", "batter"}:
            raise ConfigError("cold_by may contain pitcher and batter only / cold_by 只能是 pitcher、batter")
        if config["class_merge"] is None:
            from . import modeling

            config["class_merge"] = dict(modeling.DEFAULT_CLASS_MERGE)
        _check_filters(config["filters"])
        overrides = config["overrides"] if config["overrides"] is not None else {}
        if not isinstance(overrides, dict) or set(overrides) - set(config["models"]) or not all(isinstance(v, dict) for v in overrides.values()):
            raise ConfigError("overrides must map models of this comparison to objects of settings / overrides 必須把這次比較的模型對到設定物件")
        config["overrides"] = overrides
        for name in config["models"]:
            om.get_model_class(name)                                                  # unknown, or needs PyTorch that is not installed
            unknown = set(overrides.get(name, {})) - set(SETTING_NAMES) - {"class_merge", "seed"}
            if unknown:
                raise ConfigError(f"unknown settings for {name}: {sorted(unknown)} / {name} 有未知的設定 {sorted(unknown)}")
            st = model_settings_for(config, name)
            ctl.validate_controls(st, model=name, backend=st["backend"])
            if name == "gbm":
                try:
                    gbm.resolve_device(st["backend"], st["device"])
                except ValueError as exc:
                    raise ConfigError(str(exc)) from exc
        if "game_years" not in ctx.scope:
            raise ConfigError("a comparison needs a scope made of game_years / 比較需要以 game_years 指定範圍")
        years = set(scope_years(ctx.scope)); train, test = set(config["train_years"]), set(config["test_years"])
        if not train or not test or train & test or not (train | test) <= years:
            raise ConfigError("train_years and test_years must be non-empty, disjoint and inside the scope / 訓練與留出年份必須非空、不重疊且在範圍內")

    def run(self, ctx: ResearchContext, config: dict[str, Any]) -> ResearchResult:
        cols_all = fetch_columns(ctx, config, config["groups"], scope_years(ctx.scope))
        names = list(config["models"])
        rows: dict[str, list] = {k: [] for k in ("summary", "classes", "effects", "diff", "controls")}
        comparisons: dict[str, dict[str, Any]] = {}
        classes = None
        steps = len(config["groups"]) * len(names)
        done = 0
        for group in config["groups"]:
            sel = np.nonzero(cols_all["group"] == group)[0]
            if len(sel) == 0:
                raise ConfigError(f"Group {group} has no rows / 分組 {group} 沒有資料")
            cols = lc.take(cols_all, sel)
            year = np.asarray(cols["game_year"])
            ctr = lc.take(cols, np.nonzero(np.isin(year, config["train_years"]))[0]); cte = lc.take(cols, np.nonzero(np.isin(year, config["test_years"]))[0])
            if len(ctr["game_pk"]) == 0 or len(cte["game_pk"]) == 0:
                raise ConfigError(f"Group {group} has no training or no held-out rows / 分組 {group} 沒有訓練或留出資料")
            LeagueModelMethod._check_classes(ctr, {**config, "target": "outcome"}, group)
            _, classes, _ = lc.encode_outcome(ctr["outcome"], config["class_merge"])
            K = len(classes)
            effect_by_model: dict[str, dict[tuple, float]] = {}
            summary_rows: list[dict[str, Any]] = []
            for name in names:
                st = model_settings_for(config, name)
                started = time.monotonic()
                model = om.new_model(name).fit(ctr, None, st, None, classes=classes)
                model.group = group
                seconds = time.monotonic() - started
                sub_tr, ytr = model._encode(ctr, model.settings, fixed_classes=True)
                sub_te, yte = model._encode(cte, model.settings, fixed_classes=True)
                prior = np.bincount(ytr, minlength=K) / max(len(ytr), 1)
                prior_ll = float(-np.log(np.clip(prior[yte], md.EPS, 1.0)).mean())
                evals = [("season_specific", model.predict_proba(sub_te, effects="specific"), yte, len(ytr), seconds)]
                if model.groups or model.profile is not None:
                    evals.append(("season_population", model.predict_proba(sub_te, effects="population"), yte, len(ytr), seconds))
                for by in config["cold_by"]:
                    folds = lc.unit_folds(np.asarray(sub_tr[by]), config["cold_folds"], config["seed"])
                    if not folds:
                        continue
                    eval_idx = np.concatenate([te for _, te in folds]); pos = {int(i): k for k, i in enumerate(eval_idx)}
                    proba = np.zeros((len(eval_idx), K)); secs = 0.0
                    for tr, te in folds:
                        t0 = time.monotonic()
                        fold = om.new_model(name).fit(lc.take(sub_tr, tr), None, {**st, "bagging_reps": 1}, None, classes=classes)
                        proba[[pos[int(i)] for i in te]] = fold.predict_proba(lc.take(sub_tr, te), effects="specific"); secs += time.monotonic() - t0
                    evals.append((f"cold_{by}", proba, ytr[eval_idx], int(np.mean([len(t) for t, _ in folds])), secs))
                for mode, proba, y, n_train, secs in evals:
                    ll = md.row_logloss(y, proba)
                    calib = md.calibration_rows(y, proba, classes, 10)
                    weights = np.array([r["n"] for r in calib], dtype=float)
                    err = np.array([abs(r["mean_pred"] - r["observed"]) if r["mean_pred"] is not None and r["observed"] is not None else np.nan for r in calib], dtype=float)
                    ok = np.isfinite(err) & (weights > 0)
                    summary_rows.append({"group": group, "model": name, "mode": mode, "n_train": int(n_train), "n_eval": int(len(y)), "logloss": float(ll.mean()),
                                         "brier": float(md.multiclass_brier_rows(y, proba).mean()), "prior_logloss": prior_ll if mode.startswith("season") else None,
                                         "calibration_error": float(np.average(err[ok], weights=weights[ok])) if ok.any() else None, "fit_seconds": secs, "n_iter": model.info.get("n_iter"),
                                         "hit_cap": int(bool(model.info.get("hit_cap")))})
                    for c, cname in enumerate(classes):
                        rows["classes"].append({"group": group, "model": name, "mode": mode, "class": cname, "loss": float(ll[y == c].mean()) if (y == c).any() else None, "n": int((y == c).sum())})
                rng = np.random.default_rng(config["seed"])                                # the same rows are asked about for every model
                eff = OutcomeModelTrainMethod()._effect_rows(model, sub_tr, sub_te, config, rng, group)
                for r in eff:
                    rows["effects"].append({"group": group, "model": name, **{k: r[k] for k in ("phase", "action", "class", "effect", "lo", "hi", "n")}})
                effect_by_model[name] = {(r["phase"], r["action"], r["class"]): r["effect"] for r in eff}
                comparisons.setdefault(name, {})[group] = pc.chance_summary([np.nan if r["lo"] is None else r["lo"] for r in eff], [np.nan if r["hi"] is None else r["hi"] for r in eff], config["confidence"])
                rows["controls"] += [{"group": group, **r, "model": name} for r in ctl.describe_controls(st, model=name, backend=st["backend"])]
                done += 1
                if ctx.progress:
                    ctx.progress("model_compare", done / steps, f"{group} {name}")
            for i, a in enumerate(names):
                for b in names[i + 1:]:
                    for key in sorted(set(effect_by_model[a]) & set(effect_by_model[b])):
                        rows["diff"].append({"group": group, "model_a": a, "model_b": b, "phase": key[0], "action": key[1], "class": key[2],
                                             "difference": effect_by_model[a][key] - effect_by_model[b][key]})
            key = config["sort_by"]
            summary_rows.sort(key=lambda r: (r["mode"], float("inf") if r[key] is None else r[key]))
            rows["summary"] += summary_rows
        sections = (
            make_section(f"Comparison 並排比較（每個模型一列，依 {config['sort_by']} 排序，不是優劣判斷）", ("group", "model", "mode", "n_train", "n_eval", "logloss", "brier", "prior_logloss", "calibration_error", "fit_seconds",
                                                                                                "n_iter", "hit_cap"), rows["summary"], ("group", "model", "mode"), "duckdb", DIGITS),
            make_section("Class loss 各類結果的損失", ("group", "model", "mode", "class", "loss", "n"), rows["classes"], ("group", "model", "mode", "class"), "duckdb", DIGITS),
            make_section("Pitch effects 各模型的球種效果（聯盟平均球員；同一批列；全部列出）", ("group", "model", "phase", "action", "class", "effect", "lo", "hi", "n"), rows["effects"],
                         ("group", "model", "phase", "action", "class"), "duckdb", DIGITS),
            make_section("Effect differences 兩兩模型的球種效果差", ("group", "model_a", "model_b", "phase", "action", "class", "difference"), rows["diff"], ("group", "model_a", "model_b", "phase", "action", "class"), "duckdb", DIGITS),
            make_section("Controls 各模型用的偏差控制", ("group", "model", "control", "setting", "how", "backend"), rows["controls"], ("group", "model", "control"), "duckdb", DIGITS),
        )
        extras = {"scope": ctx.scope, "models": names, "classes": classes, "hit_cap": [{"group": r["group"], "model": r["model"], "mode": r["mode"]} for r in rows["summary"] if r.get("hit_cap")], "train_years": list(config["train_years"]), "test_years": list(config["test_years"]), "sort_by": config["sort_by"],
                  "comparisons": {"pitch_effects": comparisons}}
        return ResearchResult(sections=sections, extras=extras)


register_method(ModelCompareMethod())
