from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Mapping

from ..analysis import Filter, PITCH_GRAIN, Source
from ..analysis.run_values import all_state_codes, decode_state, re_table_node, run_context
from ..analysis_state import canonical_json, read_data_revision
from .config_schema import ConfigError
from .methods import ResearchContext
from .runner import run_checked
from .scope import compute_scope_fingerprint, normalize_scope, scope_filter_expr
from .stats import cluster_mean_se

DEFAULT_RE_SCOPE = {"game_years": [2023, 2024], "game_types": ["R"]}
_CACHE: dict[tuple[str, str, str], "ReTable"] = {}
_LOCK = threading.Lock()
_CACHE_LIMIT = 8


@dataclass(frozen=True, slots=True)
class ReTable:
    """Run expectancy per base-out-count state, learned from one data scope."""

    scope: dict[str, Any]
    states: dict[int, dict[str, Any]]      # state_code -> {n, re, se, g}; only states that occur
    re_by_state: dict[int, float]
    scope_fingerprint: str

    def missing_states(self) -> list[int]:
        return [code for code in all_state_codes() if code not in self.re_by_state]


def get_re_table(ctx: ResearchContext, re_scope: Mapping[str, Any], *, require_complete: bool = True) -> ReTable:
    scope = normalize_scope(re_scope, required=True)
    path = ctx.config.database_path
    cache_key = (str(path), read_data_revision(path), canonical_json(scope))
    with _LOCK:
        cached = _CACHE.get(cache_key)
    if cached is None:
        node = re_table_node(run_context(Filter(Source("pitches", PITCH_GRAIN), scope_filter_expr(scope)), ()))
        result = run_checked(ctx, node)
        states: dict[int, dict[str, Any]] = {}
        for row in result.rows:
            n = int(row["n"])
            if n <= 0:
                continue
            se = cluster_mean_se(n, float(row["s"]), float(row["ss"]), float(row["sn"]), float(row["nn"]), float(row["g"]))
            states[int(row["state_code"])] = {"n": n, "re": float(row["s"]) / n, "se": se, "g": int(row["g"])}
        fingerprint = compute_scope_fingerprint(path, scope)["scope_fingerprint"]
        cached = ReTable(scope, states, {code: v["re"] for code, v in states.items()}, fingerprint)
        with _LOCK:
            if len(_CACHE) >= _CACHE_LIMIT:
                _CACHE.pop(next(iter(_CACHE)))
            _CACHE[cache_key] = cached
    missing = cached.missing_states()
    if missing and require_complete:
        examples = [decode_state(code) for code in missing[:3]]
        raise ConfigError(
            f"The run-expectancy scope has no pitches in {len(missing)} of 288 states (e.g. {examples}); use a larger scope / "
            f"得分期望值範圍內有 {len(missing)} 個狀態沒有資料，請擴大 re_scope"
        )
    return cached


def re_method_inputs(ctx: ResearchContext, config: Mapping[str, Any]) -> dict[str, Any]:
    """Fingerprint of the data behind re_scope; goes into the run key so a changed RE table never reuses an old run."""

    scope = normalize_scope(config["re_scope"], required=True)
    return {"re_scope": scope, "re_scope_fingerprint": compute_scope_fingerprint(ctx.config.database_path, scope)["scope_fingerprint"]}
