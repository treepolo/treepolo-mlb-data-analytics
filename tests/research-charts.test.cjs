const assert = require("node:assert/strict");
const path = require("node:path");
const charts = require(path.join(__dirname, "..", "src", "treepolo_mlb_data", "web_static", "research-charts.js"));

const find = (tree, pred, out = []) => { if (pred(tree)) out.push(tree); (tree.children || []).forEach(c => find(c, pred, out)); return out; };
const cls = (tree, name) => find(tree, t => t.attrs && t.attrs.class && String(t.attrs.class).split(" ").includes(name));

// scales and ticks
assert.deepEqual(charts.niceTicks(0, 1, 5), [0, 0.2, 0.4, 0.6, 0.8, 1]);
assert.deepEqual(charts.niceTicks(-0.03, 0.05, 4).map(t => Math.round(t * 1000) / 1000), [-0.02, 0, 0.02, 0.04]);
assert.equal(charts.linearScale([0, 10], [100, 200])(5), 150);
assert.deepEqual(charts.niceTicks(NaN, 1), []);

// line chart: band, gap in the line for a missing value, one panel per facet, one colour per series
const rows = [
  { hand_group: "RvR", pitch_type: "SL", k: 2, estimate: 0.03, lo: 0.01, hi: 0.05 },
  { hand_group: "RvR", pitch_type: "SL", k: 3, estimate: null, lo: null, hi: null },
  { hand_group: "RvR", pitch_type: "SL", k: 4, estimate: 0.05, lo: 0.0, hi: 0.1 },
  { hand_group: "RvR", pitch_type: "FF", k: 2, estimate: 0.02, lo: 0.0, hi: 0.04 },
  { hand_group: "RvR", pitch_type: "FF", k: 3, estimate: 0.01, lo: -0.01, hi: 0.03 },
  { hand_group: "LvL", pitch_type: "SL", k: 2, estimate: 0.04, lo: 0.02, hi: 0.06 },
  { hand_group: "LvL", pitch_type: "SL", k: 3, estimate: 0.06, lo: 0.03, hi: 0.09 },
];
const line = charts.build("line", rows, { x: "k", y: "estimate", lo: "lo", hi: "hi", series: "pitch_type", facet: "hand_group", zeroLine: true }, { title: "連投 test" });
assert.equal(line.tag, "svg");
assert.equal(cls(line, "rc-panel").length, 2);
assert.equal(cls(line, "rc-point").length, 6);                       // the null estimate is not drawn
assert.equal(cls(line, "rc-line").length, 2);                        // RvR/SL has a gap -> no polyline; RvR/FF and LvL/SL are drawn (>=2 points)
assert.equal(cls(line, "rc-legend").length, 2);
assert.ok(cls(line, "rc-band").length >= 2);
assert.ok(find(line, t => t.tag === "line" && t.attrs["stroke-dasharray"]).length >= 2);   // zero line in both panels
assert.ok(find(line, t => t.text && t.text.includes("Historical outcomes")).length === 1);

// categorical x (regression terms) keeps first-appearance order
const terms = [{ term: "k=2", estimate: 0.03, lo: 0.01, hi: 0.05 }, { term: "k=3", estimate: 0.05, lo: 0.02, hi: 0.08 }];
const cat = charts.build("line", terms, { x: "term", y: "estimate", lo: "lo", hi: "hi" }, { categoricalX: true });
assert.deepEqual(find(cat, t => t.tag === "text" && /^k=/.test(t.text || "")).map(t => t.text), ["k=2", "k=3"]);

// heatmap: blank for null, faded for low_n, diverging colours when values straddle zero
const heat = charts.build("heatmap", [
  { prev1_pitch_type: "FF", pitch_type: "SL", mean_value: -0.02, n: 500, low_n: 0, hand_group: "RvR" },
  { prev1_pitch_type: "SL", pitch_type: "SL", mean_value: 0.03, n: 20, low_n: 1, hand_group: "RvR" },
  { prev1_pitch_type: "SL", pitch_type: "FF", mean_value: null, n: 0, low_n: 1, hand_group: "RvR" },
], { row: "prev1_pitch_type", col: "pitch_type", value: "mean_value", n: "n", low: "low_n", facet: "hand_group" }, { title: "heat" });
assert.equal(cls(heat, "rc-cell").length, 2);                    // 1 normal + 1 low cell (the null value is blank)
assert.equal(cls(heat, "rc-low").length, 1);
const fills = cls(heat, "rc-cell").map(c => c.attrs.fill);
assert.ok(fills.some(f => f.startsWith("rgb(")) && new Set(fills).size === 2);
assert.equal(charts.heatColor(null, 0, 1, false), "#ffffff");
assert.equal(charts.heatColor(0, -1, 1, true), "rgb(247,247,247)");

// hideLow removes faded cells and the rows/columns that only they used
const hidden = charts.build("heatmap", [{ r: "a", c: "x", v: 1, low: 0 }, { r: "b", c: "y", v: 2, low: 1 }], { row: "r", col: "c", value: "v", low: "low" }, { hideLow: true });
assert.equal(cls(hidden, "rc-cell").length, 1); assert.ok(find(hidden, t => t.tag === "text" && t.text === "b").length === 0);
assert.ok(find(heat, t => t.tag === "title" && t.text.includes("n=500")).length === 1);

// duplicate cells in one panel are reported instead of silently overwritten
const dup = charts.build("heatmap", [{ r: "a", c: "x", v: 1 }, { r: "a", c: "x", v: 2 }], { row: "r", col: "c", value: "v" }, {});
assert.ok(find(dup, t => t.tag === "text" && t.text && t.text.includes("1 格有重複資料")).length === 1);

// location heatmap puts the highest bin on top (reverseRows)
const loc = charts.build("heatmap", [
  { loc_x_bin: 0, loc_z_bin: 0, hr_rate: 0.01 }, { loc_x_bin: 0, loc_z_bin: 3, hr_rate: 0.02 }], { row: "loc_z_bin", col: "loc_x_bin", value: "hr_rate" }, { reverseRows: true });
const labelsY = find(loc, t => t.tag === "text" && t.attrs["text-anchor"] === "end" && ["0", "3"].includes(t.text)).sort((a, b) => a.attrs.y - b.attrs.y).map(t => t.text);
assert.deepEqual(labelsY, ["3", "0"]);

// scatter: error bars, hollow low-n points
const sc = charts.build("scatter", [
  { mean_value: 0.01, value_lo: 0.0, value_hi: 0.02, hr_rate: 0.01, hr_lo: 0.005, hr_hi: 0.015, pitch_type: "FF", low_n: 0 },
  { mean_value: -0.01, value_lo: -0.03, value_hi: 0.01, hr_rate: 0.02, hr_lo: 0.0, hr_hi: 0.04, pitch_type: "SL", low_n: 1 },
  { mean_value: null, hr_rate: 0.5, pitch_type: "SL", low_n: 0 },
], { x: "mean_value", y: "hr_rate", xlo: "value_lo", xhi: "value_hi", ylo: "hr_lo", yhi: "hr_hi", color: "pitch_type", low: "low_n" });
assert.equal(cls(sc, "rc-point").length, 2); assert.equal(cls(sc, "rc-hollow").length, 1);
assert.equal(find(sc, t => t.tag === "line" && t.attrs["stroke-opacity"] === 0.5).length, 4);

// hideLow drops hollow points before the axes are computed
const sc2 = charts.build("scatter", [{ x: 1, y: 1, low: 0 }, { x: 1000, y: 1000, low: 1 }], { x: "x", y: "y", low: "low" }, { hideLow: true });
assert.equal(cls(sc2, "rc-point").length, 1); assert.ok(!find(sc2, t => t.tag === "text" && t.text === "1000").length);

// empty data and serialization
const empty = charts.build("line", [{ k: null, estimate: null }], { x: "k", y: "estimate" }, { title: "none" });
assert.ok(cls(empty, "rc-empty").length === 1);
const svg = charts.toSvgString(charts.build("scatter", [{ a: 1, b: 2, c: "x<y&\"z" }], { x: "a", y: "b", color: "c" }, { title: "a<b & \"c\"" }));
assert.ok(svg.startsWith("<svg ") && svg.includes("a&lt;b &amp; &quot;c&quot;") && !svg.includes("<b"));
assert.throws(() => charts.build("pie", [], {}), /unknown chart type/);

// presets guess the mapping from section columns
const g = charts.PRESETS.streak.guess(["hand_group", "pitch_type", "k", "estimate", "lo", "hi"]);
assert.deepEqual([g.x, g.y, g.lo, g.hi, g.series, g.facet], ["k", "estimate", "lo", "hi", "pitch_type", "hand_group"]);
const e = charts.PRESETS.evHr.guess(["mean_value", "hr_rate", "value_lo", "value_hi", "hr_lo", "hr_hi", "pitch_type", "low_n"]);
assert.equal(e.xlo, "value_lo"); assert.equal(e.low, "low_n");
console.log("research-charts: ok");
