"""Apply the pre-registered detection criteria (P5 plan 2.3) to the A6 streak_regression runs of one rate.

usage: PYTHONPATH=src python docs/research/scripts/summarize_streak.py RATE [MIN_ABS_DIFF] [--markdown]
Prints one row per (hand group, pitch type, k) for k = 2, 3 and the counts the plan asks for.
"""
import sys

from treepolo_mlb_data.config import AppConfig
from treepolo_mlb_data.research.service import ResearchService

rate = sys.argv[1]
min_size = float(sys.argv[2]) if len(sys.argv) > 2 and not sys.argv[2].startswith("--") else (0.002 if rate == "mean_pitch_value" else 0.01)
service = ResearchService(AppConfig(), None)
runs = service.list_runs(kind="streak_regression")
runs = runs if isinstance(runs, list) else runs["runs"]
index = {}
for item in runs:
    full = service.store.get_run(item["id"])
    cfg = full["config"]
    if cfg["rate"] != rate or full["status"] != "success" or cfg["treatment"] != "streak_position" or cfg["hand_view"] != "four_groups" or len(cfg["pitch_types"]) != 9:
        continue
    years = tuple(cfg["scope"]["game_years"])
    fe = tuple(cfg["fixed_effects"]); spec = {(): "S2", ("batter", "pitcher"): "S3", ("batter", "pitcher_game"): "S4"}.get(tuple(sorted(fe)))
    if spec == "S3" and "zone" in cfg["controls"]:
        spec = "S5"
    index[(spec, years)] = full["id"]


def table(spec, years):
    rid = index.get((spec, years))
    if rid is None:
        return {}
    rows = service.store.load_result(rid)["sections"][0]["rows"]
    return {(r["hand_group"], r["pitch_type"], r["term"]): r for r in rows if r.get("term")}


pooled, y23, y24, s4, s2, s5 = (table("S3", (2023, 2024)), table("S3", (2023,)), table("S3", (2024,)), table("S4", (2023, 2024)), table("S2", (2023, 2024)), table("S5", (2023, 2024)))
detected, tested, flips = [], 0, 0
out = []
for key in sorted(pooled):
    g, t, term = key
    if term not in ("k=2", "k=3"):
        continue
    r = pooled[key]
    if r["estimate"] is None or r["se"] is None:
        continue
    tested += 1
    est = r["estimate"]
    c1 = r["lo"] > 0 or r["hi"] < 0
    c2 = key in s4 and s4[key]["estimate"] is not None and est * s4[key]["estimate"] > 0 and abs(s4[key]["estimate"]) >= 0.5 * abs(est)
    c3 = all(key in y and y[key]["estimate"] is not None and y[key]["estimate"] * est > 0 and abs(y[key]["estimate"]) >= abs(est) / 3 for y in (y23, y24))
    c4 = abs(est) >= min_size
    c5 = not r["low_clusters"]
    ok = c1 and c2 and c3 and c4 and c5
    if key in s2 and s2[key]["estimate"] is not None and key in s4 and s4[key]["estimate"] is not None and (s2[key]["estimate"] * est < 0 or s4[key]["estimate"] * est < 0):
        flips += 1
    out.append((g, t, term, est, r["se"], r["lo"], r["hi"], s2.get(key, {}).get("estimate"), s4.get(key, {}).get("estimate"), s5.get(key, {}).get("estimate"),
                y23.get(key, {}).get("estimate"), y24.get(key, {}).get("estimate"), int(c1), int(c2), int(c3), int(c4), int(c5), "DETECTED" if ok else ""))
    if ok:
        detected.append(key)
f = lambda v: "   -  " if v is None else f"{v:+.4f}"
print(f"rate={rate}  size threshold={min_size}  tested={tested}  detected={len(detected)}  expected by chance at 99%: {0.01 * tested:.1f}  sign flips S2/S3/S4: {flips} ({flips / max(tested, 1):.0%})")
print("group type  term   S3est   se     lo      hi      S2      S4      S5     2023    2024    c1 c2 c3 c4 c5")
for row in out:
    print(f"{row[0]} {row[1]:3s} {row[2]}  " + " ".join(f(v) for v in row[3:12]) + f"  {row[12]}{row[13]}{row[14]}{row[15]}{row[16]}  {row[17]}")
