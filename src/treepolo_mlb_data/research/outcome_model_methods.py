"""Research method ``outcome_model_train``: train a model of what happens on each pitch, with the bias controls the user chose, and report numbers only.

It replaces the role ``league_model`` had in the one-click flow (``league_model`` stays, old runs can still be read). Any year combination can be trained and tested: this method
applies no reserved-season rule. Every section holds numbers, intervals and sample sizes; nothing is judged as passed or failed.
"""
from __future__ import annotations

import base64
import zlib
from typing import Any

from .config_schema import ConfigError, ConfigField
from .guards import scope_years
from .lazy import Lazy
from .league_methods import DIGITS, GROUPS, LeagueModelMethod, fetch_columns
from .methods import ArtifactData, ResearchContext, ResearchMethod, ResearchResult, register_method
from .outcome_models import controls as ctl
from .sections import make_section
from .sequencing_methods import BUNT_POLICY, FILTERS, _check_filters

np = Lazy("numpy")
lc = Lazy(".league_core")
md = Lazy(".modeling")
anc = Lazy(".anchor")
pc = Lazy(".policy_core")
om = Lazy(".outcome_models")
gbm = Lazy(".gbm")
dz = Lazy(".outcome_models.design")

SPLITS = ("holdout_years", "cold_only")
SETTING_NAMES = tuple(f.name for f in ctl.CONTROL_FIELDS + ctl.PARAM_FIELDS)


def pack_bytes(data: bytes) -> list[dict[str, Any]]:
    blob = base64.b64encode(zlib.compress(data)).decode("ascii")
    return [{"part": i // 400_000, "payload": blob[i:i + 400_000]} for i in range(0, len(blob), 400_000)]


def unpack_bytes(rows) -> bytes:
    return zlib.decompress(base64.b64decode("".join(r["payload"] for r in sorted(rows, key=lambda r: r["part"]))))


def load_model(rows):
    """The model stored in an ``outcome_model_{group}`` artifact."""

    return om.OutcomeModel.from_bytes(unpack_bytes(rows))


def model_settings(config: dict[str, Any]) -> dict[str, Any]:
    """The part of the method's settings a model reads."""

    out = {k: config[k] for k in SETTING_NAMES}
    out.update(class_merge=config["class_merge"], seed=config["seed"])
    return out


def baseline_settings(settings: dict[str, Any]) -> dict[str, Any]:
    """The same model with every bias control off: what the controls are compared with."""

    return {**settings, **ctl.default_controls()}


def controls_active(settings: dict[str, Any]) -> bool:
    return any(settings[k] != ctl.CONTROL_DEFAULTS[k] for k in ctl.CONTROL_NAMES)


class OutcomeModelTrainMethod(ResearchMethod):
    kind = "outcome_model_train"
    version = 1
    label_zh = "訓練結果模型（可消除偏差、可選多種模型）"
    label_en = "Train an outcome model (bias controls, several models)"
    requires_scope = True
    fields = (
        BUNT_POLICY, FILTERS,
        ConfigField("groups", "str_list", default=list(GROUPS), label_zh="分組（各自訓練）", label_en="Groups (trained separately)"),
        ConfigField("model", "choice", default="gbm", choices=ctl.MODEL_KINDS, label_zh="模型", label_en="Model",
                    help_zh="logistic：邏輯迴歸；gbm：梯度提升樹；mixed、nn_embed、sequence 需要 PyTorch。", help_en="logistic, gbm; mixed, nn_embed and sequence need PyTorch."),
    ) + ctl.CONTROL_FIELDS + ctl.PARAM_FIELDS + (
        ConfigField("split", "choice", default="holdout_years", choices=SPLITS, label_zh="切分方式", label_en="Split",
                    help_zh="holdout_years：訓練年份對留出年份；cold_only：只做沒見過的球員（用範圍內全部年份）。", help_en="cold_only trains on every year in scope and only tests unseen players."),
        ConfigField("train_years", "int_list", default=[2023], unique_sorted=True, label_zh="訓練年份", label_en="Training years"),
        ConfigField("test_years", "int_list", default=[2024], unique_sorted=True, label_zh="留出年份", label_en="Held-out years"),
        ConfigField("cold_by", "str_list", default=["pitcher", "batter"], label_zh="沒見過的球員的評估", label_en="Unseen-player evaluation",
                    help_zh="pitcher、batter：依球員分折，被留出的球員完全沒進訓練。空＝不做。", help_en="Folds over players; empty = skip."),
        ConfigField("cold_folds", "int", default=3, minimum=2, maximum=20, label_zh="依球員分折數", label_en="Folds over players"),
        ConfigField("class_merge", "json", default=None, label_zh="結果類別對照（預設同前）", label_en="Outcome class map (default as before)"),
        ConfigField("min_class_count", "int", default=50, minimum=1, label_zh="類別最小訓練樣本", label_en="Minimum training rows per class"),
        ConfigField("compare_baseline", "bool", default=True, label_zh="另訓練一份「全部控制都關掉」的對照", label_en="Also train a no-controls baseline",
                    help_zh="用來算球員描述／效應的預測增益；多花一份訓練時間。", help_en="Needed for the predictive gain of the controls; costs one more fit."),
        ConfigField("replication_members", "int", default=3, minimum=0, maximum=50, label_zh="換年份重看：每個年份單獨訓練的成員數（0＝不做）", label_en="Per-year replication: members per single year (0 = skip)",
                    help_zh="訓練年份有兩個以上時，每個年份各自單獨訓練，並排列出各年份的球種效果。", help_en="With two or more training years, each year is trained alone and the pitch effects are listed side by side."),
        ConfigField("effect_sample", "int", default=20000, minimum=100, label_zh="算球種效果用的列數", label_en="Rows used for the pitch effects"),
        ConfigField("support_min_share", "float", default=0.05, minimum=0.0, maximum=1.0, label_zh="支持規則：最小份額", label_en="Support rule: minimum share"),
        ConfigField("support_min_n", "int", default=300, minimum=1, label_zh="支持規則：最小球數", label_en="Support rule: minimum pitches"),
        ConfigField("confidence", "float", default=0.95, minimum=0.5, maximum=0.999, label_zh="區間的信賴水準", label_en="Confidence level"),
        ConfigField("bootstrap_reps", "int", default=100, minimum=20, maximum=5000, label_zh="Bootstrap 次數", label_en="Bootstrap replicates"),
        ConfigField("save_package", "bool", default=True, label_zh="保存模型包", label_en="Save the model package", help_zh="決策引擎與情境查詢要用。", help_en="The decision engine and the situation query read it."),
        ConfigField("seed", "int", default=20240601, label_zh="隨機種子", label_en="Random seed"),
        ConfigField("max_rows", "int", default=1_500_000, minimum=1000, label_zh="每組每球季列數上限", label_en="Row cap per group and season"),
    )

    # ------------------------------------------------------------------------------------------------ validation
    def validate(self, config: dict[str, Any], ctx: ResearchContext) -> None:
        if not config["groups"] or set(config["groups"]) - set(GROUPS):
            raise ConfigError(f"groups must be a non-empty subset of {list(GROUPS)} / groups 必須是 {list(GROUPS)} 的非空子集")
        if set(config["cold_by"]) - {"pitcher", "batter"}:
            raise ConfigError("cold_by may contain pitcher and batter only / cold_by 只能是 pitcher、batter")
        if config["class_merge"] is None:
            from . import modeling

            config["class_merge"] = dict(modeling.DEFAULT_CLASS_MERGE)
        mapping = config["class_merge"]
        if not isinstance(mapping, dict) or not mapping or not all(isinstance(k, str) and isinstance(v, str) for k, v in mapping.items()):
            raise ConfigError("class_merge must be a non-empty object of strings / class_merge 必須是非空的字串對照")
        _check_filters(config["filters"])
        om.get_model_class(config["model"])                                   # unknown, or needs PyTorch that is not installed
        ctl.validate_controls(config, model=config["model"], backend=config["backend"])
        if config["model"] == "gbm":
            try:
                gbm.resolve_device(config["backend"], config["device"])
            except ValueError as exc:
                raise ConfigError(str(exc)) from exc
        if config["split"] == "holdout_years":
            if "game_years" not in ctx.scope:
                raise ConfigError("holdout_years needs a scope made of game_years / holdout_years 需要以 game_years 指定範圍")
            years = set(scope_years(ctx.scope)); train, test = set(config["train_years"]), set(config["test_years"])
            if not train or not test or train & test or not (train | test) <= years:
                raise ConfigError("train_years and test_years must be non-empty, disjoint and inside the scope / 訓練與留出年份必須非空、不重疊且在範圍內")
        elif not config["cold_by"]:
            raise ConfigError("cold_only needs cold_by / cold_only 需要 cold_by")

    # ------------------------------------------------------------------------------------------------ run
    def run(self, ctx: ResearchContext, config: dict[str, Any]) -> ResearchResult:
        years = scope_years(ctx.scope)
        cols_all = fetch_columns(ctx, config, config["groups"], years)
        progress = ctx.progress
        out: dict[str, list] = {k: [] for k in ("fit", "curve", "calibration", "common", "effects", "replication", "gain", "controls")}
        artifacts, comparisons, classes = [], {"pitch_effects": {}}, None
        for gi, group in enumerate(config["groups"]):
            sel = np.nonzero(cols_all["group"] == group)[0]
            if len(sel) == 0:
                raise ConfigError(f"Group {group} has no rows / 分組 {group} 沒有資料")
            cols = lc.take(cols_all, sel)
            if progress:
                progress("outcome_model_train", gi / len(config["groups"]), f"group {group}")
            classes = self._group(ctx, config, group, cols, out, artifacts, comparisons)
        loss_cols = tuple(f"loss_{c}" for c in (classes or []))
        sections = (
            make_section("Fit summary 擬合摘要", ("group", "role", "mode", "n_train", "n_eval", "logloss", "brier", "prior_logloss", "fit_seconds", "n_iter", "hit_cap") + loss_cols, out["fit"],
                         ("group", "role", "mode"), "duckdb", DIGITS),
            make_section("Learning curve 學習曲線（每 10 回合一點）", ("group", "iteration", "train_loss", "valid_loss"), out["curve"], ("group", "iteration"), "duckdb", DIGITS),
            make_section("Calibration 校準", ("group", "mode", "class", "bin", "n", "mean_pred", "observed"), out["calibration"], ("group", "mode", "class", "bin"), "duckdb", DIGITS),
            make_section("Common sense 常識對照（聯盟平均球員：預測對實際）", ("group", "pitch_type", "n", "whiff_pred", "whiff_obs", "hr_pred", "hr_obs", "in_play_pred", "in_play_obs"), out["common"],
                         ("group", "pitch_type"), "duckdb", DIGITS),
            make_section("Pitch effects 球種效果（聯盟平均球員，相對一般配球的各類結果機率差；全部列出）", ("group", "phase", "action", "class", "effect", "lo", "hi", "n"), out["effects"],
                         ("group", "phase", "action", "class"), "duckdb", DIGITS),
            make_section("Replication 換年份重看（每個年份單獨訓練的球種效果）", ("group", "year", "phase", "action", "class", "effect", "lo", "hi", "n"), out["replication"],
                         ("group", "year", "phase", "action", "class"), "duckdb", DIGITS),
            make_section("Controls 偏差控制的實際做法", ("group", "control", "setting", "how", "model", "backend"), out["controls"], ("group", "control"), "duckdb", DIGITS),
            make_section("Effect gain 球員描述或效應的預測增益（對「全部控制都關掉」的對照；沒見過的球員、沒用過的年份；正值＝比較準）", ("group", "mode", "comparison", "delta_logloss", "lo", "hi", "se", "n"),
                         out["gain"], ("group", "mode", "comparison"), "duckdb", DIGITS),
        )
        extras = {"scope": ctx.scope, "model": config["model"], "split": config["split"], "classes": classes, "train_years": self._years(config, years)[0], "test_years": self._years(config, years)[1],
                  "controls": {k: config[k] for k in ctl.CONTROL_NAMES}, "comparisons": {k: v for k, v in comparisons.items() if v}, "xgboost_available": gbm.xgboost_available()}
        return ResearchResult(sections=sections, extras=extras, artifacts=tuple(artifacts))

    @staticmethod
    def _years(config, years):
        if config["split"] == "holdout_years":
            return list(config["train_years"]), list(config["test_years"])
        return list(years), []

    # ------------------------------------------------------------------------------------------------ one group
    def _group(self, ctx, config, group, cols, out, artifacts, comparisons):
        settings = model_settings(config)
        train_years, test_years = self._years(config, scope_years(ctx.scope))
        year = np.asarray(cols["game_year"])
        tr_idx = np.nonzero(np.isin(year, train_years))[0]
        if len(tr_idx) == 0:
            raise ConfigError(f"Group {group} has no training rows / 分組 {group} 沒有訓練資料")
        ctr = lc.take(cols, tr_idx)
        cte = lc.take(cols, np.nonzero(np.isin(year, test_years))[0]) if test_years else None
        LeagueModelMethod._check_classes(ctr, {**config, "target": "outcome"}, group)
        _, classes, _ = lc.encode_outcome(ctr["outcome"], config["class_merge"])
        K = len(classes)
        kind = config["model"]

        def fit_model(rows, st, reps=None):
            model = om.new_model(kind)
            model.fit(rows, None, {**st, **({} if reps is None else {"bagging_reps": reps})}, ctx.progress, classes=classes)
            return model

        main = fit_model(ctr, settings)
        active = controls_active(settings) or main.profile is not None
        base = fit_model(ctr, baseline_settings(settings)) if (config["compare_baseline"] and active) else None
        sub_tr, ytr = main._encode(ctr, main.settings, fixed_classes=True)
        prior_tr = np.bincount(ytr, minlength=K) / max(len(ytr), 1)
        out["controls"] += [{"group": group, **r} for r in ctl.describe_controls(settings, model=kind, backend=settings["backend"])]
        for i, row in enumerate(main.info.get("trace", [])):
            out["curve"].append({"group": group, "iteration": row[0], "train_loss": row[1], "valid_loss": row[2]})
        losses: dict[str, dict[str, Any]] = {"model": {}, "baseline": {}}

        def add_fit(role, mode, model, y, proba, n_train, seconds=None, n_iter=None, hit_cap=None):
            ll = md.row_logloss(y, proba)
            prior_ll = float(-np.log(np.clip(prior_tr[y], md.EPS, 1.0)).mean())
            row = {"group": group, "role": role, "mode": mode, "n_train": int(n_train), "n_eval": int(len(y)), "logloss": float(ll.mean()), "brier": float(md.multiclass_brier_rows(y, proba).mean()),
                   "prior_logloss": prior_ll, "fit_seconds": seconds if seconds is not None else model.info.get("seconds"), "n_iter": n_iter if n_iter is not None else model.info.get("n_iter"),
                   "hit_cap": int(bool(hit_cap if hit_cap is not None else model.info.get("hit_cap")))}
            for c, name in enumerate(classes):
                row[f"loss_{name}"] = float(ll[y == c].mean()) if (y == c).any() else None
            out["fit"].append(row)
            losses["model" if role == "model" else "baseline"][mode] = ll
            return ll

        # ---- held-out years
        te_eval = None
        if cte is not None:
            sub_te, yte = main._encode(cte, main.settings, fixed_classes=True)
            te_eval = (sub_te, yte)
            games_te = np.asarray(sub_te["game_pk"])
            seen_p = np.isin(np.asarray(sub_te["pitcher"]), np.asarray(sub_tr["pitcher"])); seen_b = np.isin(np.asarray(sub_te["batter"]), np.asarray(sub_tr["batter"]))
            for role, model in (("model", main), ("baseline", base)):
                if model is None:
                    continue
                spec = model.predict_proba(sub_te, effects="specific"); pop = model.predict_proba(sub_te, effects="population")
                add_fit(role, "season_specific", model, yte, spec, len(ytr))
                if role == "model" and (model.groups or model.profile is not None):
                    add_fit(role, "season_population", model, yte, pop, len(ytr))
                for side, seen in (("pitcher", seen_p), ("batter", seen_b)):
                    if (~seen).sum() > 0:
                        ll = md.row_logloss(yte[~seen], spec[~seen])
                        out["fit"].append({"group": group, "role": role, "mode": f"season_new_{side}", "n_train": int(len(ytr)), "n_eval": int((~seen).sum()), "logloss": float(ll.mean()),
                                           "brier": float(md.multiclass_brier_rows(yte[~seen], spec[~seen]).mean()), "prior_logloss": float(-np.log(np.clip(prior_tr[yte[~seen]], md.EPS, 1.0)).mean()),
                                           "fit_seconds": None, "n_iter": None, "hit_cap": 0, **{f"loss_{n}": (float(ll[yte[~seen] == c].mean()) if (yte[~seen] == c).any() else None) for c, n in enumerate(classes)}})
                        losses["model" if role == "model" else "baseline"][f"season_new_{side}"] = (ll, np.nonzero(~seen)[0])
                if role == "model":
                    for r in md.calibration_rows(yte, spec, classes, 10):
                        out["calibration"].append({"group": group, "mode": "season_specific", **r})
            if base is not None:
                for mode in ("season_specific",):
                    d = losses["baseline"][mode] - losses["model"][mode]
                    out["gain"].append({"group": group, "mode": "season", "comparison": "baseline→model", "n": int(len(d)), **self._gain(d, games_te, config)})
                for side in ("pitcher", "batter"):
                    key = f"season_new_{side}"
                    if key in losses["model"] and key in losses["baseline"]:
                        (lm, idx), (lb, _) = losses["model"][key], losses["baseline"][key]
                        out["gain"].append({"group": group, "mode": key, "comparison": "baseline→model", "n": int(len(lm)), **self._gain(lb - lm, games_te[idx], config)})
        # ---- players the model has not seen, inside the training years
        folds_done: dict[str, Any] = {}
        for by in config["cold_by"]:
            ids = np.asarray(sub_tr[by]); folds = lc.unit_folds(ids, config["cold_folds"], config["seed"])
            if not folds:
                continue
            eval_idx = np.concatenate([te for _, te in folds]); pos = {int(i): k for k, i in enumerate(eval_idx)}
            probas = {"model": np.zeros((len(eval_idx), K)), "baseline": np.zeros((len(eval_idx), K))}
            secs = {"model": 0.0, "baseline": 0.0}
            for tr, te in folds:
                for role, st in (("model", settings), ("baseline", baseline_settings(settings))):
                    if role == "baseline" and base is None:
                        continue
                    fold_model = om.new_model(kind).fit(lc.take(sub_tr, tr), None, {**st, "bagging_reps": 1}, None, classes=classes)
                    probas[role][[pos[int(i)] for i in te]] = fold_model.predict_proba(lc.take(sub_tr, te), effects="specific")
                    secs[role] += fold_model.info.get("seconds", 0.0)
            yv = ytr[eval_idx]
            for role in ("model", "baseline"):
                if role == "baseline" and base is None:
                    continue
                add_fit(role, f"cold_{by}", main if role == "model" else base, yv, probas[role], int(np.mean([len(t) for t, _ in folds])), seconds=secs[role], n_iter=None, hit_cap=False)
            for r in (md.calibration_rows(yv, probas["model"], classes, 10) if te_eval is None else []):
                out["calibration"].append({"group": group, "mode": f"cold_{by}", **r})
            if base is not None:
                d = losses["baseline"][f"cold_{by}"] - losses["model"][f"cold_{by}"]
                out["gain"].append({"group": group, "mode": f"cold_{by}", "comparison": "baseline→model", "n": int(len(d)), **self._gain(d, ids[eval_idx], config)})
        # ---- common sense and the pitch effects (league-average player)
        ref_rows = te_eval[0] if te_eval is not None else sub_tr
        ref_y = te_eval[1] if te_eval is not None else ytr
        out["common"] += self._common_sense(group, main, ref_rows, ref_y, classes)
        rng = np.random.default_rng(config["seed"])
        eff_rows = self._effect_rows(main, sub_tr, ref_rows, config, rng, group)
        out["effects"] += eff_rows
        comparisons["pitch_effects"][group] = pc.chance_summary([r["lo"] if r["lo"] is not None else np.nan for r in eff_rows], [r["hi"] if r["hi"] is not None else np.nan for r in eff_rows], config["confidence"])
        # ---- each training year alone
        if len(train_years) >= 2 and config["replication_members"] > 0:
            for yv_ in train_years:
                yrows = lc.take(sub_tr, np.nonzero(np.asarray(sub_tr["game_year"]) == yv_)[0])
                if len(yrows["game_pk"]) == 0:
                    continue
                alone = om.new_model(kind).fit(yrows, None, {**settings, "bagging_reps": max(int(config["replication_members"]), 2)}, None, classes=classes)
                for r in self._effect_rows(alone, sub_tr, yrows, config, rng, group):
                    out["replication"].append({"group": group, "year": int(yv_), **{k: r[k] for k in ("phase", "action", "class", "effect", "lo", "hi", "n")}})
        # ---- the stored model
        if config["save_package"]:
            artifacts.append(ArtifactData(f"outcome_model_{group}", f"The fitted {kind} model of group {group} (pickled, zlib, base64 in parts)", ("part", "payload"), tuple(pack_bytes(main.to_bytes()))))
        return classes

    @staticmethod
    def _gain(delta, units, config) -> dict[str, float]:
        mean, lo, hi, se = md.paired_game_bootstrap(delta, units, reps=config["bootstrap_reps"], seed=config["seed"], confidence=config["confidence"])
        return {"delta_logloss": mean, "lo": lo, "hi": hi, "se": se}

    @staticmethod
    def _common_sense(group, model, rows, y, classes) -> list[dict[str, Any]]:
        """What the model predicts for the league-average player against what happened, per pitch type: whiff rate, home-run rate and balls-in-play rate (all per pitch)."""

        proba = model.predict_proba(rows, effects="population")
        idx = {c: i for i, c in enumerate(classes)}
        typ = np.asarray(rows["pitch_type"]).astype(str)
        res = []

        def rate(p, y_, names):
            cols_ = [idx[n] for n in names if n in idx]
            return (float(p[:, cols_].sum(axis=1).mean()) if len(cols_) else None, float(np.isin(y_, cols_).mean()) if len(cols_) else None)

        for t in dz.PITCH_TYPES:
            m = typ == t
            if not m.any():
                continue
            w_p, w_o = rate(proba[m], y[m], ("whiff",)); h_p, h_o = rate(proba[m], y[m], ("in_play_home_run",)); i_p, i_o = rate(proba[m], y[m], ("in_play_home_run", "in_play_non_hr"))
            res.append({"group": group, "pitch_type": t, "n": int(m.sum()), "whiff_pred": w_p, "whiff_obs": w_o, "hr_pred": h_p, "hr_obs": h_o, "in_play_pred": i_p, "in_play_obs": i_o})
        return res

    def _effect_rows(self, model, usage_rows, rows, config, rng, group) -> list[dict[str, Any]]:
        """How much each pitch type changes the outcome probabilities against the usual mix of the other types, by count phase, for the league-average player.

        The same rows are asked about every pitch type in turn (the type and the streak are set, everything else stays); the usual mix is the share of each supported type in
        the training rows of the phase. Interval: spread of the bagged members (none with a single member)."""

        n = len(rows["game_pk"])
        take = rng.choice(n, size=min(n, int(config["effect_sample"])), replace=False) if n else np.zeros(0, dtype=int)
        sample = lc.take(rows, np.sort(take))
        m = len(sample["game_pk"])
        if m == 0:
            return []
        phase = anc.count_phase(sample["balls"], sample["strikes"])
        u_phase = anc.count_phase(usage_rows["balls"], usage_rows["strikes"]); u_typ = dz.type_index(usage_rows["pitch_type"])
        prev = np.asarray(sample["prev1_pitch_type"]).astype(str); prior = np.nan_to_num(np.asarray(sample["prior_streak"], dtype=float))
        preds: dict[int, Any] = {}
        for a, t in enumerate(dz.PITCH_TYPES):
            if (u_typ == a).sum() < config["support_min_n"]:
                continue
            cf = {k: v for k, v in sample.items() if isinstance(v, np.ndarray) and len(v) == m}
            cf["pitch_type"] = np.full(m, t); cf["streak_pos"] = np.where(prev == t, np.minimum(prior + 1, 3), 1.0)
            preds[a] = model.predict_members(cf, effects="population", seed=config["seed"])
        rows_out = []
        classes = model.classes
        for ph, pname in enumerate(anc.PHASES):
            in_ph = phase == ph
            if not in_ph.any():
                continue
            counts = np.array([((u_typ == a) & (u_phase == ph)).sum() for a in range(len(dz.PITCH_TYPES))], dtype=float)
            share = counts / max(counts.sum(), 1.0)
            sup = [a for a in preds if share[a] >= config["support_min_share"] and counts[a] >= config["support_min_n"]]
            for a in sup:
                others = [b for b in sup if b != a]
                if not others:
                    continue
                w = share[others] / share[others].sum()
                mix = sum(wi * preds[b][:, in_ph, :] for wi, b in zip(w, others))
                eff = (preds[a][:, in_ph, :] - mix).mean(axis=1)                       # [members, classes]
                mean = eff.mean(axis=0)
                if eff.shape[0] > 1:
                    _, lo, hi = pc.member_interval(eff, config["confidence"])
                else:
                    lo = hi = np.full(eff.shape[1], np.nan)
                for c, cname in enumerate(classes):
                    rows_out.append({"group": group, "phase": pname, "action": dz.PITCH_TYPES[a], "class": cname, "effect": float(mean[c]),
                                     "lo": None if not np.isfinite(lo[c]) else float(lo[c]), "hi": None if not np.isfinite(hi[c]) else float(hi[c]), "n": int(in_ph.sum())})
        return rows_out


register_method(OutcomeModelTrainMethod())
