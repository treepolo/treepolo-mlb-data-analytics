"""Research methods of P6 plan 3.2-3.5: ``player_profiles`` (descriptor tables and their reliability) and ``league_model`` (one boosted-tree model
per handedness group, trained on the whole league, evaluated on unseen players and on a later season)."""
from __future__ import annotations

import base64
import zlib
from typing import Any


from ..analysis import Binary, Column, Filter, Limit, Literal, NamedExpr, OrderKey, PITCH_GRAIN, Project, Sort
from ..analysis.model import Boolean
from ..analysis.pitch_table import build_pitch_table
from . import modeling as md
from .config_schema import ConfigError, ConfigField
from .guards import PURPOSES, RESERVED_YEARS, check_purpose, scope_years
from .methods import ArtifactData, ResearchContext, ResearchMethod, ResearchResult, register_method
from .runner import run_checked
from .sections import make_section
from .sequencing_methods import BUNT_POLICY, CONFIDENCE, FILTERS, PURPOSE, _check_filters


class _Lazy:
    """Imports a heavy module (numpy, the boosting code) on first use so that starting the web app stays light."""

    def __init__(self, name: str):
        self._name = name

    def __getattr__(self, item: str):
        import importlib

        return getattr(importlib.import_module(self._name, __package__), item)


np = _Lazy("numpy")
gbm = _Lazy(".gbm")
lc = _Lazy(".league_core")
profiles = _Lazy(".profiles")
LEVELS = ("L0", "L1", "L2", "L3")

GROUPS = ("RvR", "RvL", "LvR", "LvL")
DIGITS = 8
EXTRA_RAW = ("pfx_x", "pfx_z", "release_spin_rate", "release_extension", "release_pos_x", "release_pos_z", "arm_angle", "game_date")
FETCH = ("pitch_uid", "game_pk", "game_year", "game_date", "pitcher", "batter", "hand_group", "pitch_type", "outcome", "zone", "pitch_index_in_pa", "events",
         "release_speed", "release_spin_rate", "spin_axis", "pfx_x", "pfx_z", "release_pos_x", "release_pos_z", "release_extension", "arm_angle", "plate_x",
         "plate_z", "balls", "strikes", "outs_when_up", "bases", "prev1_pitch_type", "streak_pos")
STRING_COLUMNS = ("hand_group", "pitch_type", "outcome", "prev1_pitch_type")


def fetch_columns(ctx: ResearchContext, config: dict[str, Any], groups, years, re_by_state=None) -> dict[str, np.ndarray]:
    """Pitch-table rows of ``groups`` as columnar arrays, one (group, season) query at a time so the peak memory stays low.

    The pitch table (bunt policy, sequence columns, filters) is the same one P3-P5 use; ``group`` and ``game_order`` are added here."""

    table = build_pitch_table(ctx.source_node(), bunt_policy=config["bunt_policy"], memory=1, re_by_state=re_by_state, mirror=False, extra_columns=EXTRA_RAW,
                              filters=config["filters"])
    fetch = FETCH + (("pitch_value", "state_code") if re_by_state is not None else ())        # run values need the run-expectancy table
    chunks: dict[str, list[np.ndarray]] = {c: [] for c in fetch}
    for group in groups:
        for year in years:
            cond = Boolean("and", (Binary(Column("hand_group"), "=", Literal(group)), Binary(Column("game_year"), "=", Literal(int(year)))))
            node = Limit(Sort(Project(Filter(table, cond), tuple(NamedExpr(c, Column(c)) for c in fetch), PITCH_GRAIN),
                              (OrderKey(Column("game_pk")), OrderKey(Column("pitch_uid")))), config["max_rows"] + 1)
            result = run_checked(ctx, node)
            if len(result.rows) > config["max_rows"]:
                raise ConfigError(f"Group {group} {year} has more than max_rows rows / 分組 {group} {year} 的列數超過上限")
            for c in fetch:
                values = [r[c] for r in result.rows]
                if c in STRING_COLUMNS:
                    chunks[c].append(np.array(["" if v is None else str(v) for v in values]))
                elif c in ("events", "game_date"):
                    chunks[c].append(np.array(values, dtype=object))
                elif c in ("game_pk", "game_year", "pitcher", "batter", "bases"):
                    chunks[c].append(np.array([0 if v is None else int(v) for v in values], dtype=np.int64))
                elif c == "pitch_uid":
                    chunks[c].append(np.array([str(v) for v in values]))
                else:
                    chunks[c].append(np.array([np.nan if v is None else float(v) for v in values], dtype=np.float64))
            del result
    cols = {c: (np.concatenate(v) if v else np.array([])) for c, v in chunks.items()}
    cols["group"] = cols.pop("hand_group")
    dates = np.array([str(v) for v in cols["game_date"]]); key = np.char.add(np.char.add(dates, "|"), cols["game_pk"].astype(str))
    _, cols["game_order"] = np.unique(key, return_inverse=True)       # chronological rank of every game (date, then game id)
    cols["game_order"] = cols["game_order"].astype(np.int64)
    # one game has exactly one rank, but two games on the same day must not share one: rank by the game id within a date
    cols["streak_pos"] = np.where(np.isnan(cols["streak_pos"]), 1.0, cols["streak_pos"])
    cols["prev1_pitch_type"] = np.where(cols["prev1_pitch_type"] == "", "none", cols["prev1_pitch_type"])
    return cols


def _check_scope_and_split(config, ctx, *, purpose_only: bool = False):
    purpose = config["purpose"]; years = set(scope_years(ctx.scope)); reserved = set(RESERVED_YEARS)
    train, test = set(config["train_years"]), set(config["test_years"])
    if purpose == "tuning":
        check_purpose(ctx.scope, "tuning")
    if purpose == "extra_study" and config["split"] != "cold_only":
        raise ConfigError("extra_study has no clean test season: use split=cold_only / extra_study 沒有乾淨的檢驗球季，請用 cold_only")
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


class PlayerProfilesMethod(ResearchMethod):
    """Descriptor tables (P6 plan 3.2, 3.3): how reliable are the shrunken player descriptors, and how are they distributed."""

    kind = "player_profiles"
    version = 1
    label_zh = "球員描述特徵與可靠度"
    label_en = "Player descriptors and their reliability"
    requires_scope = True
    fields = (
        PURPOSE, BUNT_POLICY, FILTERS,
        ConfigField("groups", "str_list", default=list(GROUPS), label_zh="分組", label_en="Groups"),
        ConfigField("min_pitches", "int", default=200, minimum=10, label_zh="可靠度檢查的最小球數", label_en="Minimum pitches for the reliability check"),
        ConfigField("seed", "int", default=20240601, label_zh="隨機種子", label_en="Random seed"),
        ConfigField("max_rows", "int", default=1_500_000, minimum=1000, label_zh="每組每球季列數上限", label_en="Row cap per group and season"),
    )

    def validate(self, config: dict[str, Any], ctx: ResearchContext) -> None:
        check_purpose(ctx.scope, config["purpose"]) if config["purpose"] != "extra_study" else None
        if not config["groups"] or set(config["groups"]) - set(GROUPS):
            raise ConfigError(f"groups must be a non-empty subset of {list(GROUPS)} / groups 必須是 {list(GROUPS)} 的非空子集")
        _check_filters(config["filters"])

    def run(self, ctx: ResearchContext, config: dict[str, Any]) -> ResearchResult:
        years = scope_years(ctx.scope)
        cols = fetch_columns(ctx, config, config["groups"], years)
        profile = profiles.ProfileModel().fit(cols)
        ind_names = profile.indicator_names
        rows_k = []
        for side, rates in (("pitcher", profiles.PITCHER_RATES), ("batter", profiles.BATTER_RATES)):
            for name, _, _ in rates:
                rows_k.append({"metric": name, "side": side, "league_rate": profile.league[name], "k_all": profile.k_all[name], "k_group": profile.k_group[name]})
        for j, name in enumerate(profiles.ARSENAL_MEANS):
            rows_k.append({"metric": f"arsenal:{name}", "side": "pitcher", "league_rate": None, "k_all": float(profile.k_arsenal[j]), "k_group": None})
        # split-half reliability: raw per-player rates in two random halves of the games
        rng = np.random.default_rng(config["seed"])
        games = np.unique(cols["game_pk"]); half = dict(zip(games.tolist(), (rng.random(len(games)) < 0.5).tolist()))
        side_of_row = np.array([half[g] for g in cols["game_pk"].tolist()])
        from .profiles import _indicators

        ind, _ = _indicators(cols)
        reliability = []
        for side, rates, key in (("pitcher", profiles.PITCHER_RATES, "pitcher"), ("batter", profiles.BATTER_RATES, "batter")):
            ids = cols[key]
            for name, num, den in rates:
                est = []
                for h in (True, False):
                    m = side_of_row == h
                    u, inv = np.unique(ids[m], return_inverse=True)
                    n_ = np.bincount(inv, weights=ind[num][m], minlength=len(u)); d_ = np.bincount(inv, weights=ind[den][m], minlength=len(u))
                    est.append(dict(zip(u.tolist(), zip(n_.tolist(), d_.tolist()))))
                common = [p for p in est[0] if p in est[1] and est[0][p][1] >= config["min_pitches"] / 10 and est[1][p][1] >= config["min_pitches"] / 10]
                if len(common) >= 10:
                    a = np.array([est[0][p][0] / est[0][p][1] for p in common]); b = np.array([est[1][p][0] / est[1][p][1] for p in common])
                    r = float(np.corrcoef(a, b)[0, 1]) if a.std() > 0 and b.std() > 0 else float("nan")
                else:
                    r = float("nan")
                reliability.append({"metric": name, "side": side, "players": len(common), "split_half_r": r})
        coverage = [{"item": "rows", "value": int(len(cols["game_pk"]))}, {"item": "pitchers", "value": int(len(np.unique(cols["pitcher"])))},
                    {"item": "batters", "value": int(len(np.unique(cols["batter"])))}, {"item": "games", "value": int(len(games))}]
        sections = (
            make_section("Coverage 範圍", ("item", "value"), coverage, ("item",), "duckdb", DIGITS),
            make_section("Prior strengths 先驗強度（收縮）", ("metric", "side", "league_rate", "k_all", "k_group"), rows_k, ("metric", "side"), "duckdb", DIGITS),
            make_section("Split-half reliability 折半可靠度", ("metric", "side", "players", "split_half_r"), reliability, ("metric", "side"), "duckdb", DIGITS),
        )
        return ResearchResult(sections=sections, extras={"scope": ctx.scope, "purpose": config["purpose"], "indicators": ind_names})


class LeagueModelMethod(ResearchMethod):
    """One boosted-tree model per handedness group on the whole league; player descriptors as inputs (P6 plan 3.4, 4.6)."""

    kind = "league_model"
    version = 1
    label_zh = "全聯盟模型（球員描述特徵）"
    label_en = "League model with player descriptors"
    requires_scope = True
    fields = (
        PURPOSE, BUNT_POLICY, FILTERS, CONFIDENCE,
        ConfigField("groups", "str_list", default=list(GROUPS), label_zh="分組（各自訓練，不合併）", label_en="Groups (fitted separately, never pooled)"),
        ConfigField("target", "choice", default="outcome", choices=("outcome", "usage"), label_zh="預測目標", label_en="Target",
                    help_zh="outcome：八類結果；usage：投手選哪種球（配球傾向）。", help_en="outcome: 8 result classes; usage: which pitch type is thrown."),
        ConfigField("levels", "str_list", default=list(LEVELS), label_zh="特徵階梯", label_en="Feature ladder",
                    help_zh="L0 只有情境與球種；L1 加投手描述；L2 加打者描述；L3 兩者。", help_en="L0 situation and pitch type; L1 + pitcher; L2 + batter; L3 both."),
        ConfigField("backend", "choice", default="hgb", choices=("xgboost", "hgb"), label_zh="訓練後端", label_en="Backend",
                    help_zh="xgboost（選用依賴，可用 GPU）或 hgb（scikit-learn，CPU）。", help_en="xgboost (optional, GPU-capable) or hgb (scikit-learn, CPU)."),
        ConfigField("device", "choice", default="auto", choices=("auto", "cpu", "cuda"), label_zh="裝置", label_en="Device",
                    help_zh="auto：有 GPU 就用；不影響 run key，但會記錄。", help_en="auto uses a GPU when present."),
        ConfigField("max_depth", "int", default=6, minimum=1, maximum=30, label_zh="樹深", label_en="Tree depth"),
        ConfigField("min_leaf", "int", default=200, minimum=1, label_zh="葉節點最小樣本", label_en="Minimum samples per leaf"),
        ConfigField("learning_rate", "float", default=0.1, minimum=1e-4, maximum=1.0, label_zh="學習率", label_en="Learning rate"),
        ConfigField("l2", "float", default=1.0, minimum=0.0, label_zh="L2 正則化", label_en="L2 regularisation"),
        ConfigField("max_iter", "int", default=300, minimum=1, label_zh="最多棵數", label_en="Maximum boosting rounds"),
        ConfigField("early_stopping_rounds", "int", default=20, minimum=1, label_zh="早停輪數（xgboost，依比賽分組的驗證集）", label_en="Early-stopping rounds (xgboost)"),
        ConfigField("bagging_reps", "int", default=1, minimum=1, maximum=200, label_zh="袋裝成員數（比賽層級 Poisson 重抽樣）", label_en="Bagging members (game-level Poisson bootstrap)"),
        ConfigField("bag_levels", "str_list", default=["L3"], label_zh="要袋裝的階梯", label_en="Levels that are bagged",
                    help_zh="其餘階梯各擬合一個模型（省算力）。", help_en="The other levels get a single fit."),
        ConfigField("cold_by", "str_list", default=["pitcher", "batter"], label_zh="未見過球員的評估", label_en="Unseen-player evaluation",
                    help_zh="pitcher、batter：依球員分折（被留出的球員完全沒進訓練）。空＝不做。", help_en="Folds over players; empty = skip."),
        ConfigField("cold_folds", "int", default=5, minimum=2, maximum=20, label_zh="依球員分折數", label_en="Folds over players"),
        ConfigField("calibration", "bool", default=False, label_zh="等距迴歸校準", label_en="Isotonic calibration",
                    help_zh="用未見過球員的外折預測校準，再套到留出球季。", help_en="Fitted on the unseen-player out-of-fold predictions."),
        ConfigField("split", "choice", default="holdout_years", choices=("holdout_years", "cold_only"), label_zh="切分方式", label_en="Split",
                    help_zh="holdout_years：訓練球季對留出球季；cold_only：只做未見過球員（extra_study 用）。", help_en="cold_only has no held-out season (extra studies)."),
        ConfigField("train_years", "int_list", default=[2023], unique_sorted=True, label_zh="訓練球季", label_en="Training seasons"),
        ConfigField("test_years", "int_list", default=[2024], unique_sorted=True, label_zh="留出球季", label_en="Held-out seasons"),
        ConfigField("class_merge", "json", default=md.DEFAULT_CLASS_MERGE, label_zh="結果類別對照", label_en="Outcome class map"),
        ConfigField("min_class_count", "int", default=50, minimum=1, label_zh="類別最小訓練樣本", label_en="Minimum training rows per class"),
        ConfigField("min_cell_swings", "int", default=30, minimum=1, label_zh="球種對比的最小揮棒數", label_en="Minimum swings per (player, type) cell"),
        ConfigField("min_gain", "float", default=0.0003, minimum=0.0, label_zh="G1a 最小對數損失增益", label_en="G1a minimum log-loss gain",
                    help_zh="小樣本（測試、快速層）模型本身的隨機變異大，需調高；真實全量資料用預設。", help_en="Raise for small samples where model variance dominates; real full-size data uses the default."),
        ConfigField("bootstrap_reps", "int", default=200, minimum=20, maximum=5000, label_zh="Bootstrap 次數", label_en="Bootstrap replicates"),
        ConfigField("seed", "int", default=20240601, label_zh="隨機種子", label_en="Random seed"),
        ConfigField("save_package", "bool", default=False, label_zh="保存模型包", label_en="Save the model package",
                    help_zh="把袋裝成員存成附件，供之後的 pitch_policy 與 pitch_query 使用。", help_en="Members are stored as an artifact for pitch_policy / pitch_query."),
        ConfigField("max_rows", "int", default=1_500_000, minimum=1000, label_zh="每組每球季列數上限", label_en="Row cap per group and season"),
    )

    def validate(self, config: dict[str, Any], ctx: ResearchContext) -> None:
        if not config["groups"] or set(config["groups"]) - set(GROUPS):
            raise ConfigError(f"groups must be a non-empty subset of {list(GROUPS)} / groups 必須是 {list(GROUPS)} 的非空子集")
        if not config["levels"] or set(config["levels"]) - set(LEVELS) or "L0" not in config["levels"]:
            raise ConfigError(f"levels must be a subset of {list(LEVELS)} containing L0 / levels 必須含 L0 且是 {list(LEVELS)} 的子集")
        if set(config["cold_by"]) - {"pitcher", "batter"}:
            raise ConfigError("cold_by may contain pitcher and batter only / cold_by 只能是 pitcher、batter")
        if set(config["bag_levels"]) - set(LEVELS):
            raise ConfigError("bag_levels must be ladder levels / bag_levels 必須是階梯")
        mapping = config["class_merge"]
        if not isinstance(mapping, dict) or not mapping or not all(isinstance(k, str) and isinstance(v, str) for k, v in mapping.items()):
            raise ConfigError("class_merge must be a non-empty object of strings / class_merge 必須是非空的字串對照")
        _check_filters(config["filters"])
        _check_scope_and_split(config, ctx)
        if config["split"] == "cold_only" and not config["cold_by"]:
            raise ConfigError("cold_only needs cold_by / cold_only 需要 cold_by")
        try:
            gbm.resolve_device(config["backend"], config["device"])
        except ValueError as exc:
            raise ConfigError(str(exc)) from exc

    def run(self, ctx: ResearchContext, config: dict[str, Any]) -> ResearchResult:
        years = scope_years(ctx.scope)
        cols = fetch_columns(ctx, config, config["groups"], years)
        train_years = list(config["train_years"]) if config["split"] == "holdout_years" else years
        in_train = np.isin(cols["game_year"], train_years)
        in_test = np.isin(cols["game_year"], config["test_years"]) if config["split"] == "holdout_years" else np.zeros(len(in_train), dtype=bool)
        profile = profiles.ProfileModel().fit(lc.take(cols, np.nonzero(in_train)[0]))        # the training period only, all groups
        device = gbm.resolve_device(config["backend"], config["device"])
        params = {k: config[k] for k in ("max_depth", "min_leaf", "learning_rate", "l2", "max_iter", "early_stopping_rounds")}
        fit_rows, gain_rows, contrast_rows, cal_rows, gate_rows, artifacts = [], [], [], [], [], []
        classes = None
        for group in config["groups"]:
            mask = cols["group"] == group
            tr_idx = np.nonzero(mask & in_train)[0]; te_idx = np.nonzero(mask & in_test)[0]
            if len(tr_idx) == 0:
                raise ConfigError(f"Group {group} has no training rows / 分組 {group} 沒有訓練資料")
            ctr = lc.take(cols, tr_idx); cte = lc.take(cols, te_idx) if len(te_idx) else None
            self._check_classes(ctr, config, group)
            res = self._evaluate(ctr, cte, profile, config, device, params)
            classes = res["classes"]
            for r in res["fit"]:
                fit_rows.append({"group": group, **r, "device": device})
            gain_rows += [{"group": group, **r} for r in res["gain"]]
            contrast_rows += [{"group": group, **r} for r in res["contrast"]]
            cal_rows += [{"group": group, **r} for r in res["calibration"]]
            gate_rows += self._gates(group, res, config["min_gain"])
            if config["save_package"]:
                artifacts.append(self._package(group, res, config))
        extras = {"scope": ctx.scope, "purpose": config["purpose"], "split": config["split"], "backend": config["backend"], "device": device, "classes": classes,
                  "target": config["target"], "train_years": train_years, "test_years": list(config["test_years"]) if config["split"] == "holdout_years" else [],
                  "final_test": config["purpose"] == "final_test", "extra_study": config["purpose"] == "extra_study", "reserved_years": list(RESERVED_YEARS),
                  "xgboost_available": gbm.xgboost_available(), "n_pitchers": int(len(np.unique(cols["pitcher"]))), "n_batters": int(len(np.unique(cols["batter"])))}
        sections = [
            make_section("Fit summary 擬合摘要", ("group", "mode", "level", "n_train", "n_eval", "logloss", "raw_logloss", "brier", "prior_logloss", "fit_seconds", "n_iter",
                                                "member_logloss_sd", "backend", "device"), fit_rows, ("group", "mode", "level"), "duckdb", DIGITS),
            make_section("Level gain 階梯增益（L0 − 各階，正值＝描述特徵有用）", ("group", "mode", "comparison", "delta_logloss", "lo", "hi", "se", "n_eval"), gain_rows,
                         ("group", "mode", "comparison"), "duckdb", DIGITS),
        ]
        if contrast_rows:
            sections.append(make_section("Type contrast 球種對比預測（G1b）", ("group", "mode", "side", "comparison", "mse_base", "mse_other", "gain", "lo", "hi", "units"), contrast_rows,
                                         ("group", "mode", "side", "comparison"), "duckdb", DIGITS))
        if gate_rows:
            sections.append(make_section("Gates 閘門（G1a、G1b；投手側、打者側各自）", ("group", "side", "gate", "mode", "value", "lo", "passed"), gate_rows, ("group", "side", "gate", "mode"), "duckdb", DIGITS))
        sections.append(make_section("Calibration 校準", ("group", "mode", "level", "class", "bin", "n", "mean_pred", "observed"), cal_rows, ("group", "mode", "level", "class", "bin"), "duckdb", DIGITS))
        return ResearchResult(sections=tuple(sections), extras=extras, artifacts=tuple(artifacts))

    # ------------------------------------------------------------------------------------------------ internals
    def _evaluate(self, ctr, cte, profile, config, device, params):
        reps_map = {lv: (config["bagging_reps"] if lv in config["bag_levels"] else 1) for lv in config["levels"]}
        return lc.evaluate_group(ctr, cte, profile=profile, target=config["target"], levels=config["levels"], backend=config["backend"], device=device, params=params,
                                 reps=max(reps_map.values()), reps_by_level=reps_map, seed=config["seed"], merge=config["class_merge"], bootstrap_reps=config["bootstrap_reps"],
                                 cold_by=config["cold_by"], cold_folds=config["cold_folds"], calibrate=config["calibration"], min_cell_swings=config["min_cell_swings"],
                                 confidence=config["confidence"])

    @staticmethod
    def _check_classes(ctr, config, group):
        if config["target"] != "outcome":
            return
        y, classes, ok = lc.encode_outcome(ctr["outcome"], config["class_merge"])
        counts = np.bincount(y[ok], minlength=len(classes))
        small = [classes[i] for i in range(len(classes)) if counts[i] < config["min_class_count"]]
        if small:
            raise ConfigError(f"Classes {small} have fewer than min_class_count training rows in group {group}; merge them with class_merge / 類別 {small} 訓練樣本不足，請用 class_merge 合併")

    @staticmethod
    def _gates(group: str, res: dict[str, Any], min_gain: float = 0.0003) -> list[dict[str, Any]]:
        """G1a (level gain on unseen players) and G1b (type contrast on unseen players) per side, with the plan 4.6 thresholds."""

        out = []
        sides = (("pitcher", "cold_pitcher", ("L1", "L3")), ("batter", "cold_batter", ("L2", "L3")))
        for side, mode, levels in sides:
            for lv in levels:
                g = next((r for r in res["gain"] if r["mode"] == mode and r["comparison"] == f"L0→{lv}"), None)
                if g is not None:
                    out.append({"group": group, "side": f"{side}:{lv}", "gate": "G1a", "mode": mode, "value": g["delta_logloss"], "lo": g["lo"],
                                "passed": int(g["delta_logloss"] >= min_gain and g["lo"] > 0)})
                c = next((r for r in res["contrast"] if r["mode"] == mode and r["side"] == side and r["comparison"] == f"L0→{lv}"), None)
                if c is not None:
                    out.append({"group": group, "side": f"{side}:{lv}", "gate": "G1b", "mode": mode, "value": c["gain"], "lo": c["lo"], "passed": int(c["lo"] > 0)})
                u = next((r for r in res["contrast"] if r["mode"] == "season_unseen" and r["side"] == side and r["comparison"] == f"L0→{lv}"), None)
                if u is not None:       # players of the held-out season that the training season never saw: reference only (few players), passed = -1 means "not a gate"
                    out.append({"group": group, "side": f"{side}:{lv}", "gate": "G1b_ref", "mode": "season_unseen", "value": u["gain"], "lo": u["lo"], "passed": -1})
        return out

    @staticmethod
    def _package(group: str, res: dict[str, Any], config: dict[str, Any]) -> ArtifactData:
        """Bagged members of every level as base64 chunks (an artifact is a table; the bytes are split to keep the rows small)."""

        rows = []
        for lv, members in res["models"].items():
            for i, m in enumerate(members):
                blob = base64.b64encode(zlib.compress(m.to_bytes())).decode("ascii")
                for part in range(0, len(blob), 400_000):
                    rows.append({"level": lv, "member": i, "part": part // 400_000, "payload": blob[part:part + 400_000]})
        return ArtifactData(f"model_package_{group}", f"Boosted-tree members of group {group} (base64 of zlib of pickled FittedGbm)", ("level", "member", "part", "payload"), tuple(rows))


for _method in (PlayerProfilesMethod(), LeagueModelMethod()):
    register_method(_method)
