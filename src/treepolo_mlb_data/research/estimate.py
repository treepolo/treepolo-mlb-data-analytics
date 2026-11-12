"""Time estimate before a training or comparison run: a short trial of each model on a small sample, scaled up to the full data by the cost rule of the model.

The trial trains the model once on about 2% of the training rows (boosted trees for 20 rounds, a neural network for one epoch, a logistic regression to the end) and times it. The
full time is that time scaled by the number of rows and the number of rounds, epochs or members, times the number of fits the run needs (the main fit, the folds over unseen players,
the no-controls baseline). It is a rough number: the real time can be half or twice as long, and the trial itself takes time too, which is reported.
"""
from __future__ import annotations

import sqlite3
import time
from typing import Any, Mapping

from .config_schema import ConfigError
from .guards import scope_years
from .lazy import Lazy
from .methods import ResearchContext
from .outcome_models import controls as ctl
from .scope import normalize_scope

np = Lazy("numpy")
lc = Lazy(".league_core")
om = Lazy(".outcome_models")
gbm = Lazy(".gbm")

NOTE = "粗估，實際可能是 0.5 到 2 倍 / A rough estimate: the real time may be 0.5 to 2 times"
SAMPLE_FRACTION = 0.02
MIN_SAMPLE_ROWS = 4000


def group_counts(config, years) -> dict[tuple[str, int], int]:
    """Rows of every (hand group, year) in regular-season play, counted straight from the database."""

    conn = sqlite3.connect(f"file:{config.database_path}?mode=ro", uri=True)
    try:
        have = {r[1] for r in conn.execute("PRAGMA table_info(pitches)")}
        marks = ",".join("?" for _ in years)
        if {"p_throws", "stand", "game_year"} <= have:
            where = f"game_year IN ({marks})" + (" AND game_type = 'R'" if "game_type" in have else "")
            return {(f"{p}v{s}", int(y)): int(n) for p, s, y, n in conn.execute(f"SELECT p_throws, stand, game_year, COUNT(*) FROM pitches WHERE {where} GROUP BY p_throws, stand, game_year", list(years))}
        return {}
    finally:
        conn.close()


def _fits(cold_by, cold_folds: int, with_baseline: bool) -> float:
    """How many full-size fits a run needs, counting a fold as (folds - 1) / folds of one."""

    folds = len(cold_by) * (cold_folds - 1) if cold_by else 0
    one = 1.0 + folds
    return one * (2.0 if with_baseline else 1.0)


def scale(rule: str, probe_seconds: float, probe_rows: int, rows: int, st: Mapping[str, Any], probe_unit: int) -> float:
    """Seconds for one full-size fit of the settings ``st``."""

    per_row = probe_seconds / max(probe_rows, 1)
    members = int(st.get("bagging_reps", 1))
    if rule == "rounds":                       # trees: rows x rounds x members
        return per_row * rows * (int(st["max_iter"]) / max(probe_unit, 1)) * members
    if rule == "epochs":                       # neural networks: rows x epochs x members
        return per_row * rows * (int(st.get("epochs", 1)) / max(probe_unit, 1)) * members
    if rule == "iterations":                   # mixed effects: rows x iterations x members
        return per_row * rows * (int(st.get("max_iter", 1)) / max(probe_unit, 1)) * members
    return per_row * rows * members            # linear: the trial ran to the end, so only the rows grow


def estimate(service, payload: Mapping[str, Any]) -> dict[str, Any]:
    """``payload``: models, groups, years, train_years, cold_by, cold_folds, compare_baseline, settings (shared), overrides (per model)."""

    from .league_methods import fetch_columns

    models = list(payload.get("models") or [])
    if not models:
        raise ConfigError("models is required / 需要 models")
    years = sorted(int(y) for y in (payload.get("years") or [2023, 2024]))
    train_years = sorted(int(y) for y in (payload.get("train_years") or years[:1]))
    groups = list(payload.get("groups") or ["RvR"])
    cold_by = list(payload.get("cold_by") if payload.get("cold_by") is not None else ["pitcher", "batter"])
    folds = int(payload.get("cold_folds") or 3)
    shared = dict(payload.get("settings") or {}); overrides = dict(payload.get("overrides") or {})
    with_baseline = bool(payload.get("compare_baseline", False))
    scope = normalize_scope({"game_years": years, "game_types": ["R"]}, required=True)
    ctx = ResearchContext(service.config, None, scope, None, service.store)
    counts = group_counts(service.config, years)
    started = time.monotonic()
    rows_out, skipped = [], []
    from .outcome_models.base import fill_defaults

    for group in groups:
        n_train = sum(counts.get((group, y), 0) for y in train_years)
        probe_rows = max(MIN_SAMPLE_ROWS, int(n_train * SAMPLE_FRACTION)) if n_train else MIN_SAMPLE_ROWS
        fetch_cfg = {"bunt_policy": "exclude_plate_appearance", "filters": [], "max_rows": 10_000_000}
        cols = fetch_columns(ctx, fetch_cfg, [group], train_years[:1], row_limit=probe_rows)
        if len(cols["game_pk"]) == 0:
            raise ConfigError(f"Group {group} has no rows to time / 分組 {group} 沒有資料可試跑")
        _, classes, _ = lc.encode_outcome(cols["outcome"], fill_defaults({})["class_merge"])
        for name in models:
            try:
                cls = om.get_model_class(name)
            except ConfigError as exc:
                if not any(s["model"] == name for s in skipped):
                    skipped.append({"model": name, "reason": str(exc)})
                continue
            st = fill_defaults({**shared, **overrides.get(name, {})})
            ctl.validate_controls(st, model=name, backend=st["backend"])
            probe = fill_defaults({**st, **cls.probe_settings})
            t0 = time.monotonic()
            model = cls()
            model.fit(cols, None, probe, None, classes=classes)
            probe_seconds = time.monotonic() - t0
            one = scale(cls.cost_rule, probe_seconds, len(cols["game_pk"]), n_train or len(cols["game_pk"]), st, cls.probe_unit)
            seconds = one * _fits(cold_by, folds, with_baseline)
            rows_out.append({"group": group, "model": name, "rows_train": int(n_train), "probe_rows": int(len(cols["game_pk"])), "probe_seconds": probe_seconds, "seconds_one_fit": one,
                             "fits": _fits(cold_by, folds, with_baseline), "seconds": seconds, "low": seconds * 0.5, "high": seconds * 2.0, "rule": cls.cost_rule})
    return {"estimates": rows_out, "skipped": skipped, "total_seconds": float(sum(r["seconds"] for r in rows_out)), "trial_seconds": time.monotonic() - started, "note": NOTE,
            "assumptions": {"sample_fraction": SAMPLE_FRACTION, "cold_by": cold_by, "cold_folds": folds, "compare_baseline": with_baseline,
                            "rule": "trees: rows x rounds x members; neural network: rows x epochs x members; logistic: rows x members; a trial on about 2% of the rows"}}
