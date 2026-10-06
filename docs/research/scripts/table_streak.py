"""Compact table of streak_regression S3 (with S4 and S5) for chosen cells, plus the A10 pairs placebo. Run from the repo root."""
import sys
from treepolo_mlb_data.config import AppConfig
from treepolo_mlb_data.research.service import ResearchService

service = ResearchService(AppConfig(), None)
runs = service.list_runs(kind="streak_regression"); runs = runs if isinstance(runs, list) else runs["runs"]
idx = {}
for item in runs:
    full = service.store.get_run(item["id"]); c = full["config"]
    if full["status"] != "success" or c["hand_view"] != "four_groups" or len(c["pitch_types"]) != 9 or c["treatment"] != "streak_position" or c["scope"]["game_years"] != [2023, 2024]:
        continue
    fe = tuple(sorted(c["fixed_effects"])); spec = {(): "S2", ("batter", "pitcher"): "S3", ("batter", "pitcher_game"): "S4"}.get(fe)
    if spec == "S3" and "zone" in c["controls"]:
        spec = "S5"
    idx[(c["rate"], spec)] = full["id"]


def get(rate, spec, g, t, term):
    rid = idx.get((rate, spec))
    if rid is None:
        return None
    for r in service.store.load_result(rid)["sections"][0]["rows"]:
        if (r["hand_group"], r["pitch_type"], r["term"]) == (g, t, term):
            return r
    return None


rates = ["whiff_per_swing", "swing_rate", "called_strike_per_take", "foul_per_swing", "in_play_per_swing", "hr_per_pitch", "mean_pitch_value"]
cells = [("RvR", "FF"), ("RvR", "SL"), ("RvL", "FF"), ("RvL", "SL"), ("RvR", "SI")]
print("| 比率 | 組 | 球種 | k | S3 估計 [99% 區間] | S4 | S5 |"); print("|---|---|---|---|---|---|---|")
for rate in rates:
    for g, t in cells:
        for term in ("k=2", "k=3"):
            a, b, c = get(rate, "S3", g, t, term), get(rate, "S4", g, t, term), get(rate, "S5", g, t, term)
            if not a or a["estimate"] is None:
                continue
            fmt = lambda r: "—" if not r or r["estimate"] is None else f"{r['estimate']:+.4f}"
            print(f"| {rate} | {g} | {t} | {term[2]} | {a['estimate']:+.4f} [{a['lo']:+.4f}, {a['hi']:+.4f}] | {fmt(b)} | {fmt(c)} |")
