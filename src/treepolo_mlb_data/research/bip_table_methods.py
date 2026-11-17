"""Research method ``bip_table``: what happens after a ball in play, by the type of the batted ball, the outs and the runners (training seasons only).

For every ball in play that is not a home run (a bunt plate appearance is excluded as everywhere else) the data say what followed: the outs after the play, the runners after the play and the runs
that scored. For each batted-ball type (ground ball, line drive, fly ball, popup) x outs (0-2) x runners (8) cell this gives the expected loss after the ball is hit, ``runs + run expectancy of the
state afterwards``, and how it came about (out, force out, double play, hit, error, ...). The cell of one hand group has few pitches in some places, so its value is pulled towards the value of
the same cell of the four hand groups together: ``(n * cell mean + kappa * pooled mean) / (n + kappa)``. ``kappa`` is chosen by predicting the last training season from the earlier ones (never
a season that is held out for checking). The decision engine reads the smoothed value as the end value of a ball in play of that type.

Also reported, for the training seasons only: inside a batted-ball type, how much the pitch types differ in the loss after a ball in play (after the outs and runners are taken out)."""
from __future__ import annotations

from typing import Any

from .config_schema import ConfigError, ConfigField
from .guards import scope_years
from .league_methods import DIGITS, GROUPS, fetch_columns
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
lc = _Lazy(".league_core")
pc = _Lazy(".policy_core")

EVENT_KINDS = ("out", "force_out", "double_play", "sac_fly", "single", "double", "triple", "error", "fielders_choice")
_EVENT_OF = {"field_out": "out", "force_out": "force_out", "grounded_into_double_play": "double_play", "double_play": "double_play", "sac_fly_double_play": "double_play", "triple_play": "double_play",
             "sac_fly": "sac_fly", "single": "single", "double": "double", "triple": "triple", "field_error": "error", "fielders_choice": "fielders_choice", "fielders_choice_out": "fielders_choice"}
N_TYPES = 4


def bip_rows(cols, re_cnt, shape) -> dict[str, Any]:
    """The balls in play that are not home runs, with what followed: batted-ball type (0-3), outs, bases, the loss after the play (runs + run expectancy afterwards), outs and bases after,
    runs, what the event was and the pitch type. Rows without a batted-ball type, with a missing run value or with a walk-off (censored) are left out."""

    outcome = np.asarray(cols["outcome"]).astype(str)
    bb = np.asarray(cols["bb_type"]).astype(str)
    kinds = list(lc.BB_TYPE_TO_CLASS)
    t = np.array([kinds.index(x) if x in kinds else -1 for x in bb])
    o = np.asarray(cols["outs_when_up"]).astype(int); B = np.asarray(cols["bases"]).astype(int)
    b = np.asarray(cols["balls"]).astype(int); k = np.asarray(cols["strikes"]).astype(int)
    pv = np.asarray(cols["pitch_value"], dtype=float)
    ok = np.isin(outcome, lc.IN_PLAY_NOT_HR) & (t >= 0) & np.isfinite(pv) & (o < 3)
    idx = np.nonzero(ok)[0]
    value = pv[idx] + re_cnt[np.minimum(b[idx], shape.n_balls - 1), np.minimum(k[idx], shape.n_strikes - 1), np.minimum(o[idx], 2), B[idx]]       # = runs + the run expectancy afterwards
    nxt = np.asarray(cols["next_state_code"], dtype=float)[idx]
    over = ~np.isfinite(nxt)                                                                                                                  # the last pitch of the half-inning: three outs
    code = np.where(over, 0.0, nxt).astype(int)
    o_after = np.where(over, 3, (code // 10) % 10); B_after = np.where(over, 0, code % 10)
    events = np.asarray(cols["events"]).astype(str)[idx]
    return {"type": t[idx], "outs": o[idx], "bases": B[idx], "value": value, "outs_after": o_after, "bases_after": B_after, "runs": np.nan_to_num(np.asarray(cols["runs_on_pitch"], dtype=float)[idx]),
            "event": np.array([EVENT_KINDS.index(_EVENT_OF[e]) if e in _EVENT_OF else -1 for e in events]), "pitch_type": np.asarray(cols["pitch_type"]).astype(str)[idx]}


def cell_stats(rows: dict[str, Any]) -> dict[str, Any]:
    """Counts, sums of the value, of runs and of outs added, and the counts of each kind of event, per (type, outs, bases) cell."""

    n = np.zeros((N_TYPES, 3, 8)); s = np.zeros((N_TYPES, 3, 8)); runs = np.zeros((N_TYPES, 3, 8)); outs = np.zeros((N_TYPES, 3, 8)); ev = np.zeros((len(EVENT_KINDS), N_TYPES, 3, 8))
    t, o, B = rows["type"], rows["outs"], rows["bases"]
    np.add.at(n, (t, o, B), 1.0); np.add.at(s, (t, o, B), rows["value"]); np.add.at(runs, (t, o, B), rows["runs"]); np.add.at(outs, (t, o, B), rows["outs_after"] - rows["outs"])
    ok = rows["event"] >= 0
    np.add.at(ev, (rows["event"][ok], t[ok], o[ok], B[ok]), 1.0)
    return {"n": n, "sum": s, "runs": runs, "outs": outs, "events": ev}


def merge_stats(parts) -> dict[str, Any]:
    out = None
    for p in parts:
        out = {k: v.copy() for k, v in p.items()} if out is None else {k: out[k] + p[k] for k in out}
    return out


def smooth(group: dict[str, Any], pooled: dict[str, Any], kappa: float) -> np.ndarray:
    """[4, 3, 8] loss after a ball in play: the cell mean of the group pulled towards the same cell of the pooled hand groups (``kappa`` pseudo-pitches). A cell that the pooled data lack
    falls back to the mean of its batted-ball type and outs."""

    pm = np.where(pooled["n"] > 0, pooled["sum"] / np.maximum(pooled["n"], 1.0), np.nan)
    fallback = np.where(pooled["n"].sum(axis=2, keepdims=True) > 0, pooled["sum"].sum(axis=2, keepdims=True) / np.maximum(pooled["n"].sum(axis=2, keepdims=True), 1.0), np.nanmean(pm))
    pm = np.where(np.isnan(pm), fallback, pm)
    n = group["n"]
    return (group["sum"] + kappa * pm) / np.maximum(n + kappa, 1e-12)


def choose_kappa(train_group, train_pooled, test_rows, grid) -> tuple[float, list[dict[str, float]]]:
    """The ``kappa`` with the smallest mean squared error of the loss after a ball in play, for the balls in play of a season that the tables did not see."""

    out = []
    for kappa in grid:
        table = smooth(train_group, train_pooled, float(kappa))
        err = test_rows["value"] - table[test_rows["type"], test_rows["outs"], test_rows["bases"]]
        out.append({"kappa": float(kappa), "mse": float(np.mean(err ** 2)), "n_test": int(len(err))})
    best = min(out, key=lambda r: (r["mse"], r["kappa"]))
    return best["kappa"], out


def pitch_type_differences(rows: dict[str, Any], types: list[str]) -> list[dict[str, Any]]:
    """Inside a batted-ball type: the mean loss after a ball in play of each pitch type, after the (type, outs, bases) cell mean is taken out, with its standard error. Positive = more loss
    than the other pitch types in the same cells."""

    cell_n = np.zeros((N_TYPES, 3, 8)); cell_s = np.zeros((N_TYPES, 3, 8))
    np.add.at(cell_n, (rows["type"], rows["outs"], rows["bases"]), 1.0); np.add.at(cell_s, (rows["type"], rows["outs"], rows["bases"]), rows["value"])
    mean = cell_s / np.maximum(cell_n, 1.0)
    resid = rows["value"] - mean[rows["type"], rows["outs"], rows["bases"]]
    out = []
    for ti, tname in enumerate(lc.BB_TYPE_TO_CLASS):
        for p in types:
            m = (rows["type"] == ti) & (rows["pitch_type"] == p)
            if m.sum() < 2:
                continue
            out.append({"batted_ball": tname, "pitch_type": p, "n": int(m.sum()), "mean_loss": float(rows["value"][m].mean()), "mean_after_cells": float(resid[m].mean()),
                        "se": float(resid[m].std(ddof=1) / np.sqrt(m.sum()))})
    return out


class BipTableMethod(ResearchMethod):
    kind = "bip_table"
    version = 1
    label_zh = "打進場內之後的統計表（依擊球類型、出局數、壘包）"
    label_en = "After a ball in play: table by batted-ball type, outs and runners"
    requires_scope = True
    fields = (
        BUNT_POLICY, FILTERS, RE_SCOPE,
        ConfigField("groups", "str_list", default=list(GROUPS), label_zh="分組（全部讀進來當母體；各組各出一張表）", label_en="Groups (all are read as the pooled prior; one table per group)"),
        ConfigField("kappa_grid", "json", default=[0, 5, 10, 20, 50, 100, 200, 500, 1000], label_zh="κ 的候選值", label_en="Candidate kappa values",
                    help_zh="用較早的季預測最後一季來挑；2025 年不能放進範圍。", help_en="Chosen by predicting the last season of the scope from the earlier ones."),
        ConfigField("max_rows", "int", default=1_500_000, minimum=1000, label_zh="每組每球季列數上限", label_en="Row cap per group and season"),
    )

    def validate(self, config: dict[str, Any], ctx: ResearchContext) -> None:
        if not config["groups"] or set(config["groups"]) - set(GROUPS):
            raise ConfigError(f"groups must be a non-empty subset of {list(GROUPS)} / groups 必須是 {list(GROUPS)} 的非空子集")
        if len(scope_years(ctx.scope)) < 2:
            raise ConfigError("the scope needs at least two seasons: kappa is chosen by predicting the last one from the earlier ones / 範圍需要至少兩個球季（κ 用較早的季預測最後一季來挑）")
        if not isinstance(config["kappa_grid"], list) or not config["kappa_grid"] or any(float(k) < 0 for k in config["kappa_grid"]):
            raise ConfigError("kappa_grid must be a non-empty list of numbers >= 0 / kappa_grid 必須是非空的非負數列")
        _check_filters(config["filters"])

    def method_inputs(self, ctx: ResearchContext, config: dict[str, Any]) -> dict[str, Any]:
        return re_method_inputs(ctx, config)

    def run(self, ctx: ResearchContext, config: dict[str, Any]) -> ResearchResult:
        from .policy_core import Shape
        from .profiles import PITCH_TYPES

        years = sorted(scope_years(ctx.scope))
        shape = Shape()
        re = get_re_table(ctx, config["re_scope"])
        re_cnt = np.zeros((shape.n_balls, shape.n_strikes, 3, 8))
        for b in range(shape.n_balls):
            for k in range(shape.n_strikes):
                for o in range(3):
                    for B in range(8):
                        re_cnt[b, k, o, B] = re.re_by_state[b * 1000 + k * 100 + o * 10 + B]
        per: dict[tuple[str, int], dict[str, Any]] = {}
        all_rows: dict[str, dict[str, Any]] = {}
        for gi, group in enumerate(config["groups"]):
            parts = []
            for yi, year in enumerate(years):
                if ctx.progress:
                    ctx.progress("bip_table", (gi * len(years) + yi) / (len(config["groups"]) * len(years)), f"group {group} {year}")
                cols = fetch_columns(ctx, {**config, "class_scheme": "eight", "with_bb_type": True}, [group], [year], re_by_state=re.re_by_state)
                rows = bip_rows(cols, re_cnt, shape)
                del cols
                per[(group, year)] = rows
                parts.append(rows)
            all_rows[group] = {k: np.concatenate([p[k] for p in parts]) for k in parts[0]}
        stats = {key: cell_stats(rows) for key, rows in per.items()}
        pooled_all = merge_stats(stats.values())
        last, earlier = years[-1], years[:-1]
        pooled_early = merge_stats([stats[(g, y)] for g in config["groups"] for y in earlier])
        sections_cells, kappa_rows, artifacts, diff_rows, cell_summary = [], [], [], [], []
        types = [t for t in PITCH_TYPES]
        for group in config["groups"]:
            group_early = merge_stats([stats[(group, y)] for y in earlier]); group_all = merge_stats([stats[(group, y)] for y in years])
            kappa, grid = choose_kappa(group_early, pooled_early, per[(group, last)], config["kappa_grid"])
            for r in grid:
                kappa_rows.append({"group": group, **r, "chosen": r["kappa"] == kappa})
            table = smooth(group_all, pooled_all, kappa)
            n_g = group_all["n"]; n_p = pooled_all["n"]
            for ti, tname in enumerate(lc.BB_TYPE_TO_CLASS):
                for o in range(3):
                    for B in range(8):
                        n = n_g[ti, o, B]
                        row = {"group": group, "batted_ball": tname, "outs": o, "bases": B, "n_group": int(n), "n_pooled": int(n_p[ti, o, B]),
                               "mean_group": float(group_all["sum"][ti, o, B] / n) if n else None, "mean_pooled": float(pooled_all["sum"][ti, o, B] / max(n_p[ti, o, B], 1.0)),
                               "smoothed": float(table[ti, o, B]), "mean_runs": float(group_all["runs"][ti, o, B] / n) if n else None, "mean_outs_added": float(group_all["outs"][ti, o, B] / n) if n else None}
                        for ei, ev in enumerate(EVENT_KINDS):
                            row[f"share_{ev}"] = float(group_all["events"][ei, ti, o, B] / n) if n else None
                        cell_summary.append(row)
            artifacts.append(ArtifactData(f"bip_table_{group}", f"The loss after a ball in play by batted-ball type x outs x runners (group {group}; npz)", ("part", "payload"),
                                          tuple(_pack({"value": table, "n_group": n_g, "n_pooled": n_p, "kappa": np.array(kappa), "types": np.array(list(lc.BB_TYPE_TO_CLASS))}))))
            for r in pitch_type_differences(all_rows[group], types):
                diff_rows.append({"group": group, **r})
        sections = (
            make_section("Cells 打進場內之後的統計表（每格：本組球數、四個手別組合併的球數、各平均、收縮後的值、各種結果的比例；值＝得分＋之後的得分期望，越大對投手越不利）",
                         ("group", "batted_ball", "outs", "bases", "n_group", "n_pooled", "mean_group", "mean_pooled", "smoothed", "mean_runs", "mean_outs_added") + tuple(f"share_{e}" for e in EVENT_KINDS),
                         cell_summary, ("group", "batted_ball", "outs", "bases"), "duckdb", DIGITS),
            make_section("Kappa 收縮強度的選擇（用較早的季預測最後一季的打進場內之後的值，均方誤差越小越好）", ("group", "kappa", "mse", "n_test", "chosen"), kappa_rows, ("group", "kappa"), "duckdb", DIGITS),
            make_section("Pitch types 同一擊球類型內各球種的差異（只用訓練季；mean_after_cells＝扣掉（擊球類型、出局、壘包）各格平均之後的打進場內平均失分，正＝比同格其他球種失分多）",
                         ("group", "batted_ball", "pitch_type", "n", "mean_loss", "mean_after_cells", "se"), diff_rows, ("group", "batted_ball", "pitch_type"), "duckdb", DIGITS),
        )
        notes = [{"control": "bip_table", "zh": "打進場內（非全壘打、非觸擊）之後的結果只用訓練季統計；κ 用較早的季預測最後一季來挑，不使用要留作檢驗的年份。觸擊的整個打席照原本的設定排除。",
                  "en": "What follows a ball in play (not a home run, not a bunt) is counted from the training seasons only; kappa is chosen by predicting the last training season from the earlier ones; held-out seasons are never used. Bunt plate appearances stay excluded."}]
        return ResearchResult(sections=sections, extras={"scope": ctx.scope, "re_scope": config["re_scope"], "years": years, "groups": list(config["groups"]), "notes": notes}, artifacts=tuple(artifacts))


def _pack(arrays: dict[str, Any]):
    from .policy_methods import pack_arrays

    return pack_arrays(arrays)


register_method(BipTableMethod())
