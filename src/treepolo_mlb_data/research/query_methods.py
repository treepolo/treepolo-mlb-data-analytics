"""Research method ``pitch_query`` (P6 plan A.6) and the runtime that answers situation queries from stored runs."""
from __future__ import annotations

import threading
import time
from typing import Any

from .config_schema import ConfigError, ConfigField
from .league_methods import DIGITS
from .methods import ArtifactData, ResearchContext, ResearchMethod, ResearchResult, register_method
from .sections import make_section

GROUPS = ("RvR", "RvL", "LvR", "LvL")
PITCH_TYPES = ("FF", "SI", "FC", "SL", "ST", "CH", "CU", "FS", "KC")      # the frozen list (profiles.PITCH_TYPES; kept here so that importing the web app stays light)


class _Lazy:
    """Imports a heavy module on first use so that starting the web app stays light."""

    def __init__(self, name: str):
        self._name = name

    def __getattr__(self, item: str):
        import importlib

        return getattr(importlib.import_module(self._name, __package__), item)


np = _Lazy("numpy")
qc = _Lazy(".query_core")


def _run_of_kind(ctx: ResearchContext, run_id: int, kind: str) -> dict[str, Any]:
    run = ctx.store.get_run(int(run_id)) if ctx.store is not None else None
    if run is None or run["kind"] != kind or run["status"] != "success":
        raise ConfigError(f"run {run_id} is not a successful {kind} run / 紀錄 {run_id} 不是成功的 {kind} 紀錄")
    return run


class PitchQueryMethod(ResearchMethod):
    kind = "pitch_query"
    version = 1
    label_zh = "情境查詢工具（驗收與球員名稱）"
    label_en = "Situation query tool (acceptance and player names)"
    requires_scope = True
    fields = (
        ConfigField("policy_run_id", "int", default=0, minimum=0, label_zh="pitch_policy 紀錄編號（情境表）", label_en="pitch_policy run id (situation table)"),
        ConfigField("package_run_id", "int", default=0, minimum=0, label_zh="league_model 紀錄編號（模型包；0＝只有 M0）", label_en="league_model run id (model package; 0 = M0 only)",
                    help_zh="需要用 save_package 執行過，才能查詢特定投手與打者（M1–M3）。", help_en="Needs a run made with save_package for named players (M1-M3)."),
        ConfigField("level", "choice", default="L3", choices=("L1", "L2", "L3"), label_zh="個別化用的特徵階", label_en="Descriptor level for player queries"),
        ConfigField("check_situations", "int", default=20, minimum=3, maximum=200, label_zh="驗收情境數（含外推與不支持）", label_en="Situations to check (incl. extrapolated and unsupported)"),
        ConfigField("check_samples", "int", default=20, minimum=2, maximum=200, label_zh="驗收時 M1/M2 的抽樣球員數", label_en="Sampled players in the M1/M2 checks"),
        ConfigField("check_members", "int", default=4, minimum=1, maximum=60, label_zh="驗收時使用的袋裝成員數", label_en="Bagged members in the checks"),
        ConfigField("with_names", "bool", default=True, label_zh="建立球員名稱對照（投手 player_name、打者從 des 擷取）", label_en="Build the player-name table"),
        ConfigField("seed", "int", default=20240601, label_zh="隨機種子", label_en="Random seed"),
    )

    def validate(self, config: dict[str, Any], ctx: ResearchContext) -> None:
        if not config["policy_run_id"]:
            raise ConfigError("policy_run_id is required / 需要 policy_run_id")
        _run_of_kind(ctx, config["policy_run_id"], "pitch_policy")
        if config["package_run_id"]:
            _run_of_kind(ctx, config["package_run_id"], "league_model")

    def method_inputs(self, ctx: ResearchContext, config: dict[str, Any]) -> dict[str, Any]:
        out = {"policy_run_key": _run_of_kind(ctx, config["policy_run_id"], "pitch_policy")["run_key"]}
        if config["package_run_id"]:
            out["package_run_key"] = _run_of_kind(ctx, config["package_run_id"], "league_model")["run_key"]
        return out

    def run(self, ctx: ResearchContext, config: dict[str, Any]) -> ResearchResult:
        rt = QueryRuntime(ctx.store, policy_run_id=config["policy_run_id"], package_run_id=config["package_run_id"], level=config["level"])
        rng = np.random.default_rng(config["seed"])
        accept, latency, sources = [], [], []
        for group in rt.groups():
            table = rt.table(group)
            accept += self._acceptance(group, table, config["check_situations"], rng)
            sources.append({"group": group, "policy_run_id": config["policy_run_id"], "package_run_id": config["package_run_id"] or None, "members": table.meta["members"], "mode": table.meta["mode"]})
            if config["package_run_id"] and rt.has_package(group):
                a, l = self._player_checks(rt, group, config, rng)
                accept += a; latency += l
        artifacts, names_rows, extras_names = [], [], {}
        if config["with_names"]:
            names = qc.build_names(ctx.config.database_path, ctx.scope)
            names_rows = [{"side": "pitcher", "name": "(coverage)", "id": "", "value": names["coverage"]["pitcher"]}, {"side": "batter", "name": "(coverage)", "id": "", "value": names["coverage"]["batter"]}]
            artifacts.append(ArtifactData("player_names", "Player names: pitchers from player_name, batters read from the description of the last pitch of a plate appearance", ("side", "id", "name"),
                                          tuple(names["rows"])))
            extras_names = names["coverage"]
        sections = (
            make_section("Acceptance 驗收（plan 5.7）", ("group", "check", "situation", "max_abs_diff", "passed"), accept, ("group", "check", "situation"), "duckdb", DIGITS),
            make_section("Latency 延遲（秒）", ("group", "mode", "pairs", "members", "seconds"), latency, ("group", "mode"), "duckdb", DIGITS),
            make_section("Names 名稱涵蓋", ("side", "name", "id", "value"), names_rows, ("side", "name"), "duckdb", DIGITS),
            make_section("Sources 來源", ("group", "policy_run_id", "package_run_id", "members", "mode"), sources, ("group",), "duckdb", DIGITS),
        )
        return ResearchResult(sections=sections, extras={"policy_run_id": config["policy_run_id"], "package_run_id": config["package_run_id"] or None, "level": config["level"],
                                                         "groups": rt.groups(), "names": extras_names}, artifacts=tuple(artifacts))

    @staticmethod
    def _acceptance(group: str, table: qc.SituationTable, n: int, rng: np.random.Generator) -> list[dict[str, Any]]:
        """Random situations (some extrapolated, some with unsupported pitch types): the lookup must match a direct reading of the arrays and the closed forms."""

        a = table.arrays; sh = table.shape
        b, k, o, base, p, r = sh.coords()
        valid = np.nonzero(((p == sh.none) == ((b == 0) & (k == 0))) & ((p != sh.none) | (r == 0)))[0]
        n_obs = a["n"]
        extrap = [s for s in valid if (a["support"][s] & (n_obs[s] < table.meta["min_count"])).any()]
        unsupported = [s for s in valid if (~a["support"][s]).any()]
        picks = list(rng.choice(extrap, size=min(5, len(extrap)), replace=False)) if extrap else []
        picks += list(rng.choice(unsupported, size=min(3, len(unsupported)), replace=False)) if unsupported else []
        picks += list(rng.choice(valid, size=max(n - len(picks), 0), replace=False))
        rows = []
        for s in picks[:n]:
            s = int(s)
            label = f"{int(b[s])}-{int(k[s])}, {int(o[s])} out, bases {int(base[s])}, prev {'none' if p[s] == sh.none else PITCH_TYPES[p[s]]}, streak {int(r[s]) + 1}"
            got = table.rows(s)
            q = np.array([x["q"] if x["q"] is not None else np.nan for x in got]); obs = a["obs"][s]
            diff = np.abs(q - a["q"][s]); diff = diff[np.isfinite(diff)]
            rows.append({"group": group, "check": "Q equals the table", "situation": label, "max_abs_diff": float(diff.max()) if len(diff) else 0.0, "passed": int(not len(diff) or diff.max() < 1e-12)})
            rest_share = 1.0 - obs
            errs = []
            for ai, x in enumerate(got):
                if x["marginal"] is None or rest_share[ai] <= 1e-12:
                    continue
                qbar = (np.nansum(obs * a["q"][s]) - obs[ai] * a["q"][s][ai]) / rest_share[ai]
                errs.append(abs(x["marginal"] - table.meta["delta"] * (a["q"][s][ai] - qbar)))
            worst = float(max(errs)) if errs else 0.0
            rows.append({"group": group, "check": "marginal value equals Delta*(Q_a - Qbar_-a)", "situation": label, "max_abs_diff": worst, "passed": int(worst < 1e-9)})
            flags_ok = all((x["flag"] == "unsupported") == (not a["support"][s][ai]) and (x["flag"] == "extrapolated") == bool(a["support"][s][ai] and n_obs[s][ai] < table.meta["min_count"])
                           for ai, x in enumerate(got))
            rows.append({"group": group, "check": "flags follow the support rule", "situation": label, "max_abs_diff": 0.0, "passed": int(flags_ok)})
        return rows

    @staticmethod
    def _player_checks(rt: "QueryRuntime", group: str, config: dict[str, Any], rng: np.random.Generator) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """M1, M2 and M3 on a random pitcher and batter: repeatable, probabilities add to one, intervals ordered; the time each takes."""

        pq = rt.player_query(group)
        pit = int(rng.choice(pq.package.profile.players("pitcher", group)[0])); bat = int(rng.choice(pq.package.profile.players("batter", group)[0]))
        accept, latency = [], []
        for mode, kw in (("M1", {"pitcher": pit}), ("M2", {"batter": bat}), ("M3", {"pitcher": pit, "batter": bat})):
            t0 = time.monotonic()
            args = dict(samples=config["check_samples"], members=config["check_members"], level=config["level"], seed=config["seed"], **kw)
            first = pq.query(0, 0, 1, "000", "none", 1, **args)
            seconds = time.monotonic() - t0
            second = pq.query(0, 0, 1, "000", "none", 1, **args)
            same = max(abs((x["q"] or 0.0) - (y["q"] or 0.0)) for x, y in zip(first["rows"], second["rows"]))
            sums = [abs(sum(x["outcomes"].values()) - 1.0) for x in first["rows"] if x["supported"]]
            ordered = all(x["q_lo"] <= x["q"] <= x["q_hi"] for x in first["rows"] if x["supported"] and x["q"] is not None)
            accept.append({"group": group, "check": f"{mode} repeats and is well formed", "situation": f"pitcher {pit}, batter {bat}", "max_abs_diff": float(max(same, max(sums) if sums else 0.0)),
                           "passed": int(same < 1e-12 and (not sums or max(sums) < 1e-9) and ordered)})
            latency.append({"group": group, "mode": mode, "pairs": first["sampling"]["pairs"], "members": first["sampling"]["members"], "seconds": seconds})
        return accept, latency


class QueryRuntime:
    """Answers queries from the stored runs behind one ``pitch_query`` run (tables and packages are loaded when first needed)."""

    def __init__(self, store, *, policy_run_id: int, package_run_id: int = 0, level: str = "L3", query_run_id: int | None = None):
        self.store, self.policy_run_id, self.package_run_id, self.level, self.query_run_id = store, int(policy_run_id), int(package_run_id or 0), level, query_run_id
        self._tables: dict[str, qc.SituationTable] = {}; self._queries: dict[str, qc.PlayerQuery] = {}; self._lock = threading.RLock()
        self._names: dict[str, dict[str, str]] | None = None

    def groups(self) -> list[str]:
        found = []
        for g in GROUPS:
            if self.store.load_artifact(self.policy_run_id, f"situation_table_{g}") is not None:
                found.append(g)
        if not found:
            raise qc.QueryError("the policy run has no situation tables / pitch_policy 紀錄沒有情境表")
        return found

    def table(self, group: str) -> qc.SituationTable:
        with self._lock:
            if group not in self._tables:
                art = self.store.load_artifact(self.policy_run_id, f"situation_table_{group}")
                if art is None:
                    raise qc.QueryError(f"no situation table for group {group}; available {self.groups()} / 沒有分組 {group} 的情境表")
                self._tables[group] = qc.SituationTable.from_rows(art["rows"])
            return self._tables[group]

    def has_package(self, group: str) -> bool:
        return bool(self.package_run_id) and self.store.load_artifact(self.package_run_id, f"model_package_{group}") is not None

    def player_query(self, group: str) -> qc.PlayerQuery:
        with self._lock:
            if group not in self._queries:
                if not self.has_package(group):
                    raise qc.QueryError(f"no model package for group {group}: only league-average (M0) queries are available / 沒有分組 {group} 的模型包，只能做聯盟平均（M0）查詢")
                package = qc.PackageModel.from_rows(self.store.load_artifact(self.package_run_id, f"model_package_{group}")["rows"])
                self._queries[group] = qc.PlayerQuery(self.table(group), package)
            return self._queries[group]

    # --- gates of the model run, per side
    def gate_status(self, group: str) -> dict[str, str]:
        out = {"pitcher": "unknown", "batter": "unknown"}
        if not self.package_run_id:
            return out
        result = self.store.load_result(self.package_run_id)
        rows = next((s["rows"] for s in result["sections"] if s["title"].startswith("Gates")), [])
        for side in out:
            mine = [r for r in rows if r["group"] == group and str(r["side"]).startswith(side + ":")]
            if not mine:
                continue
            g1a = any(r["gate"] == "G1a" and r["passed"] == 1 for r in mine)
            g1b = any(r["gate"] == "G1b" and r["passed"] == 1 for r in mine)
            out[side] = "full" if g1a and g1b else ("level_only" if g1a else "off")
        return out

    # --- names
    def names(self) -> dict[str, dict[str, str]]:
        if self._names is None:
            self._names = {"pitcher": {}, "batter": {}}
            art = self.store.load_artifact(self.query_run_id, "player_names") if self.query_run_id else None
            for r in (art["rows"] if art else []):
                self._names[r["side"]][str(r["id"])] = r["name"]
        return self._names

    def search(self, group: str, side: str, text: str, limit: int = 20) -> list[dict[str, Any]]:
        """Players of ``group`` whose id or name contains ``text`` (empty text: the ones with most pitches)."""

        pq = self.player_query(group)
        ids, n = pq.package.profile.players(side, group)
        names = self.names().get(side, {})
        text = (text or "").strip().lower()
        order = np.argsort(-n)
        out = []
        for i in order:
            ident = str(ids[i]); name = names.get(ident, "")
            if not text or text in ident or text in name.lower():
                out.append({"id": ident, "name": name or None, "pitches": int(n[i])})
                if len(out) >= limit:
                    break
        return out

    def answer(self, params: dict[str, Any]) -> dict[str, Any]:
        group = params.get("group")
        if group not in self.groups():
            raise qc.QueryError(f"group must be one of {self.groups()} / group 必須是 {self.groups()} 其中之一")
        table = self.table(group)
        balls, strikes, outs = int(params["balls"]), int(params["strikes"]), int(params["outs"])
        bases = params.get("bases", "000"); prev = params.get("prev", "none"); streak = int(params.get("streak", 1))
        pitcher, batter = params.get("pitcher"), params.get("batter")
        pitcher = _as_id(pitcher); batter = _as_id(batter)
        if pitcher is None and batter is None:
            res = table.lookup(balls, strikes, outs, bases, prev, streak)
            res["gates"] = self.gate_status(group) if self.package_run_id else {}
            return res
        status = self.gate_status(group)
        notice = []
        if pitcher is not None and status["pitcher"] == "off":
            notice.append("投手側的個別化沒有通過閘門（球員描述在現有資料下沒有預測力），改顯示聯盟平均 / pitcher descriptors did not pass the gate: league average shown")
            pitcher = None
        if batter is not None and status["batter"] == "off":
            notice.append("打者側的個別化沒有通過閘門，改顯示聯盟平均 / batter descriptors did not pass the gate: league average shown")
            batter = None
        pq = self.player_query(group)
        res = pq.query(balls, strikes, outs, bases, prev, streak, pitcher=pitcher, batter=batter, level=params.get("level") or self.level,
                       samples=int(params.get("samples", 100)), members=int(params.get("members", 10)), seed=int(params.get("seed", 20240601)))
        if any(status[s] == "level_only" for s in res["players"]):
            notice.append("只有水準個別化：球員整體強弱改變機率與 Q 的水準，但球種的排序與邊際價值與聯盟平均沒有可偵測差異 / level-only personalisation")
            for row in res["rows"]:
                row["detected"] = False
        names = self.names()
        for side, info in res["players"].items():
            info["name"] = names.get(side, {}).get(info["id"])
        res["gates"] = status; res["notice"] = notice
        return res


def _as_id(value: Any) -> Any:
    if value in (None, "", "null"):
        return None
    text = str(value)
    return int(text) if text.lstrip("-").isdigit() else text


register_method(PitchQueryMethod())
