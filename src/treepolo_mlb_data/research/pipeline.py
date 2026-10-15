"""One-click research pipeline: model settings search -> league model -> fixed-effects anchor -> decision engine and situation table -> query tool.

Each step is an ordinary research run, so an identical step that already exists is reused: pressing start again after a failure (or after the data did not change)
continues from where it stopped. The job runs in a background thread; ``status()`` is what the web page polls."""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from .config_schema import ConfigError

GROUPS = ("RvR", "RvL", "LvR", "LvL")
TIERS: dict[str, dict[str, Any]] = {
    "fast": {"label": "快速（約 1 小時，開發用）", "trials": 4, "folds": 3, "tune_rows": 250_000, "members": 5, "cross_members": 3, "max_iter": 150, "bootstrap_reps": 100},
    "standard": {"label": "標準（約 3 小時，正式結果）", "trials": 8, "folds": 5, "tune_rows": 500_000, "members": 30, "cross_members": 5, "max_iter": 300, "bootstrap_reps": 200},
    "smoke": {"label": "冒煙（幾分鐘，只確認跑得通）", "trials": 2, "folds": 3, "tune_rows": 60_000, "members": 3, "cross_members": 2, "max_iter": 60, "bootstrap_reps": 50, "hidden": False},
}
STEPS = (("search", "param_search", "模型參數搜尋"), ("model", "league_model", "訓練全聯盟模型"), ("anchor", "action_effects", "固定效應對照答案"),
         ("policy", "pitch_policy", "決策引擎與情境表"), ("query", "pitch_query", "查詢工具與驗收"))


def tier_info() -> list[dict[str, Any]]:
    return [{"id": k, "label": v["label"], "trials": v["trials"], "folds": v["folds"], "members": v["members"]} for k, v in TIERS.items() if not v.get("hidden")]


def environment_info() -> dict[str, Any]:
    from . import gbm

    available = gbm.xgboost_available()
    cuda = bool(available and gbm.cuda_works())
    return {"xgboost": available, "cuda": cuda, "default_backend": "xgboost" if available else "hgb", "default_device": "cuda" if cuda else "cpu"}


def normalize_settings(raw: Mapping[str, Any]) -> dict[str, Any]:
    tier = str(raw.get("tier", "fast"))
    if tier not in TIERS:
        raise ConfigError(f"tier must be one of {list(TIERS)} / tier 必須是 {list(TIERS)} 其中之一")
    groups = list(raw.get("groups") or GROUPS)
    if set(groups) - set(GROUPS) or not groups:
        raise ConfigError(f"groups must be a non-empty subset of {list(GROUPS)} / groups 必須是 {list(GROUPS)} 的非空子集")
    env = environment_info()
    backend = str(raw.get("backend") or env["default_backend"])
    device = str(raw.get("device") or "auto")
    years = [int(y) for y in (raw.get("years") or [2023, 2024])]
    overrides = dict(raw.get("overrides") or {})
    return {"tier": tier, "groups": groups, "backend": backend, "device": device, "years": sorted(years), "seed": int(raw.get("seed", 20240601)), "overrides": overrides}


def step_config(step: str, settings: Mapping[str, Any], context: Mapping[str, Any]) -> dict[str, Any]:
    t = TIERS[settings["tier"]]
    scope = {"game_years": settings["years"], "game_types": ["R"]}
    common = {"scope": scope, "groups": settings["groups"], "seed": settings["seed"]}
    machine = {"backend": settings["backend"], "device": settings["device"]}
    best = context.get("best_params", {})
    if step == "search":
        cfg = {**common, **machine, "trials": t["trials"], "folds": t["folds"], "tune_rows": t["tune_rows"], "max_iter": t["max_iter"], "max_rows": 3_500_000}
    elif step == "model":
        cfg = {**common, **machine, **best, "max_iter": t["max_iter"], "levels": ["L0", "L1", "L2", "L3"], "bagging_reps": t["members"], "bag_levels": ["L3"], "cold_by": ["pitcher", "batter"],
               "cold_folds": t["folds"], "split": "cold_only", "bootstrap_reps": t["bootstrap_reps"], "save_package": True, "max_rows": 3_500_000}
    elif step == "anchor":
        cfg = {"scope": scope, "groups": settings["groups"], "re_scope": scope, "max_rows": 3_500_000}       # the anchor has no random part
    elif step == "policy":
        cfg = {**common, **machine, **best, "max_iter": t["max_iter"], "re_scope": scope, "members": t["members"], "cross_members": t["cross_members"], "cross_eval": True,
               "anchor_run_id": context["anchor_run_id"], "max_rows": 3_500_000}
    else:
        cfg = {"scope": scope, "policy_run_id": context["policy_run_id"], "package_run_id": context["model_run_id"], "level": "L3", "seed": settings["seed"]}
    cfg.update(settings["overrides"].get(step, {}))
    return cfg


class PipelineManager:
    """Runs one pipeline at a time in a background thread and keeps its state for the page to poll."""

    def __init__(self, service):
        self.service = service
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._job: dict[str, Any] = {"state": "idle", "steps": [], "settings": None}
        self._cancel = threading.Event()

    # --- control
    def start(self, raw: Mapping[str, Any]) -> dict[str, Any]:
        settings = normalize_settings(raw)
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                raise ConfigError("a pipeline is already running / 已有一個流程正在執行")
            self._cancel.clear()
            self._job = {"state": "running", "settings": settings, "error": None, "started_at": _now(), "finished_at": None, "summary": None, "query_run_id": None,
                         "steps": [{"id": sid, "kind": kind, "title": title, "status": "pending", "run_id": None, "seconds": None, "reused": None, "message": "", "fraction": None}
                                   for sid, kind, title in STEPS]}
            self._thread = threading.Thread(target=self._run, args=(settings,), daemon=True)
            self._thread.start()
            return self.status()

    def cancel(self) -> dict[str, Any]:
        self._cancel.set()
        return self.status()

    def status(self) -> dict[str, Any]:
        import copy

        with self._lock:
            return copy.deepcopy(self._job)

    def wait(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    # --- the job
    def _update(self, index: int | None = None, **fields) -> None:
        with self._lock:
            if index is None:
                self._job.update(fields)
            else:
                self._job["steps"][index].update(fields)

    def _run(self, settings: dict[str, Any]) -> None:
        context: dict[str, Any] = {}
        try:
            for i, (sid, kind, title) in enumerate(STEPS):
                if self._cancel.is_set():
                    self._update(state="cancelled", finished_at=_now()); return
                started = time.monotonic()
                self._update(i, status="running", message="")
                cfg = step_config(sid, settings, context)

                def progress(stage: str, fraction: float | None, detail: str | None, _i=i) -> None:
                    self._update(_i, message=(detail or stage or ""), fraction=fraction)

                try:
                    out = self.service.run(kind, cfg, study_name="P6", progress=progress)
                except Exception as exc:
                    self._update(i, status="error", message=str(exc), seconds=time.monotonic() - started)
                    self._update(state="error", error=f"步驟「{title}」失敗：{exc}（已完成的步驟會保留，修正後再按開始會從這一步繼續）", finished_at=_now())
                    return
                run = out["run"]
                self._update(i, status="done", run_id=run["id"], reused=bool(out["reused"]), seconds=time.monotonic() - started, message="沿用之前相同設定的結果" if out["reused"] else "", fraction=1.0)
                self._collect(sid, run, context)
            self._update(state="done", finished_at=_now(), summary=summarize(self.service, context, settings), query_run_id=context.get("query_run_id"))
        except Exception as exc:  # pragma: no cover - a bug, not a data problem
            self._update(state="error", error=f"{type(exc).__name__}: {exc}", finished_at=_now())

    def _collect(self, sid: str, run: Mapping[str, Any], context: dict[str, Any]) -> None:
        if sid == "search":
            context["best_params"] = dict(self.service.store.load_result(run["id"])["extras"]["best_params"])
            context["search_run_id"] = run["id"]
        elif sid == "model":
            context["model_run_id"] = run["id"]
        elif sid == "anchor":
            context["anchor_run_id"] = run["id"]
        elif sid == "policy":
            context["policy_run_id"] = run["id"]
        elif sid == "query":
            context["query_run_id"] = run["id"]


def _le(value: Any, limit: float) -> bool:
    """value <= limit; a missing value (NaN stored as null) never passes."""

    return value is not None and value == value and value <= limit


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def summarize(service, context: Mapping[str, Any], settings: Mapping[str, Any]) -> dict[str, Any]:
    """The few numbers the page shows when everything is done."""

    def rows(run_id: int | None, prefix: str) -> list[dict[str, Any]]:
        if not run_id:
            return []
        result = service.store.load_result(run_id)
        return next((s["rows"] for s in result["sections"] if s["title"].startswith(prefix)), [])

    out: dict[str, Any] = {"best_params": context.get("best_params"), "groups": {}}
    for g in settings["groups"]:
        r1 = next((r for r in rows(context.get("policy_run_id"), "R1") if r["group"] == g and str(r["item"]).startswith("states")), None)
        start = next((r for r in rows(context.get("policy_run_id"), "R1") if r["group"] == g and str(r["item"]).startswith("start")), None)
        values = [r for r in rows(context.get("policy_run_id"), "Policy value") if r["group"] == g]
        gates = [r for r in rows(context.get("model_run_id"), "Gates") if r["group"] == g]
        marg = [r for r in rows(context.get("policy_run_id"), "Marginal value") if r["group"] == g]
        r1_ok = bool(r1 and start and _le(r1["median_abs_diff"], 0.01) and _le(r1["max_abs_diff"], 0.05) and _le(start["median_abs_diff"], 0.005))
        out["groups"][g] = {
            "r1": {"passed": r1_ok, "median_abs_diff": r1 and r1["median_abs_diff"], "max_abs_diff": r1 and r1["max_abs_diff"], "start_diff": start and start["median_abs_diff"]},
            "policies": [{"policy": v["policy"], "kind": v["kind"], "saving_pa": v["saving_pa"], "lo": v["lo"], "hi": v["hi"], "saving_per_team_season": v["saving_per_team_season"]} for v in values],
            "gates": [{"side": r["side"], "gate": r["gate"], "mode": r["mode"], "value": r["value"], "lo": r["lo"], "passed": r["passed"]} for r in gates],
            "actions": [{"action": m["action"], "advantage": m["advantage"], "adv_lo": m["adv_lo"], "adv_hi": m["adv_hi"], "marginal": m["marginal"], "detected_share": m["detected_share"],
                         "supported_share": m["supported_share"]} for m in marg],
        }
    return out
