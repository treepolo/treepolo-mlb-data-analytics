"""Grade the frozen final tests (plan 2.3/2.4): for every cell detected in the modeling period, compare S3 in 2025 (F1) and 2026 (F2).

usage: PYTHONPATH=src python docs/research/scripts/grade_final.py
Detection rule re-applied exactly as in summarize_streak.py (criteria 1-5, 99% intervals, 1pp or 0.002 for mean_pitch_value; hr_per_pitch 0.002 post hoc).
Grade: strong = same sign in 2025 and (|z of difference| < 2 or 2025 interval excludes 0); medium = 2025 interval contains 0 and the modeling estimate;
refuted = 2025 sign flipped and 2025 interval excludes the modeling estimate; otherwise weak.
"""
import json
import math
from collections import Counter

from treepolo_mlb_data.config import AppConfig
from treepolo_mlb_data.research.service import ResearchService

RATES = ["whiff_per_swing", "swing_rate", "called_strike_per_take", "foul_per_swing", "in_play_per_swing", "hr_per_pitch", "mean_pitch_value"]
service = ResearchService(AppConfig(), None)
runs = service.list_runs(kind="streak_regression")
runs = runs if isinstance(runs, list) else runs["runs"]
fulls = [service.store.get_run(i["id"]) for i in runs]
fulls = [f for f in fulls if f["status"] == "success" and f["config"]["hand_view"] == "four_groups" and len(f["config"]["pitch_types"]) == 9 and f["config"]["treatment"] == "streak_position"]


def spec_of(c):
    fe = tuple(sorted(c["fixed_effects"]))
    spec = {(): "S2", ("batter", "pitcher"): "S3", ("batter", "pitcher_game"): "S4"}.get(fe)
    return "S5" if spec == "S3" and "zone" in c["controls"] else spec


def table(rate, spec, years, purpose):
    cands = [f for f in fulls if f["config"]["rate"] == rate and spec_of(f["config"]) == spec and tuple(f["config"]["scope"]["game_years"]) == years and f["config"].get("purpose") == purpose]
    if not cands:
        return {}
    rows = service.store.load_result(max(cands, key=lambda f: f["id"])["id"])["sections"][0]["rows"]
    return {(r["hand_group"], r["pitch_type"], r["term"]): r for r in rows if r.get("term") in ("k=2", "k=3") and r.get("estimate") is not None and r.get("se") is not None}


def sign(v):
    return (v > 0) - (v < 0)


report = []
summary = Counter()
for rate in RATES:
    size = 0.002 if rate in ("mean_pitch_value", "hr_per_pitch") else 0.01
    P, Y23, Y24, S4 = (table(rate, "S3", (2023, 2024), "tuning"), table(rate, "S3", (2023,), "tuning"), table(rate, "S3", (2024,), "tuning"), table(rate, "S4", (2023, 2024), "tuning"))
    T25, T26 = table(rate, "S3", (2025,), "final_test"), table(rate, "S3", (2026,), "final_test")
    U25, U26 = table(rate, "S4", (2025,), "final_test"), table(rate, "S4", (2026,), "final_test")
    for key, r in sorted(P.items()):
        est = r["estimate"]
        ok = ((r["lo"] > 0 or r["hi"] < 0) and key in S4 and est * S4[key]["estimate"] > 0 and abs(S4[key]["estimate"]) >= 0.5 * abs(est)
              and all(key in y and y[key]["estimate"] * est > 0 and abs(y[key]["estimate"]) >= abs(est) / 3 for y in (Y23, Y24)) and abs(est) >= size and not r["low_clusters"])
        if not ok:
            continue
        row = {"rate": rate, "key": list(key), "model": round(est, 4), "lo": round(r["lo"], 4), "hi": round(r["hi"], 4)}
        for tag, T in (("2025", T25), ("2026", T26)):
            t = T.get(key)
            if t is None or t.get("low_clusters"):
                row[tag] = {"grade": "no data"}
                continue
            z = (t["estimate"] - est) / math.sqrt(t["se"] ** 2 + r["se"] ** 2)
            same = sign(t["estimate"]) == sign(est)
            excl0 = t["lo"] > 0 or t["hi"] < 0
            if same and (abs(z) < 2 or excl0):
                grade = "strong"
            elif t["lo"] <= 0 <= t["hi"] and t["lo"] <= est <= t["hi"]:
                grade = "medium"
            elif not same and not (t["lo"] <= est <= t["hi"]):
                grade = "refuted"
            else:
                grade = "weak"
            row[tag] = {"est": round(t["estimate"], 4), "lo": round(t["lo"], 4), "hi": round(t["hi"], 4), "z": round(z, 2), "same_sign": same, "excl0": excl0, "grade": grade}
        summary[(rate, "2025", row["2025"]["grade"])] += 1
        summary[(rate, "2026", row["2026"]["grade"])] += 1
        report.append(row)
print(json.dumps({"detected_cells": len(report)}, ensure_ascii=False))
for rate in RATES:
    for tag in ("2025", "2026"):
        c = {g: summary[(rate, tag, g)] for g in ("strong", "medium", "weak", "refuted", "no data") if summary[(rate, tag, g)]}
        print(rate, tag, c)
with open("docs/research/summaries/final_test_streak_grades.json", "w", encoding="utf-8") as h:
    json.dump(report, h, ensure_ascii=False, indent=1)
for row in report:
    if row["rate"] in ("whiff_per_swing", "mean_pitch_value"):
        a, b = row["2025"], row["2026"]
        print(row["rate"][:6], *row["key"], f"model {row['model']:+.4f}", "| 2025", a.get("est"), a["grade"], "| 2026", b.get("est"), b["grade"])
