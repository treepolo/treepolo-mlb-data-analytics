"""Grade the frozen previous-pitch tests (Q2): cells significant in the modeling period (99% CI excludes 0, size threshold), same sign / interval in 2025 and 2026."""
import json
from collections import Counter

from treepolo_mlb_data.config import AppConfig
from treepolo_mlb_data.research.service import ResearchService

s = ResearchService(AppConfig(), None)
runs = s.list_runs(kind="streak_regression"); runs = runs if isinstance(runs, list) else runs["runs"]
tabs = {}
for i in runs:
    f = s.store.get_run(i["id"]); c = f["config"]
    if f["status"] != "success" or c["treatment"] != "prev1_pitch_type" or c["hand_view"] != "four_groups":
        continue
    yrs = tuple(c["scope"]["game_years"]); purpose = c.get("purpose")
    rows = {(r["hand_group"], r["pitch_type"], r["term"]): r for r in s.store.load_result(f["id"])["sections"][0]["rows"] if r.get("term") and r.get("estimate") is not None and r.get("se") is not None}
    tabs[(c["rate"], yrs, purpose)] = (f["id"], rows)
out, report = {}, []
for rate in ("whiff_per_swing", "swing_rate", "mean_pitch_value"):
    size = 0.002 if rate == "mean_pitch_value" else 0.01
    M = tabs[(rate, (2023, 2024), "tuning")][1]
    det = {k: r for k, r in M.items() if (r["lo"] > 0 or r["hi"] < 0) and abs(r["estimate"]) >= size and not r["low_clusters"]}
    for yr in (2025, 2026):
        T = tabs[(rate, (yr,), "final_test")][1]
        c = Counter()
        for k, r in det.items():
            t = T.get(k)
            if t is None or t["low_clusters"]:
                c["no data"] += 1; continue
            same = t["estimate"] * r["estimate"] > 0
            excl0 = t["lo"] > 0 or t["hi"] < 0
            c["same sign & interval excludes 0" if same and excl0 else "same sign, interval has 0" if same else "opposite sign"] += 1
        out[f"{rate} {yr}"] = {"detected_modeling": len(det), **c}
        print(rate, yr, len(det), dict(c))
json.dump(out, open("docs/research/summaries/final_test_prev1_grades.json", "w"), ensure_ascii=False, indent=1)
