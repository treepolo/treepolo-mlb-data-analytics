"""One-click research pipeline: model settings search (can be skipped) -> trained outcome model -> fixed-effects anchor -> decision engine and situation table -> query tool.

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
STEPS = (("search", "param_search", "模型參數搜尋"), ("model", "outcome_model_train", "訓練模型"), ("anchor", "action_effects", "固定效應對照答案"),
         ("policy", "pitch_policy", "決策引擎與情境表"), ("query", "pitch_query", "查詢工具與驗收"))


FILE_FORMAT = 1
FILE_STEP_KEYS = {"search": "search", "model": "train", "anchor": "anchor", "policy": "policy", "query": "query"}      # the names in a settings file
AUTO_FIELDS = {"policy": ("model_run_id", "anchor_run_id"), "query": ("policy_run_id", "package_run_id")}           # filled in from the earlier steps while the flow runs
OPTIONAL_STEPS = ("search", "anchor")
FROM_THE_TOP = ("scope", "groups")                                                                                    # one value for all steps, at the top of the file


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
    from .outcome_models import controls as ctl

    model = str(raw.get("model") or "gbm")
    if model not in ctl.MODEL_KINDS:
        raise ConfigError(f"model must be one of {list(ctl.MODEL_KINDS)} / model 必須是 {list(ctl.MODEL_KINDS)} 其中之一")
    controls = dict(raw.get("controls") or {})
    choices = {f.name: f.choices for f in ctl.CONTROL_FIELDS if f.type == "choice"}
    for name, value in controls.items():
        if name not in choices or value not in choices[name]:
            raise ConfigError(f"controls: {name}={value!r} is not valid / controls：{name}={value!r} 不合法")
    return {"tier": tier, "groups": groups, "backend": backend, "device": device, "years": sorted(years), "seed": int(raw.get("seed", 20240601)), "overrides": overrides,
            "model": model, "controls": controls, "search": bool(raw.get("search", True))}


def _file_step_config(step: str, settings: Mapping[str, Any], context: Mapping[str, Any]) -> dict[str, Any]:
    """A step's settings from a settings file: the user's values, the scope and the groups from the top of the file, the run numbers from the earlier steps."""

    from .methods import get_method

    kind = next(k for sid, k, _ in STEPS if sid == step)
    scope = {"game_years": settings["years"], "game_types": ["R"]}
    cfg = dict(settings["steps"].get(step, {}))
    names = {f.name for f in get_method(kind).fields}
    if "groups" in names:
        cfg["groups"] = list(settings["groups"])
    cfg["scope"] = scope
    for name, source in (("model_run_id", "model_run_id"), ("anchor_run_id", "anchor_run_id"), ("policy_run_id", "policy_run_id"), ("package_run_id", "model_run_id")):
        if name in AUTO_FIELDS.get(step, ()):
            cfg[name] = int(context.get(source) or 0)
    if step in ("anchor", "policy") and not cfg.get("re_scope"):
        cfg["re_scope"] = scope
    return cfg


def step_config(step: str, settings: Mapping[str, Any], context: Mapping[str, Any]) -> dict[str, Any]:
    if settings.get("steps") is not None:
        return _file_step_config(step, settings, context)
    t = TIERS[settings["tier"]]
    scope = {"game_years": settings["years"], "game_types": ["R"]}
    common = {"scope": scope, "groups": settings["groups"], "seed": settings["seed"]}
    machine = {"backend": settings["backend"], "device": settings["device"]}
    model = {"model": settings["model"], **settings["controls"]}
    best = context.get("best_params", {})
    if step == "search":
        cfg = {**common, **machine, **model, "trials": t["trials"], "folds": t["folds"], "tune_rows": t["tune_rows"], "max_iter": t["max_iter"], "max_rows": 3_500_000}
    elif step == "model":
        cfg = {**common, **machine, **model, **best, "max_iter": t["max_iter"], "bagging_reps": t["members"], "cold_by": ["pitcher", "batter"], "cold_folds": t["folds"], "split": "cold_only",
               "bootstrap_reps": t["bootstrap_reps"], "save_package": True, "replication_members": 0, "max_rows": 3_500_000}
    elif step == "anchor":
        cfg = {"scope": scope, "groups": settings["groups"], "re_scope": scope, "max_rows": 3_500_000}       # the anchor has no random part
    elif step == "policy":
        cfg = {**common, "re_scope": scope, "model_run_id": context["model_run_id"], "cross_members": t["cross_members"], "replication_members": min(3, t["cross_members"]), "cross_eval": True,
               "anchor_run_id": context.get("anchor_run_id", 0), "max_rows": 3_500_000}
    else:
        cfg = {"scope": scope, "policy_run_id": context["policy_run_id"], "package_run_id": context["model_run_id"], "seed": settings["seed"]}
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
        settings = self._settings_from_file(raw["file"]) if raw.get("file") is not None else normalize_settings(raw)
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

    def _settings_from_file(self, doc: Any) -> dict[str, Any]:
        errors = validate_document(self.service, doc)
        if errors:
            shown = "；".join(f"{e['step'] or '檔案'}{('/' + e['field']) if e['field'] else ''}: {e['message']}" for e in errors[:5])
            raise ConfigError(f"the settings file is not valid / 設定檔不合法：{shown}")
        enabled = dict(doc.get("enabled") or {})
        steps = {sid: dict((doc["steps"] or {}).get(FILE_STEP_KEYS[sid], {})) for sid, _, _ in STEPS}
        train = steps.get("model", {})
        return {"tier": "custom", "groups": list(doc["groups"]), "years": sorted(int(y) for y in doc["years"]), "backend": train.get("backend", "xgboost"), "device": train.get("device", "auto"),
                "seed": int(train.get("seed", 20240601)), "overrides": {}, "model": train.get("model", "gbm"), "controls": {}, "steps": steps, "enabled": enabled,
                "search": bool(enabled.get("search", True)) and bool(steps.get("search"))}

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
                if sid == "search" and not settings["search"]:                 # the settings typed in the full settings mode are used as they are
                    self._update(i, status="done", message="略過：使用設定裡填的參數", seconds=0.0, fraction=1.0, reused=False)
                    context["best_params"] = {}
                    continue
                if sid == "anchor" and (settings.get("enabled") or {}).get("anchor") is False:
                    self._update(i, status="done", message="略過：不做固定效應對照", seconds=0.0, fraction=1.0, reused=False)
                    context["anchor_run_id"] = 0
                    continue
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


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def summarize(service, context: Mapping[str, Any], settings: Mapping[str, Any]) -> dict[str, Any]:
    """The few numbers the page shows when everything is done."""

    def rows(run_id: int | None, prefix: str) -> list[dict[str, Any]]:
        if not run_id:
            return []
        result = service.store.load_result(run_id)
        return next((s["rows"] for s in result["sections"] if s["title"].startswith(prefix)), [])

    policy_extras = service.store.load_result(context["policy_run_id"])["extras"] if context.get("policy_run_id") else {}
    model_extras = service.store.load_result(context["model_run_id"])["extras"] if context.get("model_run_id") else {}
    from .outcome_models import controls as ctl

    out: dict[str, Any] = {"best_params": context.get("best_params") or None, "groups": {}, "replication": policy_extras.get("replication"),
                           "model": {"kind": model_extras.get("model"), "controls": {k: v for k, v in (model_extras.get("controls") or {}).items() if v != ctl.CONTROL_DEFAULTS.get(k)}}}
    for g in settings["groups"]:
        r1 = next((r for r in rows(context.get("policy_run_id"), "R1") if r["group"] == g and str(r["item"]).startswith("states")), None)
        start = next((r for r in rows(context.get("policy_run_id"), "R1") if r["group"] == g and str(r["item"]).startswith("start")), None)
        values = [r for r in rows(context.get("policy_run_id"), "Policy value") if r["group"] == g]
        gains = [r for r in rows(context.get("model_run_id"), "Effect gain") if r["group"] == g]
        marg = [r for r in rows(context.get("policy_run_id"), "Marginal value") if r["group"] == g]
        out["groups"][g] = {
            # how far the engine's observed-play value is from the run expectancy table: numbers only (0 = identical)
            "r1": {"median_abs_diff": r1 and r1["median_abs_diff"], "max_abs_diff": r1 and r1["max_abs_diff"], "start_diff": start and start["median_abs_diff"]},
            "policies": [{"policy": v["policy"], "kind": v["kind"], "saving_pa": v["saving_pa"], "lo": v["lo"], "hi": v["hi"], "saving_per_team_season": v["saving_per_team_season"]} for v in values],
            "effect_gains": [{"mode": r["mode"], "comparison": r["comparison"], "value": r["delta_logloss"], "lo": r["lo"], "hi": r["hi"], "n": r["n"]} for r in gains],
            "actions": [{"action": m["action"], "advantage": m["advantage"], "adv_lo": m["adv_lo"], "adv_hi": m["adv_hi"], "marginal": m["marginal"], "supported_share": m["supported_share"]} for m in marg],
            "comparisons": {section: per_group[g] for section, per_group in (policy_extras.get("comparisons") or {}).items() if g in per_group},
        }
    return out


# ------------------------------------------------------------------------------------------------ settings files (full settings mode)
def _jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def full_config(raw: Mapping[str, Any]) -> dict[str, Any]:
    """The settings file that matches the simple-mode choices in ``raw`` (tier, groups, years, model, controls): every setting of every step with its value, ready to edit."""

    from .config_schema import normalize_config
    from .methods import get_method

    settings = normalize_settings(raw)
    steps: dict[str, Any] = {}
    placeholders = {"model_run_id": 0, "anchor_run_id": 0, "policy_run_id": 0}
    for sid, kind, _ in STEPS:
        cfg = step_config(sid, settings, placeholders)
        for name in FROM_THE_TOP + AUTO_FIELDS.get(sid, ()):
            cfg.pop(name, None)
        if sid in ("anchor", "policy"):
            cfg.pop("re_scope", None)                                    # the scope of the run-expectancy table is the scope of the file unless the user writes one
        full = normalize_config(get_method(kind).fields, cfg)
        for name in FROM_THE_TOP + AUTO_FIELDS.get(sid, ()):
            full.pop(name, None)
        if sid in ("anchor", "policy"):
            full.pop("re_scope", None)
        steps[FILE_STEP_KEYS[sid]] = _jsonable(full)
    return {"format": FILE_FORMAT, "created_at": _now(), "groups": list(settings["groups"]), "years": list(settings["years"]), "steps": steps,
            "enabled": {"search": bool(settings["search"]), "anchor": True}}


def validate_document(service, doc: Any) -> list[dict[str, str]]:
    """Every problem of a settings file, one entry per step and setting (empty list = valid). Run numbers of earlier steps are not checked (they do not exist yet)."""

    from .config_schema import _normalize_value, normalize_config
    from .methods import ResearchContext, get_method
    from .scope import normalize_scope

    errors: list[dict[str, str]] = []

    def err(step: str, field: str, message: str) -> None:
        errors.append({"step": step, "field": field, "message": message})

    if not isinstance(doc, Mapping):
        return [{"step": "", "field": "", "message": "設定檔必須是 JSON 物件 / the file must be a JSON object"}]
    if doc.get("format") != FILE_FORMAT:
        err("", "format", f"format 必須是 {FILE_FORMAT} / format must be {FILE_FORMAT}")
    groups = doc.get("groups")
    if not isinstance(groups, list) or not groups or set(map(str, groups)) - set(GROUPS):
        err("", "groups", f"groups 必須是 {list(GROUPS)} 的非空子集 / groups must be a non-empty subset of {list(GROUPS)}")
    years = doc.get("years")
    if not isinstance(years, list) or not years or not all(isinstance(y, int) and not isinstance(y, bool) for y in years):
        err("", "years", "years 必須是整數年份的非空列表 / years must be a non-empty list of integers")
    steps = doc.get("steps")
    if not isinstance(steps, Mapping):
        err("", "steps", "steps 必須是物件 / steps must be an object")
        return errors
    enabled = doc.get("enabled") or {}
    if not isinstance(enabled, Mapping) or set(enabled) - {FILE_STEP_KEYS[s] for s in OPTIONAL_STEPS} or not all(isinstance(v, bool) for v in enabled.values()):
        err("", "enabled", f"enabled 只能含 {[FILE_STEP_KEYS[s] for s in OPTIONAL_STEPS]}，值是 true 或 false / enabled may hold only those steps, as true or false")
    by_key = {FILE_STEP_KEYS[sid]: (sid, kind) for sid, kind, _ in STEPS}
    for key in steps:
        if key not in by_key:
            err(str(key), "", f"未知的步驟 {key!r}；可用 {list(by_key)} / unknown step")
    scope = None
    if isinstance(years, list) and years and all(isinstance(y, int) for y in years):
        try:
            scope = normalize_scope({"game_years": sorted(years), "game_types": ["R"]}, required=True)
        except ConfigError as exc:
            err("", "years", str(exc))
    auto_names = {n for names in AUTO_FIELDS.values() for n in names}
    for key, (sid, kind) in by_key.items():
        cfg = steps.get(key)
        if cfg is None:
            err(key, "", "缺少這個步驟的設定 / this step has no settings")
            continue
        if not isinstance(cfg, Mapping):
            err(key, "", "步驟設定必須是物件 / step settings must be an object")
            continue
        method = get_method(kind)
        fields = {f.name: f for f in method.fields}
        clean: dict[str, Any] = {}
        before = len(errors)
        for name, value in cfg.items():
            if name in FROM_THE_TOP or name in AUTO_FIELDS.get(sid, ()) or name == "re_scope" and sid in ("anchor", "policy"):
                continue                                                  # taken from the top of the file or from the earlier steps
            if name not in fields:
                err(key, name, "未知的設定 / unknown setting")
                continue
            try:
                clean[name] = _normalize_value(fields[name], value)
            except ConfigError as exc:
                err(key, name, str(exc))
        if len(errors) > before or scope is None:
            continue
        if "groups" in fields and isinstance(groups, list) and groups:
            clean["groups"] = [str(g) for g in groups]
        try:
            normalized = normalize_config(method.fields, clean)
            for name in ("re_scope",):
                if name in normalized and not normalized[name]:
                    normalized[name] = scope
            method.validate(normalized, ResearchContext(service.config, None, scope, None, service.store))
        except ConfigError as exc:
            message = str(exc)
            if any(n in message for n in auto_names) or "outcome_model_train" in message and sid in ("policy", "query"):
                continue                                                  # a run number the earlier steps will supply
            mentioned = [n for n in fields if n in message]
            err(key, mentioned[0] if mentioned else "", message)
    return errors
