from __future__ import annotations

import math
from typing import Any

from ..analysis import Binary, Column, Filter, IsNull, Limit, Literal, NamedExpr, OrderKey, PITCH_GRAIN, Project, Sort
from ..analysis.exclusions import BUNT_POLICIES
from ..analysis.pitch_table import build_pitch_table, pitch_table_columns
from . import modeling as md
from .config_schema import ConfigError, ConfigField
from .guards import PURPOSES, RESERVED_YEARS, check_modeling_scope, check_purpose, scope_years
from .methods import ArtifactData, ResearchContext, ResearchMethod, ResearchResult, register_method
from .re_source import DEFAULT_RE_SCOPE, get_re_table, re_method_inputs
from .runner import run_checked
from .scope import normalize_scope
from .sections import make_section
from .sequencing_methods import BUNT_POLICY, CONFIDENCE, FILTERS, PURPOSE, RE_SCOPE, _check_filters
from .stats import z_value

GROUPS = ("RvR", "RvL", "LvR", "LvL", "same_side", "opposite_side")
EXTRA_RAW = ("pfx_x", "pfx_z", "release_spin_rate", "release_extension")
DIGITS = 8   # model outputs are rounded harder than counts: threaded fits differ in the last digits


class OutcomeModelMethod(ResearchMethod):
    """Multinomial outcome models (logistic / gradient boosting) per handedness group with held-out evaluation and sequence gain."""

    kind = "outcome_model"
    version = 3  # 2: entity_features, hgb_early_stopping (default off), memory 0; 3: sequence_ladder
    label_zh = "結果機率模型與序列增益"
    label_en = "Outcome model and sequence gain"
    requires_scope = True
    fields = (
        PURPOSE, RE_SCOPE, BUNT_POLICY, CONFIDENCE, FILTERS,
        ConfigField("groups", "str_list", default=["RvR", "RvL", "LvR", "LvL"], label_zh="分組", label_en="Groups",
                    help_zh="RvR、RvL、LvR、LvL；same_side／opposite_side 需要鏡像。", help_en="same_side/opposite_side use the mirrored frame."),
        ConfigField("models", "str_list", default=["logistic", "hgb"], label_zh="模型", label_en="Models"),
        ConfigField("variants", "str_list", default=["base", "full"], label_zh="特徵組", label_en="Feature sets",
                    help_zh="base＝不含前球資訊；full＝加上序列特徵。兩者都有才算序列增益。", help_en="Both are needed for the sequence gain."),
        ConfigField("base_numeric", "str_list", default=["plate_x", "plate_z", "release_speed", "pfx_x", "pfx_z", "outs_when_up", "pitch_index_in_pa"], label_zh="基本數值特徵", label_en="Base numeric features"),
        ConfigField("base_categorical", "str_list", default=["pitch_type", "zone", "count_state", "bases"], label_zh="基本類別特徵", label_en="Base categorical features"),
        ConfigField("sequence_numeric", "str_list", default=["speed_diff_prev1", "dx_prev1", "dz_prev1"], label_zh="序列數值特徵", label_en="Sequence numeric features"),
        ConfigField("sequence_categorical", "str_list", default=["prev1_pitch_type", "prev2_pitch_type", "prev1_description", "streak_cap"], label_zh="序列類別特徵", label_en="Sequence categorical features"),
        ConfigField("memory", "int", default=2, minimum=0, maximum=5, label_zh="序列記憶長度", label_en="Sequence memory",
                    help_zh="0＝完全不用前球資訊（只剩 base，沒有序列增益）。", help_en="0 = no previous-pitch information at all (base only, no sequence gain)."),
        ConfigField("sequence_ladder", "bool", default=False, label_zh="記憶長度階梯", label_en="Memory ladder",
                    help_zh="true：變體 base、m1…m{memory}（m_j 含前 j 球的球種、前一球結果、連投顆數與數值差），輸出各 m 相對 base 與相對前一個 m 的邊際增益。", help_en="Fits base, m1..m{memory}; reports cumulative and marginal gains."),
        ConfigField("entity_features", "str_list", default=[], label_zh="個體效應（投手、打者）", label_en="Entity effects",
                    help_zh="可含 pitcher、batter；只對 logistic 生效（稀疏單熱編碼，不足 entity_min_count 球的個體併入 OTHER）。", help_en="pitcher and/or batter; logistic only."),
        ConfigField("entity_min_count", "int", default=200, minimum=1, label_zh="個體最小訓練球數", label_en="Minimum training pitches per entity"),
        ConfigField("hgb_early_stopping", "bool", default=False, label_zh="提升樹早停", label_en="Boosting early stopping",
                    help_zh="預設關閉：早停用隨機驗證集，是提升樹結果不穩定的主因。", help_en="Off by default: its random validation split made boosting results unstable."),
        ConfigField("streak_cap", "int", default=4, minimum=2, maximum=10, label_zh="連投顆數上限（特徵）", label_en="Streak cap (feature)"),
        ConfigField("class_merge", "json", default=md.DEFAULT_CLASS_MERGE, label_zh="結果類別對照", label_en="Outcome class map",
                    help_zh="結果類別 → 模型類別；未列出的類別（短打、unclassified）排除並計數。", help_en="Unlisted categories are dropped and counted."),
        ConfigField("min_class_count", "int", default=50, minimum=1, label_zh="類別最小訓練樣本", label_en="Minimum training rows per class",
                    help_zh="任一類別少於此數就報錯，請用 class_merge 合併。", help_en="Fewer is an error; merge classes with class_merge."),
        ConfigField("split", "choice", default="holdout_years", choices=("holdout_years", "grouped_kfold"), label_zh="切分方式", label_en="Split"),
        ConfigField("train_years", "int_list", default=[2023], unique_sorted=True, label_zh="訓練球季", label_en="Training seasons"),
        ConfigField("test_years", "int_list", default=[2024], unique_sorted=True, label_zh="留出球季", label_en="Held-out seasons"),
        ConfigField("n_folds", "int", default=5, minimum=2, maximum=20, label_zh="交叉驗證折數（依比賽分組）", label_en="Folds (grouped by game)"),
        ConfigField("C", "float", default=1.0, minimum=1e-6, label_zh="邏輯迴歸正則化 C", label_en="Logistic regression C"),
        ConfigField("max_iter", "int", default=300, minimum=10, label_zh="邏輯迴歸最大迭代", label_en="Logistic regression max iterations"),
        ConfigField("hgb_max_iter", "int", default=150, minimum=1, label_zh="提升樹棵數", label_en="Boosting iterations"),
        ConfigField("hgb_learning_rate", "float", default=0.1, minimum=1e-4, maximum=1.0, label_zh="提升樹學習率", label_en="Boosting learning rate"),
        ConfigField("hgb_max_depth", "int", default=6, minimum=1, maximum=30, label_zh="提升樹深度", label_en="Boosting depth"),
        ConfigField("hgb_min_samples_leaf", "int", default=20, minimum=1, label_zh="葉節點最小樣本", label_en="Minimum samples per leaf"),
        ConfigField("seed", "int", default=20240601, label_zh="隨機種子", label_en="Random seed"),
        ConfigField("bootstrap_reps", "int", default=200, minimum=20, maximum=5000, label_zh="Bootstrap 次數（依比賽重抽）", label_en="Bootstrap replicates (games)"),
        ConfigField("calibration_bins", "int", default=10, minimum=2, maximum=50, label_zh="校準分箱數", label_en="Calibration bins"),
        ConfigField("ev_check", "bool", default=True, label_zh="期望得分檢查", label_en="Expected-value check"),
        ConfigField("min_value_cell_n", "int", default=30, minimum=1, label_zh="結果價值格子最小樣本", label_en="Minimum rows per value cell"),
        ConfigField("table_consistency", "bool", default=True, label_zh="與次數表一致性檢查", label_en="Check against counted tables"),
        ConfigField("consistency_min_n", "int", default=2000, minimum=10, label_zh="一致性檢查的格子大小", label_en="Cell size for the consistency check"),
        ConfigField("max_rows_per_group", "int", default=1_500_000, minimum=1000, label_zh="每組列數上限", label_en="Row cap per group"),
    )

    # ------------------------------------------------------------------ validation
    def validate(self, config: dict[str, Any], ctx: ResearchContext) -> None:
        purpose = config["purpose"]; years = set(scope_years(ctx.scope)); reserved = set(RESERVED_YEARS)
        if not config["groups"] or set(config["groups"]) - set(GROUPS):
            raise ConfigError(f"groups must be a non-empty subset of {list(GROUPS)} / groups 必須是 {list(GROUPS)} 的非空子集")
        if not config["models"] or set(config["models"]) - set(md.MODEL_NAMES):
            raise ConfigError(f"models must be a non-empty subset of {list(md.MODEL_NAMES)} / models 必須是 {list(md.MODEL_NAMES)} 的非空子集")
        if config["sequence_ladder"]:
            if config["memory"] < 1:
                raise ConfigError("sequence_ladder needs memory >= 1 / sequence_ladder 需要 memory >= 1")
            config["variants"] = ["base"] + [f"m{j}" for j in range(1, config["memory"] + 1)]
            config["sequence_categorical"] = []
        if not config["variants"] or set(config["variants"]) - ({"base", "full"} | {f"m{j}" for j in range(1, 6)}):
            raise ConfigError("variants must be a non-empty subset of base, full / variants 必須是 base、full 的非空子集")
        for key, allowed in (("base_numeric", md.NUMERIC_FEATURES), ("sequence_numeric", md.NUMERIC_FEATURES),
                             ("base_categorical", md.CATEGORICAL_FEATURES), ("sequence_categorical", md.CATEGORICAL_FEATURES)):
            bad = sorted(set(config[key]) - set(allowed))
            if bad:
                raise ConfigError(f"{key} has unknown features {bad}; allowed {list(allowed)} / {key} 含未知特徵 {bad}")
        num_f, cat_f = self._split_features(config, f"m{config['memory']}" if config["sequence_ladder"] else "full"); needed = set(md.source_columns(num_f + cat_f))
        available = pitch_table_columns(memory=max(config["memory"], 1), with_value=config["ev_check"], mirror=True, extra_columns=EXTRA_RAW)
        if needed - available:
            raise ConfigError(f"features need columns the pitch table does not have (memory too small?): {sorted(needed - available)} / 特徵需要的欄位不存在（memory 太小？）")
        if set(config["entity_features"]) - {"pitcher", "batter"} or len(set(config["entity_features"])) != len(config["entity_features"]):
            raise ConfigError("entity_features must be distinct values of pitcher, batter / entity_features 只能是 pitcher、batter")
        if config["entity_features"] and "hgb" in config["models"]:
            raise ConfigError("entity_features need models=['logistic']: boosting would memorize individuals / entity_features 只能搭配 logistic")
        if config["memory"] == 0:   # normalized in place so the stored settings say what was really run
            config["variants"] = ["base"]; config["sequence_numeric"] = []; config["sequence_categorical"] = []
        mapping = config["class_merge"]
        if not isinstance(mapping, dict) or not mapping or not all(isinstance(k, str) and isinstance(v, str) for k, v in mapping.items()):
            raise ConfigError("class_merge must be a non-empty object of strings / class_merge 必須是非空的字串對照")
        _check_filters(config["filters"])
        if config["ev_check"]:
            config["re_scope"] = normalize_scope(config["re_scope"], required=True)
            check_modeling_scope(config["re_scope"], purpose, "re_scope")
        train, test = set(config["train_years"]), set(config["test_years"])
        if purpose == "tuning":
            check_purpose(ctx.scope, "tuning")
        if purpose == "extra_study" and config["split"] != "grouped_kfold":
            raise ConfigError("extra_study has no clean test set: use split=grouped_kfold / extra_study 沒有乾淨的檢驗集，請用 grouped_kfold")
        if purpose == "final_test" and config["split"] != "holdout_years":
            raise ConfigError("final_test needs split=holdout_years / final_test 需要 holdout_years")
        if config["split"] == "holdout_years":
            if "game_years" not in ctx.scope:
                raise ConfigError("holdout_years needs a scope made of game_years / holdout_years 需要以 game_years 指定範圍")
            if not train or not test or train & test or not (train | test) <= years:
                raise ConfigError("train_years and test_years must be non-empty, disjoint and inside the scope / 訓練與留出球季必須非空、不重疊且在範圍內")
            if purpose == "final_test" and (train & reserved or not test <= reserved):
                raise ConfigError(f"final_test: train_years must avoid {sorted(reserved)} and test_years must be reserved seasons / final_test：訓練球季不得含保留球季，留出球季必須是保留球季")
            if purpose == "tuning" and test & reserved:
                raise ConfigError("tuning cannot hold out a reserved season / tuning 不可留出保留球季")

    def method_inputs(self, ctx: ResearchContext, config: dict[str, Any]) -> dict[str, Any]:
        return re_method_inputs(ctx, config) if config["ev_check"] else {}

    # ------------------------------------------------------------------------ run
    def run(self, ctx: ResearchContext, config: dict[str, Any]) -> ResearchResult:
        import numpy as np
        from sklearn.model_selection import GroupKFold

        re = get_re_table(ctx, config["re_scope"]) if config["ev_check"] else None
        mirror = any(g in ("same_side", "opposite_side") for g in config["groups"])
        all_feats = {v: self._split_features(config, v) for v in config["variants"]}
        columns = ["pitch_uid", "game_pk", "game_year", "outcome", "pitch_type", "balls", "strikes", "streak_pos"]
        for v in all_feats.values():
            columns += md.source_columns(v[0] + v[1])
        if re:
            columns += ["state_code", "pitch_value"]
        columns += list(config["entity_features"])
        columns = list(dict.fromkeys(columns))
        table = build_pitch_table(ctx.source_node(), bunt_policy=config["bunt_policy"], memory=max(config["memory"], 1),
                                  re_by_state=re.re_by_state if re else None, mirror=mirror, extra_columns=EXTRA_RAW, filters=config["filters"])
        fit_rows, gain_rows, class_rows, cal_rows, cons_rows, ev_rows, coef_rows = [], [], [], [], [], [], []
        z = z_value(config["confidence"]); backend = "duckdb"
        for group in config["groups"]:
            field = "frame_group" if group in ("same_side", "opposite_side") else "hand_group"
            node = Limit(Sort(Project(Filter(table, Binary(Column(field), "=", Literal(group))), tuple(NamedExpr(c, Column(c)) for c in columns), PITCH_GRAIN),
                              (OrderKey(Column("game_pk")), OrderKey(Column("pitch_uid")))), config["max_rows_per_group"] + 1)
            result = run_checked(ctx, node)
            backend = result.backend
            if len(result.rows) > config["max_rows_per_group"]:
                raise ConfigError(f"Group {group} has more than max_rows_per_group rows / 分組 {group} 的列數超過上限")
            rows = [dict(r) for r in result.rows]
            md.add_derived(rows, config["streak_cap"])
            n_unmapped = sum(r["outcome"] not in config["class_merge"] for r in rows)
            rows = [r for r in rows if r["outcome"] in config["class_merge"]]
            base_num = config["base_numeric"]
            n_missing = sum(any(r[c] is None for c in base_num) for r in rows)
            rows = [r for r in rows if all(r[c] is not None for c in base_num)]
            if not rows:
                raise ConfigError(f"Group {group} has no usable rows / 分組 {group} 沒有可用資料")
            classes = sorted({config["class_merge"][r["outcome"]] for r in rows})
            y = np.array([classes.index(config["class_merge"][r["outcome"]]) for r in rows])
            years = np.array([r["game_year"] for r in rows]); games = np.array([r["game_pk"] for r in rows])
            folds = self._folds(config, years, games, GroupKFold)
            n_eval = int(sum(len(te) for _, te in folds))
            eval_index = np.concatenate([te for _, te in folds]); pos = {int(i): k for k, i in enumerate(eval_index)}
            for tr, _ in folds:
                counts = np.bincount(y[tr], minlength=len(classes))
                small = [classes[c] for c in range(len(classes)) if counts[c] < config["min_class_count"]]
                if small:
                    raise ConfigError(f"Classes {small} have fewer than min_class_count training rows in group {group}; merge them with class_merge / 類別 {small} 訓練樣本不足，請用 class_merge 合併")
            preds: dict[tuple[str, str], Any] = {}; seconds: dict[tuple[str, str], float] = {}; nfeat: dict[str, int] = {}
            ev_pred: dict[tuple[str, str], Any] = {}
            prior_loss = np.zeros(n_eval); ev_fallback: dict[tuple[str, str], int] = {}
            for fi, (tr, te) in enumerate(folds):
                prior = np.bincount(y[tr], minlength=len(classes)) / len(tr)
                prior_loss[[pos[int(i)] for i in te]] = -np.log(np.clip(prior[y[te]], md.EPS, 1.0))
                if re:
                    table_v, class_mean = md.value_table(y[tr], [rows[i]["state_code"] for i in tr], [rows[i]["pitch_value"] for i in tr], len(classes), config["min_value_cell_n"])
                for variant, (num, cat) in all_feats.items():
                    tr_rows = [rows[i] for i in tr]; te_rows = [rows[i] for i in te]
                    design = md.Design(num, cat, list(config["entity_features"]), config["entity_min_count"]).fit(tr_rows)
                    xtr, xte = design.transform(tr_rows), design.transform(te_rows)
                    nfeat[variant] = len(design.columns)
                    for model in config["models"]:
                        proba, secs, fitted = md.fit_predict(model, config, xtr, y[tr], xte, len(classes))
                        key = (model, variant)
                        preds.setdefault(key, np.zeros((n_eval, len(classes))))[[pos[int(i)] for i in te]] = proba
                        seconds[key] = seconds.get(key, 0.0) + secs
                        if re:
                            ev, fb = md.expected_values(proba, [rows[i]["state_code"] for i in te], table_v, class_mean)
                            ev_pred.setdefault(key, np.zeros(n_eval))[[pos[int(i)] for i in te]] = ev
                            ev_fallback[key] = ev_fallback.get(key, 0) + fb
                        if model == "logistic" and len(folds) == 1:
                            for ci, cname in enumerate(classes):
                                for fname, coef in zip(design.columns, fitted.coef_[ci]):
                                    if fname.startswith("ent:"):
                                        continue
                                    coef_rows.append({"group": group, "variant": variant, "class": cname, "feature": fname, "coef": float(coef)})
            y_eval = y[eval_index]; games_eval = games[eval_index]
            realized = np.array([np.nan if rows[i]["pitch_value"] is None else rows[i]["pitch_value"] for i in eval_index]) if re else None
            losses: dict[tuple[str, str], Any] = {}
            for key, proba in preds.items():
                model, variant = key
                loss = md.row_logloss(y_eval, proba); losses[key] = loss
                brier = md.multiclass_brier_rows(y_eval, proba)
                fit_rows.append({"group": group, "model": model, "variant": variant, "n_train": int(np.mean([len(t) for t, _ in folds])), "n_eval": n_eval,
                                 "n_features": nfeat[variant], "classes": ",".join(classes), "logloss": float(loss.mean()), "brier": float(brier.mean()),
                                 "prior_logloss": float(prior_loss.mean()), "skill": float(1 - loss.mean() / prior_loss.mean()), "fit_seconds": seconds[key],
                                 "rows_dropped_unmapped": n_unmapped, "rows_dropped_missing": n_missing})
                for r in md.calibration_rows(y_eval, proba, classes, config["calibration_bins"]):
                    cal_rows.append({"group": group, "model": model, "variant": variant, **r})
                if config["table_consistency"]:
                    keys = [(rows[i]["count_state"], rows[i]["pitch_type"]) for i in eval_index]
                    for r in md.table_consistency(keys, y_eval, proba, classes, min_n=config["consistency_min_n"], confidence=config["confidence"]):
                        cons_rows.append({"group": group, "model": model, "variant": variant, **r})
                if re:
                    ok = ~np.isnan(realized); err = ev_pred[key][ok] - realized[ok]
                    ev_rows.append({"group": group, "model": model, "variant": variant, "n_value": int(ok.sum()), "rmse": float(np.sqrt(np.mean(err ** 2))),
                                    "mean_error": float(err.mean()), "fallback_lookups": ev_fallback[key]})
            variants = list(config["variants"])
            if config["sequence_ladder"]:
                pairs = [(variants[i - 1], variants[i]) for i in range(1, len(variants))] + [("base", v) for v in variants[2:]]
            elif {"base", "full"} <= set(variants):
                pairs = [("base", "full")]
            else:
                pairs = []
            for first, second in pairs:
                for model in config["models"]:
                    delta = losses[(model, first)] - losses[(model, second)]
                    mean, lo, hi, se = md.paired_game_bootstrap(delta, games_eval, reps=config["bootstrap_reps"], seed=config["seed"], confidence=config["confidence"])
                    row = {"group": group, "model": model, "comparison": f"{first}→{second}", "delta_logloss": mean, "lo": lo, "hi": hi, "se": se, "n_eval": n_eval}
                    if re:
                        ok = ~np.isnan(realized)
                        d_ev = (ev_pred[(model, first)][ok] - realized[ok]) ** 2 - (ev_pred[(model, second)][ok] - realized[ok]) ** 2
                        m2, l2, h2, _ = md.paired_game_bootstrap(d_ev, games_eval[ok], reps=config["bootstrap_reps"], seed=config["seed"], confidence=config["confidence"])
                        row.update(delta_ev_mse=m2, ev_lo=l2, ev_hi=h2)
                    gain_rows.append(row)
                    for ci, cname in enumerate(classes):
                        class_rows.append({"group": group, "model": model, "comparison": f"{first}→{second}", "class": cname, "delta_logloss_contribution": float((delta * (y_eval == ci)).mean())})
            del rows
        extras = {"scope": ctx.scope, "purpose": config["purpose"], "split": config["split"], "re_scope": config["re_scope"] if re else None,
                  "final_test": config["purpose"] == "final_test", "extra_study": config["purpose"] == "extra_study",
                  "reserved_years": list(RESERVED_YEARS), "z": z}
        sections = [make_section("Fit summary 擬合摘要", ("group", "model", "variant", "n_train", "n_eval", "n_features", "classes", "logloss", "brier", "prior_logloss", "skill", "fit_seconds",
                                                          "rows_dropped_unmapped", "rows_dropped_missing"), fit_rows, ("group", "model", "variant"), backend, DIGITS)]
        if gain_rows:
            sections.append(make_section("Sequence gain 序列增益（base − full，正值＝序列資訊有用）", ("group", "model", "comparison", "delta_logloss", "lo", "hi", "se", "n_eval", "delta_ev_mse", "ev_lo", "ev_hi"),
                                         gain_rows, ("group", "model", "comparison"), backend, DIGITS))
            sections.append(make_section("Sequence gain by true class 各類別的增益貢獻", ("group", "model", "comparison", "class", "delta_logloss_contribution"), class_rows, ("group", "model", "comparison", "class"), backend, DIGITS))
        sections.append(make_section("Calibration 校準", ("group", "model", "variant", "class", "bin", "n", "mean_pred", "observed"), cal_rows, ("group", "model", "variant", "class", "bin"), backend, DIGITS))
        if cons_rows:
            sections.append(make_section("Model vs counted tables 與次數表一致性", ("group", "model", "variant", "class", "cells", "mean_abs_diff", "max_abs_diff", "share_abs_z_above_critical"),
                                         cons_rows, ("group", "model", "variant", "class"), backend, DIGITS))
        if ev_rows:
            sections.append(make_section("Expected-value check 期望得分檢查", ("group", "model", "variant", "n_value", "rmse", "mean_error", "fallback_lookups"), ev_rows, ("group", "model", "variant"), backend, DIGITS))
        artifacts = ()
        if coef_rows:
            artifacts = (ArtifactData("logistic_coefficients", "Standardized logistic-regression coefficients (holdout split only)", ("group", "variant", "class", "feature", "coef"),
                                      tuple({k: round(v, DIGITS) if isinstance(v, float) else v for k, v in r.items()} for r in coef_rows)),)
        return ResearchResult(sections=tuple(sections), extras=extras, artifacts=artifacts)

    @staticmethod
    def _split_features(config, variant):
        num = list(config["base_numeric"]); cat = list(config["base_categorical"])
        if variant == "full":
            num += config["sequence_numeric"]; cat += config["sequence_categorical"]
        elif variant.startswith("m"):
            j = int(variant[1:])
            num += config["sequence_numeric"]; cat += [f"prev{i}_pitch_type" for i in range(1, j + 1)] + ["prev1_description", "streak_cap"]
        return num, cat

    @staticmethod
    def _folds(config, years, games, group_kfold):
        import numpy as np

        if config["split"] == "holdout_years":
            tr = np.nonzero(np.isin(years, config["train_years"]))[0]; te = np.nonzero(np.isin(years, config["test_years"]))[0]
            if len(tr) == 0 or len(te) == 0:
                raise ConfigError("train or held-out rows are empty / 訓練或留出資料為空")
            return [(tr, te)]
        return [(tr, te) for tr, te in group_kfold(n_splits=config["n_folds"]).split(np.zeros(len(games)), groups=games)]


class SyntheticCheckMethod(ResearchMethod):
    """Known-answer self-check of the whole pipeline on synthetic worlds (zero decay, true decay, survival bias, placebo, sequence gain)."""

    kind = "synthetic_check"
    version = 2  # 2: pairs placebo checks, T2 placebo checks, pitcher world support
    label_zh = "合成資料已知答案檢查"
    label_en = "Synthetic known-answer check"
    requires_scope = False
    fields = (
        ConfigField("groups", "str_list", default=["oracle", "T0", "T1", "T2", "T3", "impossible", "placebo", "sequence_gain"], label_zh="檢查項目", label_en="Check groups"),
        ConfigField("n_pa", "int", default=300_000, minimum=300_000, label_zh="每個世界的打席數", label_en="Plate appearances per world"),
        ConfigField("seed", "int", default=1, label_zh="隨機種子", label_en="Random seed"),
    )

    def validate(self, config: dict[str, Any], ctx: ResearchContext) -> None:
        from .synthetic import CHECK_GROUPS

        if not config["groups"] or set(config["groups"]) - set(CHECK_GROUPS):
            raise ConfigError(f"groups must be a non-empty subset of {list(CHECK_GROUPS)} / groups 必須是 {list(CHECK_GROUPS)} 的非空子集")

    def run(self, ctx: ResearchContext, config: dict[str, Any]) -> ResearchResult:
        from .synthetic import run_checks

        rows = run_checks(n_pa=config["n_pa"], seed=config["seed"], groups=tuple(config["groups"]))
        return ResearchResult(
            sections=(make_section("Known-answer checks 已知答案檢查", ("world", "check", "expected", "observed", "passed"), rows, ("world", "check"), "duckdb", DIGITS),),
            extras={"all_passed": all(r["passed"] for r in rows), "failed": [f'{r["world"]}: {r["check"]}' for r in rows if not r["passed"]]})


for _method in (OutcomeModelMethod(), SyntheticCheckMethod()):
    register_method(_method)
