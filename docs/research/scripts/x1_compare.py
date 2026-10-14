"""Compare the extra study X1 (2023-2026, S3) with the modeling period (2023-2024, S3), per rate; plus the X1 memory ladder.

usage: PYTHONPATH=src python docs/research/scripts/x1_compare.py
"""
import json
import math

from treepolo_mlb_data.config import AppConfig
from treepolo_mlb_data.research.service import ResearchService

RATES = ["whiff_per_swing", "swing_rate", "called_strike_per_take", "foul_per_swing", "in_play_per_swing", "hr_per_pitch", "mean_pitch_value"]
s = ResearchService(AppConfig(), None)
runs = s.list_runs(kind="streak_regression"); runs = runs if isinstance(runs, list) else runs["runs"]
fulls = [s.store.get_run(i["id"]) for i in runs]
fulls = [f for f in fulls if f["status"] == "success" and f["config"]["hand_view"] == "four_groups" and len(f["config"]["pitch_types"]) == 9
         and f["config"]["treatment"] == "streak_position" and sorted(f["config"]["fixed_effects"]) == ["batter", "pitcher"] and "zone" not in f["config"]["controls"]]


def table(rate, years, purpose):
    c = [f for f in fulls if f["config"]["rate"] == rate and tuple(f["config"]["scope"]["game_years"]) == years and f["config"].get("purpose") == purpose]
    rows = s.store.load_result(max(c, key=lambda f: f["id"])["id"])["sections"][0]["rows"]
    return {(r["hand_group"], r["pitch_type"], r["term"]): r for r in rows if r.get("term") in ("k=2", "k=3") and r.get("estimate") is not None and r.get("se") is not None}


summary = []
for rate in RATES:
    M, X = table(rate, (2023, 2024), "tuning"), table(rate, (2023, 2024, 2025, 2026), "extra_study")
    keys = sorted(set(M) & set(X))
    same = sum(1 for k in keys if M[k]["estimate"] * X[k]["estimate"] > 0)
    zs = [(X[k]["estimate"] - M[k]["estimate"]) / math.sqrt(X[k]["se"] ** 2 + M[k]["se"] ** 2) for k in keys]  # overlapping data: z is only a rough guide
    xd = [k for k in keys if (X[k]["lo"] > 0 or X[k]["hi"] < 0)]
    md = [k for k in keys if (M[k]["lo"] > 0 or M[k]["hi"] < 0)]
    mx = sum(abs(X[k]["estimate"]) for k in keys) / len(keys); mm = sum(abs(M[k]["estimate"]) for k in keys) / len(keys)
    summary.append({"rate": rate, "cells": len(keys), "same_sign": same, "ci99_excl0_modeling": len(md), "ci99_excl0_X1": len(xd), "mean_abs_est_modeling": round(mm, 4), "mean_abs_est_X1": round(mx, 4),
                    "share_abs_z_gt2": round(sum(1 for z in zs if abs(z) > 2) / len(zs), 2), "mean_se_ratio_X1_over_model": round(sum(X[k]["se"] / M[k]["se"] for k in keys) / len(keys), 2)})
for r in summary: print(json.dumps(r, ensure_ascii=False))
json.dump(summary, open("docs/research/summaries/x1_vs_modeling_streak.json", "w"), ensure_ascii=False, indent=1)
log = [json.loads(l) for l in open("docs/research/summaries/x1_log.jsonl")]
rid = [d["run_id"] for d in log if d["config"].startswith("A8") and d["run_id"]][-1]
for sec in s.store.load_result(rid)["sections"]:
    if sec["rows"] and "comparison" in sec["rows"][0] and "se" in sec["rows"][0]:
        for r in sec["rows"]:
            if r["comparison"] in ("base→m1", "m1→m2", "m2→m3"): print("A8", r["group"], r["comparison"], round(r["delta_logloss"], 5), round(r["lo"], 5), round(r["hi"], 5))
    if sec["rows"] and sec["rows"][0].get("variant") == "base" and "logloss" in sec["rows"][0]:
        for r in sec["rows"]:
            if r["variant"] == "base": print("A8 base", r["group"], round(r["logloss"], 4), r["n_eval"])
