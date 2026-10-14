"""Forest plot: whiff_per_swing S3 effects of the cells detected in the modeling period, modeling vs 2025 vs 2026 (writes an SVG; render to PNG with a browser)."""
import json

rows = [r for r in json.load(open("docs/research/summaries/final_test_streak_grades.json", encoding="utf-8")) if r["rate"] == "whiff_per_swing"]
rows.sort(key=lambda r: -r["model"])
W, H, L, T, ROW = 980, 110 + 34 * len(rows), 230, 70, 34
xs = [v for r in rows for v in (r["lo"], r["hi"], *(r[y].get("lo", 0) for y in ("2025", "2026")), *(r[y].get("hi", 0) for y in ("2025", "2026")))]
lo, hi = min(xs + [0]) * 100 - 1, max(xs) * 100 + 1
sx = lambda v: L + (v * 100 - lo) / (hi - lo) * (W - L - 30)
col = {"model": "#4C6A92", "2025": "#C4701A", "2026": "#2E8B57"}
out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" font-family="sans-serif" font-size="12"><rect width="100%" height="100%" fill="#fff"/>',
       f'<text x="12" y="22" font-size="15" font-weight="600" fill="#222">Whiff-per-swing change at the k-th same-type pitch (S3, 99% CI), cells detected in 2023-24</text>',
       f'<text x="12" y="40" fill="#555">Percentage points vs 1st pitch of the streak. Blue = modeling 2023-24, orange = 2025 test, green = 2026 test.</text>']
for t in range(int(lo) // 2 * 2, int(hi) + 1, 2):
    x = sx(t / 100)
    out.append(f'<line x1="{x}" x2="{x}" y1="{T-12}" y2="{H-34}" stroke="{"#444" if t == 0 else "#ddd"}"/><text x="{x}" y="{H-18}" text-anchor="middle" fill="#555">{t}</text>')
for i, r in enumerate(rows):
    y = T + i * ROW + 12
    out.append(f'<text x="{L-10}" y="{y+4}" text-anchor="end" fill="#222">{" ".join(r["key"])}</text>')
    for j, (tag, e, a, b) in enumerate([("model", r["model"], r["lo"], r["hi"])] + [(yr, r[yr]["est"], r[yr]["lo"], r[yr]["hi"]) for yr in ("2025", "2026") if "est" in r[yr]]):
        yy = y + (j - 1) * 8
        out.append(f'<line x1="{sx(a)}" x2="{sx(b)}" y1="{yy}" y2="{yy}" stroke="{col[tag]}" stroke-width="2"/><circle cx="{sx(e)}" cy="{yy}" r="3.5" fill="{col[tag]}"/>')
out.append("</svg>")
open("docs/research/figures/P5-6_whiff_forest.svg", "w").write("\n".join(out))
