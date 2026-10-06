"""Summarize the A7 previous-pitch regressions (reference = previous pitch of the SAME type). Run from the repo root."""
import sys
from treepolo_mlb_data.config import AppConfig
from treepolo_mlb_data.research.service import ResearchService

service = ResearchService(AppConfig(), None)
runs = service.list_runs(kind="streak_regression"); runs = runs if isinstance(runs, list) else runs["runs"]
want = sys.argv[1:] or ["whiff_per_swing", "swing_rate", "mean_pitch_value"]
for item in runs:
    full = service.store.get_run(item["id"]); c = full["config"]
    if full["status"] != "success" or c["treatment"] != "prev1_pitch_type" or c["rate"] not in want or c["hand_view"] != "four_groups":
        continue
    rows = [r for r in service.store.load_result(full["id"])["sections"][0]["rows"] if r.get("term") and r["estimate"] is not None]
    size = 0.002 if c["rate"] == "mean_pitch_value" else 0.01
    sig = [r for r in rows if (r["lo"] > 0 or r["hi"] < 0) and abs(r["estimate"]) >= size and not r["low_clusters"]]
    print(f"== {c['rate']} (run {full['id']}): {len(rows)} terms, {len(sig)} with 99% interval excluding 0 and |effect| >= {size}; expected by chance {0.01 * len(rows):.1f}")
    for r in sorted(sig, key=lambda r: -abs(r["estimate"]) / (r["se"] or 1))[:14]:
        print(f"   {r['hand_group']} {r['pitch_type']} after {r['term'][5:]}: {r['estimate']:+.4f} [{r['lo']:+.4f}, {r['hi']:+.4f}] n={r['n_obs']} clusters={r['clusters']}")
