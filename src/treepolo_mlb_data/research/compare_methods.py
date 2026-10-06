from __future__ import annotations

import math
from typing import Any

from .config_schema import ConfigError, ConfigField
from .methods import ResearchContext, ResearchMethod, ResearchResult, register_method
from .sections import make_section
from .stats import z_value
from .store import ResearchStore

IGNORED_CONFIG_KEYS = ("scope", "purpose", "train_years", "test_years")


def _find_section(result: dict[str, Any], prefix: str) -> dict[str, Any]:
    for section in result.get("sections", []):
        if str(section.get("title", "")).startswith(prefix):
            return section
    raise ConfigError(f"No result section starts with {prefix!r} / 找不到標題以 {prefix!r} 開頭的結果節")


def _keyed(section: dict[str, Any], keys: list[str]) -> dict[tuple, dict[str, Any]]:
    missing = [k for k in keys if k not in section.get("columns", [])]
    if missing:
        raise ConfigError(f"key_columns not in the section: {missing} / key_columns 不在結果節中: {missing}")
    out: dict[tuple, dict[str, Any]] = {}
    for row in section.get("rows", []):
        key = tuple(row.get(k) for k in keys)
        if key in out:
            raise ConfigError(f"Duplicate key {key}; choose key_columns that identify a row / key_columns 無法唯一識別列")
        out[key] = row
    return out


class RunCompareMethod(ResearchMethod):
    """Cell-by-cell comparison of one result section of two recorded runs (b relative to a)."""

    kind = "run_compare"
    version = 1
    label_zh = "兩次研究結果比較"
    label_en = "Compare two runs"
    requires_scope = False
    fields = (
        ConfigField("run_key_a", "str", required=True, label_zh="參考紀錄的 run_key", label_en="run_key of the reference run"),
        ConfigField("run_key_b", "str", required=True, label_zh="比較紀錄的 run_key", label_en="run_key of the compared run"),
        ConfigField("section", "str", required=True, label_zh="結果節標題開頭", label_en="Start of the section title"),
        ConfigField("key_columns", "str_list", default=[], label_zh="配對用欄位（空白＝該節的 grain 欄位）", label_en="Key columns (empty = the section grain)"),
        ConfigField("estimate", "str", required=True, label_zh="估計值欄位", label_en="Estimate column"),
        ConfigField("se", "str", default="", label_zh="標準誤欄位（空白＝不算 z）", label_en="Standard error column (empty = no z)"),
        ConfigField("min_abs_diff", "float", default=0.0, minimum=0.0, label_zh="只列出差距至少此值的列", label_en="List only rows with at least this difference"),
        ConfigField("confidence", "float", default=0.95, minimum=0.5, maximum=0.999, label_zh="信賴水準（臨界 z）", label_en="Confidence level (critical z)"),
    )

    def run(self, ctx: ResearchContext, config: dict[str, Any]) -> ResearchResult:
        store = ResearchStore(ctx.config.research_state_database_path, ctx.config.research_blob_dir)
        try:
            runs = {}
            for tag in ("a", "b"):
                found = store.find_run_by_key(config[f"run_key_{tag}"])
                if found is None or found["status"] != "success":
                    raise ConfigError(f"run_key_{tag} is not a successful run in the local research store / run_key_{tag} 不是本機研究庫中成功的紀錄")
                result = store.load_result(found["id"])
                if result is None:
                    raise ConfigError(f"run_key_{tag} has no stored result / run_key_{tag} 沒有結果")
                runs[tag] = (found, result)
        finally:
            store.close()
        (run_a, res_a), (run_b, res_b) = runs["a"], runs["b"]
        section_a, section_b = _find_section(res_a, config["section"]), _find_section(res_b, config["section"])
        keys = list(config["key_columns"]) or list(section_a.get("grain", {}).get("keys", []))
        if not keys:
            raise ConfigError("key_columns is empty and the section has no grain keys / 沒有可用的配對欄位")
        for name in (config["estimate"], config["se"]):
            if name and (name not in section_a.get("columns", []) or name not in section_b.get("columns", [])):
                raise ConfigError(f"column {name!r} is missing in one of the sections / 欄位 {name!r} 不在兩個結果節中")
        rows_a, rows_b = _keyed(section_a, keys), _keyed(section_b, keys)
        z_crit = z_value(config["confidence"])
        compare: list[dict[str, Any]] = []
        for key in sorted(set(rows_a) & set(rows_b), key=lambda k: tuple(str(x) for x in k)):
            a, b = rows_a[key][config["estimate"]], rows_b[key][config["estimate"]]
            if a is None or b is None:
                continue
            diff = b - a
            if abs(diff) < config["min_abs_diff"]:
                continue
            se_diff = z = None
            if config["se"]:
                sa, sb = rows_a[key][config["se"]], rows_b[key][config["se"]]
                if sa is not None and sb is not None:
                    se_diff = math.sqrt(sa * sa + sb * sb)
                    z = diff / se_diff if se_diff > 0 else None
            compare.append({**dict(zip(keys, key)), "a": a, "b": b, "diff": diff, "se_diff": se_diff, "z": z})
        compare.sort(key=lambda r: (r["z"] is None, -abs(r["z"]) if r["z"] is not None else -abs(r["diff"])))
        zs = [r["z"] for r in compare if r["z"] is not None]
        unmatched = [{"side": "only_a", "key": str(k)} for k in sorted(set(rows_a) - set(rows_b), key=str)] + \
                    [{"side": "only_b", "key": str(k)} for k in sorted(set(rows_b) - set(rows_a), key=str)]
        cfg_a, cfg_b = run_a["config"], run_b["config"]
        diffs = [{"key": k, "a": str(cfg_a.get(k)), "b": str(cfg_b.get(k))} for k in sorted(set(cfg_a) | set(cfg_b))
                 if k not in IGNORED_CONFIG_KEYS and cfg_a.get(k) != cfg_b.get(k)]
        extras = {"run_a": {"kind": run_a["kind"], "scope": run_a["scope"]}, "run_b": {"kind": run_b["kind"], "scope": run_b["scope"]},
                  "n_matched": len(compare), "n_unmatched": len(unmatched), "key_columns": keys,
                  "share_abs_z_above_critical": sum(abs(z) > z_crit for z in zs) / len(zs) if zs else None,
                  "mean_z": sum(zs) / len(zs) if zs else None, "direction": "diff = b - a"}
        return ResearchResult(sections=(
            make_section("Compare 比較（diff = b − a）", (*keys, "a", "b", "diff", "se_diff", "z"), compare, tuple(keys), "store"),
            make_section("Unmatched 未配對", ("side", "key"), unmatched, ("side", "key"), "store"),
            make_section("Config differences 設定差異（不含 scope、purpose、train_years、test_years）", ("key", "a", "b"), diffs, ("key",), "store"),
        ), extras=extras)


register_method(RunCompareMethod())
