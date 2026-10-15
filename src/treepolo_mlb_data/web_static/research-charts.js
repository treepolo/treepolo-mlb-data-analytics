(() => {
  "use strict";

  // Pure chart builders for research results. Every builder returns a plain element tree
  // ({tag, attrs, children, text}) so it can be tested without a DOM, rendered with toDom()
  // or saved with toSvgString(). Missing values (null/undefined/NaN) are never drawn or filled in.

  const PALETTE = ["#0072B2", "#E69F00", "#009E73", "#D55E00", "#CC79A7", "#56B4E9", "#F0E442", "#444444", "#999999"]; // Okabe-Ito
  const DISCLAIMER = "歷史結果，不是改投別的球會怎樣 · Historical outcomes, not what a different pitch would do";
  const PANEL = { width: 340, height: 260, left: 52, right: 12, top: 26, bottom: 40 };
  const INK = "#26323f", GRID = "#d7dde5", MUTED = "#5b6775";

  const node = (tag, attrs = {}, children = [], text) => ({ tag, attrs, children: children.filter(Boolean), text });
  const isNum = v => typeof v === "number" && Number.isFinite(v);
  const num = v => (v === null || v === undefined || v === "" ? null : (Number.isFinite(Number(v)) ? Number(v) : null));
  const fmt = v => (Math.abs(v) >= 100 || Number.isInteger(v) ? String(Math.round(v * 100) / 100) : String(Math.round(v * 10000) / 10000));

  function niceTicks(lo, hi, count = 5) {
    if (!isNum(lo) || !isNum(hi)) return [];
    if (lo === hi) { const pad = lo === 0 ? 1 : Math.abs(lo) * 0.1; lo -= pad; hi += pad; }
    const raw = (hi - lo) / count, mag = Math.pow(10, Math.floor(Math.log10(raw)));
    const step = [1, 2, 2.5, 5, 10].map(m => m * mag).find(s => s >= raw) || raw;
    const ticks = [];
    for (let i = Math.ceil(lo / step - 1e-9); i <= Math.floor(hi / step + 1e-9); i++) ticks.push(Number((i * step).toPrecision(12)) + 0);
    return ticks;
  }

  function linearScale(domain, range) {
    const [d0, d1] = domain, [r0, r1] = range, span = d1 - d0 || 1;
    return v => r0 + ((v - d0) / span) * (r1 - r0);
  }

  function extent(values) {
    const ok = values.filter(isNum);
    return ok.length ? [Math.min(...ok), Math.max(...ok)] : null;
  }

  function colorFor(index) { return PALETTE[index % PALETTE.length]; }

  function mix(a, b, t) { return a.map((x, i) => Math.round(x + (b[i] - x) * t)); }
  const rgb = c => `rgb(${c[0]},${c[1]},${c[2]})`;
  function heatColor(value, lo, hi, diverging) {
    if (!isNum(value)) return "#ffffff";
    if (diverging) {
      const m = Math.max(Math.abs(lo), Math.abs(hi)) || 1, t = Math.max(-1, Math.min(1, value / m));
      return t >= 0 ? rgb(mix([247, 247, 247], [178, 24, 43], t)) : rgb(mix([247, 247, 247], [33, 102, 172], -t));
    }
    const t = hi === lo ? 0.5 : (value - lo) / (hi - lo);
    return rgb(mix([247, 251, 255], [8, 81, 156], Math.max(0, Math.min(1, t))));
  }

  function groupBy(rows, key) {
    const map = new Map();
    rows.forEach(r => { const k = key && r[key] != null ? String(r[key]) : ""; if (!map.has(k)) map.set(k, []); map.get(k).push(r); });
    return map;
  }

  function panelFrame(title, innerW, innerH, extra) {
    return node("g", { class: "rc-panel" }, [
      title ? node("text", { x: PANEL.left, y: 14, "font-size": 12, "font-weight": 700, fill: INK }, [], title) : null,
      ...extra,
    ]);
  }

  function axes(xScale, yScale, xTicks, yTicks, xLabel, yLabel, xTickLabel) {
    const x0 = PANEL.left, x1 = PANEL.width - PANEL.right, y0 = PANEL.height - PANEL.bottom, y1 = PANEL.top;
    const out = [];
    yTicks.forEach(t => {
      const y = yScale(t);
      out.push(node("line", { x1: x0, x2: x1, y1: y, y2: y, stroke: GRID, "stroke-width": 1 }));
      out.push(node("text", { x: x0 - 4, y: y + 3, "text-anchor": "end", "font-size": 10, fill: MUTED }, [], fmt(t)));
    });
    xTicks.forEach(t => {
      const x = xScale(t);
      out.push(node("line", { x1: x, x2: x, y1: y0, y2: y0 + 3, stroke: MUTED }));
      out.push(node("text", { x, y: y0 + 14, "text-anchor": "middle", "font-size": 10, fill: MUTED }, [], xTickLabel ? xTickLabel(t) : fmt(t)));
    });
    out.push(node("line", { x1: x0, x2: x1, y1: y0, y2: y0, stroke: MUTED }));
    out.push(node("line", { x1: x0, x2: x0, y1: y0, y2: y1, stroke: MUTED }));
    out.push(node("text", { x: (x0 + x1) / 2, y: PANEL.height - 6, "text-anchor": "middle", "font-size": 11, fill: INK }, [], xLabel || ""));
    out.push(node("text", { x: 12, y: (y0 + y1) / 2, "text-anchor": "middle", "font-size": 11, fill: INK, transform: `rotate(-90 12 ${(y0 + y1) / 2})` }, [], yLabel || ""));
    return out;
  }

  // ---- line chart with confidence band ------------------------------------------------------------------------
  // mapping: {x, y, lo, hi, series, facet, zeroLine}; x may be numeric or categorical (ordered by first appearance).
  function lineChart(rows, mapping, options = {}) {
    const cats = [];
    const xKey = r => { const n = num(r[mapping.x]); if (n !== null && !options.categoricalX) return n; const label = String(r[mapping.x]); let i = cats.indexOf(label); if (i < 0) { cats.push(label); i = cats.length - 1; } return i + 1; };
    const usable = rows.map(r => ({ r, x: r[mapping.x] == null ? null : xKey(r), y: num(r[mapping.y]), lo: num(r[mapping.lo]), hi: num(r[mapping.hi]) })).filter(p => p.x !== null);
    const seriesNames = [...new Set(usable.map(p => (mapping.series ? String(p.r[mapping.series]) : "")))];
    const panels = [...groupBy(usable.map(p => p.r), mapping.facet).keys()];
    const yAll = usable.flatMap(p => [p.y, p.lo, p.hi]).filter(isNum);
    const yExt = extent(mapping.zeroLine === false ? yAll : yAll.concat([0]));
    const xExt = extent(usable.map(p => p.x));
    if (!yExt || !xExt) return emptyChart(options.title || "", "沒有可畫的資料 No data to draw");
    const yTicks = niceTicks(yExt[0], yExt[1]), xTicks = cats.length ? cats.map((_, i) => i + 1) : niceTicks(xExt[0], xExt[1], 4).filter(t => Number.isInteger(t) || !options.integerX);
    const yDomain = [Math.min(yExt[0], yTicks[0]), Math.max(yExt[1], yTicks[yTicks.length - 1])];
    const xDomain = xExt[0] === xExt[1] ? [xExt[0] - 1, xExt[1] + 1] : [xExt[0] - 0.2, xExt[1] + 0.2];
    const xs = linearScale(xDomain, [PANEL.left, PANEL.width - PANEL.right]);
    const ys = linearScale(yDomain, [PANEL.height - PANEL.bottom, PANEL.top]);
    const built = panels.map(panelName => {
      const mine = usable.filter(p => (mapping.facet ? String(p.r[mapping.facet]) : "") === panelName);
      const parts = axes(xs, ys, xTicks, yTicks, options.xLabel || mapping.x, options.yLabel || mapping.y, cats.length ? (t => cats[t - 1] || "") : null);
      if (mapping.zeroLine !== false && yDomain[0] <= 0 && yDomain[1] >= 0) parts.push(node("line", { x1: PANEL.left, x2: PANEL.width - PANEL.right, y1: ys(0), y2: ys(0), stroke: INK, "stroke-dasharray": "4 3" }));
      seriesNames.forEach((name, si) => {
        const pts = mine.filter(p => (mapping.series ? String(p.r[mapping.series]) : "") === name).sort((a, b) => a.x - b.x);
        const color = colorFor(si);
        const bandPts = pts.filter(p => isNum(p.lo) && isNum(p.hi));
        if (bandPts.length > 1) {
          const up = bandPts.map(p => `${xs(p.x)},${ys(p.hi)}`), down = bandPts.slice().reverse().map(p => `${xs(p.x)},${ys(p.lo)}`);
          parts.push(node("polygon", { points: up.concat(down).join(" "), fill: color, "fill-opacity": 0.18, stroke: "none", class: "rc-band" }));
        }
        bandPts.forEach(p => parts.push(node("line", { x1: xs(p.x), x2: xs(p.x), y1: ys(p.lo), y2: ys(p.hi), stroke: color, "stroke-width": 1.5 })));
        let segment = [];
        const flush = () => { if (segment.length > 1) parts.push(node("polyline", { points: segment.join(" "), fill: "none", stroke: color, "stroke-width": 2, class: "rc-line" })); segment = []; };
        pts.forEach(p => { if (isNum(p.y)) segment.push(`${xs(p.x)},${ys(p.y)}`); else flush(); });
        flush();
        pts.filter(p => isNum(p.y)).forEach(p => parts.push(node("circle", { cx: xs(p.x), cy: ys(p.y), r: 3, fill: color, class: "rc-point" })));
      });
      return panelFrame(panelName, PANEL.width, PANEL.height, parts);
    });
    return assemble(built, panels, seriesNames, options);
  }

  // ---- heatmap ---------------------------------------------------------------------------------------------------
  // mapping: {row, col, value, n, low, facet}; low=1 cells are drawn faded; null values are left blank.
  function heatmap(allRows, mapping, options = {}) {
    const rows = options.hideLow && mapping.low ? allRows.filter(r => Number(r[mapping.low]) !== 1) : allRows;
    const rowLabels = uniqueOrdered(rows.filter(r => num(r[mapping.value]) !== null).map(r => r[mapping.row]), options.rowOrder);
    const colLabels = uniqueOrdered(rows.filter(r => num(r[mapping.value]) !== null).map(r => r[mapping.col]), options.colOrder);
    const values = rows.map(r => num(r[mapping.value])).filter(isNum);
    if (!values.length) return emptyChart(options.title || "", "沒有可畫的資料 No data to draw");
    const [lo, hi] = extent(values);
    const diverging = options.diverging != null ? options.diverging : (lo < 0 && hi > 0);
    const panels = [...groupBy(rows, mapping.facet).keys()];
    const cw = 40, ch = 22;
    const dim = { width: PANEL.left + colLabels.length * cw + PANEL.right, height: PANEL.top + rowLabels.length * ch + 14 };
    let collisions = 0;
    const built = panels.map(panelName => {
      const mine = rows.filter(r => (mapping.facet ? String(r[mapping.facet]) : "") === panelName), parts = [];
      const seenCells = new Set();
      colLabels.forEach((c, ci) => parts.push(node("text", { x: PANEL.left + ci * cw + cw / 2, y: PANEL.top - 4, "text-anchor": "middle", "font-size": 10, fill: MUTED }, [], String(c))));
      rowLabels.forEach((r, ri) => parts.push(node("text", { x: PANEL.left - 4, y: PANEL.top + ri * ch + ch / 2 + 3, "text-anchor": "end", "font-size": 10, fill: MUTED }, [], String(r))));
      mine.forEach(r => {
        const ci = colLabels.indexOf(r[mapping.col]), ri = rowLabels.indexOf(r[mapping.row]), v = num(r[mapping.value]);
        if (ci < 0 || ri < 0 || v === null) return;
        const cellKey = `${ci}|${ri}`;
        if (seenCells.has(cellKey)) collisions += 1; seenCells.add(cellKey);
        const faded = mapping.low && Number(r[mapping.low]) === 1;
        const tip = `${r[mapping.row]} → ${r[mapping.col]}: ${fmt(v)}${mapping.n && r[mapping.n] != null ? ` (n=${r[mapping.n]})` : ""}`;
        parts.push(node("rect", { x: PANEL.left + ci * cw, y: PANEL.top + ri * ch, width: cw - 1, height: ch - 1, fill: heatColor(v, lo, hi, diverging), "fill-opacity": faded ? 0.35 : 1, class: faded ? "rc-cell rc-low" : "rc-cell" }, [node("title", {}, [], tip)]));
        parts.push(node("text", { x: PANEL.left + ci * cw + cw / 2, y: PANEL.top + ri * ch + ch / 2 + 3, "text-anchor": "middle", "font-size": 9, fill: INK }, [], (Math.round(v * 1000) / 1000).toFixed(3)));
      });
      return panelFrame(panelName, dim.width, dim.height, parts);
    });
    return assemble(built, panels, [], options, `${mapping.value}: ${fmt(lo)} … ${fmt(hi)}（滑過格子看 n；淡色＝樣本不足 faded = low n）${collisions ? `　⚠ ${collisions} 格有重複資料，只顯示最後一筆，請用篩選器選定一組 ${collisions} cells have several rows; filter to one group` : ""}`, dim);
  }

  // ---- scatter with error bars ---------------------------------------------------------------------------------------
  // mapping: {x, y, xlo, xhi, ylo, yhi, color, low, facet}; low=1 points are hollow.
  function scatter(rows, mapping, options = {}) {
    const kept = options.hideLow && mapping.low ? rows.filter(r => Number(r[mapping.low]) !== 1) : rows;
    const pts = kept.map(r => ({ r, x: num(r[mapping.x]), y: num(r[mapping.y]), xlo: num(r[mapping.xlo]), xhi: num(r[mapping.xhi]), ylo: num(r[mapping.ylo]), yhi: num(r[mapping.yhi]) })).filter(p => isNum(p.x) && isNum(p.y));
    if (!pts.length) return emptyChart(options.title || "", "沒有可畫的資料 No data to draw");
    const xe = extent(pts.flatMap(p => [p.x, p.xlo, p.xhi])), ye = extent(pts.flatMap(p => [p.y, p.ylo, p.yhi]));
    const xt = niceTicks(xe[0], xe[1]), yt = niceTicks(ye[0], ye[1]);
    const xs = linearScale([Math.min(xe[0], xt[0]), Math.max(xe[1], xt[xt.length - 1])], [PANEL.left, PANEL.width - PANEL.right]);
    const ys = linearScale([Math.min(ye[0], yt[0]), Math.max(ye[1], yt[yt.length - 1])], [PANEL.height - PANEL.bottom, PANEL.top]);
    const names = [...new Set(pts.map(p => (mapping.color ? String(p.r[mapping.color]) : "")))];
    const panels = [...groupBy(pts.map(p => p.r), mapping.facet).keys()];
    const built = panels.map(panelName => {
      const parts = axes(xs, ys, xt, yt, options.xLabel || mapping.x, options.yLabel || mapping.y);
      pts.filter(p => (mapping.facet ? String(p.r[mapping.facet]) : "") === panelName).forEach(p => {
        const color = colorFor(names.indexOf(mapping.color ? String(p.r[mapping.color]) : "")), hollow = mapping.low && Number(p.r[mapping.low]) === 1;
        if (isNum(p.xlo) && isNum(p.xhi)) parts.push(node("line", { x1: xs(p.xlo), x2: xs(p.xhi), y1: ys(p.y), y2: ys(p.y), stroke: color, "stroke-opacity": 0.5 }));
        if (isNum(p.ylo) && isNum(p.yhi)) parts.push(node("line", { x1: xs(p.x), x2: xs(p.x), y1: ys(p.ylo), y2: ys(p.yhi), stroke: color, "stroke-opacity": 0.5 }));
        parts.push(node("circle", { cx: xs(p.x), cy: ys(p.y), r: 3.5, fill: hollow ? "none" : color, stroke: color, "stroke-width": 1.5, class: hollow ? "rc-point rc-hollow" : "rc-point" }));
      });
      return panelFrame(panelName, PANEL.width, PANEL.height, parts);
    });
    return assemble(built, panels, names, options, "空心 hollow = 樣本不足 low n");
  }


  // ---- horizontal bars with intervals ---------------------------------------------------------------------------------
  // mapping: {label, value, lo, hi, color, low, facet}; one row per label, a bar from 0 to the value, a whisker for the interval; low=1 bars are faded.
  function bars(rows, mapping, options = {}) {
    const kept = options.hideLow && mapping.low ? rows.filter(r => Number(r[mapping.low]) !== 1) : rows;
    const pts = kept.map(r => ({ r, label: r[mapping.label] == null ? null : String(r[mapping.label]), v: num(r[mapping.value]), lo: num(r[mapping.lo]), hi: num(r[mapping.hi]) })).filter(p => p.label !== null && isNum(p.v));
    if (!pts.length) return emptyChart(options.title || "", "沒有可畫的資料 No data to draw");
    const ext = extent(pts.flatMap(p => [p.v, p.lo, p.hi]).concat(options.zeroBase === false ? [] : [0]));
    const ticks = niceTicks(ext[0], ext[1], 4);
    const domain = [Math.min(ext[0], ticks[0]), Math.max(ext[1], ticks[ticks.length - 1])];
    const labels = options.labelOrder ? uniqueOrdered(pts.map(p => p.label), options.labelOrder) : [...new Set(pts.map(p => p.label))];
    const names = [...new Set(pts.map(p => (mapping.color ? String(p.r[mapping.color]) : "")))];
    const panels = [...groupBy(pts.map(p => p.r), mapping.facet).keys()];
    const rowH = 18, left = 120, right = 16, top = 26, bottom = 34;
    const dim = { width: 380, height: top + labels.length * rowH + bottom };
    const xs = linearScale(domain, [left, dim.width - right]);
    const built = panels.map(panelName => {
      const parts = [];
      ticks.forEach(t => {
        parts.push(node("line", { x1: xs(t), x2: xs(t), y1: top, y2: top + labels.length * rowH, stroke: GRID, "stroke-width": 1 }));
        parts.push(node("text", { x: xs(t), y: top + labels.length * rowH + 13, "text-anchor": "middle", "font-size": 10, fill: MUTED }, [], fmt(t)));
      });
      if (domain[0] <= 0 && domain[1] >= 0) parts.push(node("line", { x1: xs(0), x2: xs(0), y1: top, y2: top + labels.length * rowH, stroke: INK, "stroke-dasharray": "4 3" }));
      labels.forEach((label, li) => parts.push(node("text", { x: left - 6, y: top + li * rowH + rowH / 2 + 3, "text-anchor": "end", "font-size": 10, fill: INK }, [], label)));
      pts.filter(p => (mapping.facet ? String(p.r[mapping.facet]) : "") === panelName).forEach(p => {
        const li = labels.indexOf(p.label), y = top + li * rowH, cy = y + rowH / 2, color = colorFor(names.indexOf(mapping.color ? String(p.r[mapping.color]) : ""));
        const faded = mapping.low && Number(p.r[mapping.low]) === 1;
        const x0 = xs(Math.min(0, p.v)), x1 = xs(Math.max(0, p.v));
        parts.push(node("rect", { x: x0, y: y + 3, width: Math.max(x1 - x0, 0.5), height: rowH - 7, fill: color, "fill-opacity": faded ? 0.3 : 0.85, class: faded ? "rc-bar rc-low" : "rc-bar" }, [node("title", {}, [], `${p.label}: ${fmt(p.v)}${isNum(p.lo) && isNum(p.hi) ? ` [${fmt(p.lo)}, ${fmt(p.hi)}]` : ""}`)]));
        if (isNum(p.lo) && isNum(p.hi)) {
          parts.push(node("line", { x1: xs(p.lo), x2: xs(p.hi), y1: cy, y2: cy, stroke: INK, "stroke-width": 1.5, class: "rc-whisker" }));
          [p.lo, p.hi].forEach(e => parts.push(node("line", { x1: xs(e), x2: xs(e), y1: cy - 3, y2: cy + 3, stroke: INK, "stroke-width": 1.5 })));
        }
      });
      parts.push(node("text", { x: (left + dim.width - right) / 2, y: dim.height - 4, "text-anchor": "middle", "font-size": 11, fill: INK }, [], options.xLabel || mapping.value));
      return node("g", { class: "rc-panel" }, [panelName ? node("text", { x: left, y: 14, "font-size": 12, "font-weight": 700, fill: INK }, [], panelName) : null, ...parts]);
    });
    return assemble(built, panels, names, options, mapping.low ? "淡色＝無可偵測差異或樣本不足 faded = not detected / low n" : "", dim);
  }

  function uniqueOrdered(values, order) {
    const seen = [...new Set(values.filter(v => v !== null && v !== undefined))];
    if (order) return order.filter(v => seen.includes(v)).concat(seen.filter(v => !order.includes(v)));
    return seen.sort((a, b) => (isNum(Number(a)) && isNum(Number(b)) ? Number(a) - Number(b) : String(a).localeCompare(String(b))));
  }

  function emptyChart(title, message) {
    return node("svg", { xmlns: "http://www.w3.org/2000/svg", viewBox: "0 0 360 80", width: 360, height: 80, class: "research-chart rc-empty" }, [
      node("text", { x: 10, y: 24, "font-size": 12, "font-weight": 700, fill: INK }, [], title),
      node("text", { x: 10, y: 52, "font-size": 12, fill: MUTED }, [], message)]);
  }

  function assemble(panelNodes, panelNames, seriesNames, options, note, dim = PANEL) {
    const columns = Math.min(options.columns || 2, Math.max(panelNodes.length, 1));
    const rowsN = Math.ceil(panelNodes.length / columns);
    const width = columns * dim.width;
    const perRow = Math.max(1, Math.floor((width - 16) / 70)), legendRows = seriesNames.length > 1 ? Math.ceil(seriesNames.length / perRow) : 0;
    const head = 44, legend = legendRows ? legendRows * 14 + 6 : 0, foot = 22;
    const height = head + legend + rowsN * dim.height + foot;
    const children = [
      node("text", { x: 8, y: 18, "font-size": 14, "font-weight": 700, fill: INK }, [], options.title || ""),
      node("text", { x: 8, y: 34, "font-size": 10, fill: MUTED }, [], options.disclaimer || DISCLAIMER),
    ];
    if (legend) seriesNames.forEach((name, i) => children.push(node("g", { class: "rc-legend" }, [
      node("rect", { x: 8 + (i % perRow) * 70, y: head - 4 + Math.floor(i / perRow) * 14, width: 10, height: 10, fill: colorFor(i) }),
      node("text", { x: 22 + (i % perRow) * 70, y: head + 5 + Math.floor(i / perRow) * 14, "font-size": 10, fill: INK }, [], name || "—")])));
    panelNodes.forEach((p, i) => children.push(node("g", { transform: `translate(${(i % columns) * dim.width},${head + legend + Math.floor(i / columns) * dim.height})` }, [p])));
    if (note) children.push(node("text", { x: 8, y: height - 6, "font-size": 10, fill: MUTED }, [], note));
    return node("svg", { xmlns: "http://www.w3.org/2000/svg", viewBox: `0 0 ${width} ${height}`, width, height, class: "research-chart", "font-family": "system-ui, sans-serif" }, children);
  }

  // ---- presets: default column mapping guessed from the section's columns ---------------------------------------------
  function pick(columns, names) { return names.find(n => columns.includes(n)) || null; }
  const PRESETS = {
    streak: { label: "連投曲線 Streak curve", type: "line", guess: c => ({ x: pick(c, ["k", "term"]), y: pick(c, ["estimate", "mean", "diff"]), lo: pick(c, ["lo"]), hi: pick(c, ["hi"]), series: pick(c, ["pitch_type"]), facet: pick(c, ["hand_group", "frame_group"]), zeroLine: true }), options: { integerX: true } },
    direction: { label: "方向圖 Direction map（邊際價值）", type: "heatmap", guess: c => ({ row: pick(c, ["context"]), col: pick(c, ["action"]), value: pick(c, ["marginal"]), n: pick(c, ["n"]), low: pick(c, ["not_detected"]), facet: pick(c, ["group", "hand_group"]) }),
      options: { diverging: true, disclaimer: "模型估計（聯盟平均）：多投某球種約 10 個百分點的每球失分變化；負＝對投手有利，灰淡＝無可偵測差異 · Model estimate for the league average" } },
    frontier: { label: "取捨前緣 Risk frontier", type: "scatter", guess: c => ({ x: pick(c, ["hr_probability"]), y: pick(c, ["expected_runs"]), xlo: pick(c, ["hr_lo"]), xhi: pick(c, ["hr_hi"]), ylo: pick(c, ["lo"]), yhi: pick(c, ["hi"]), color: pick(c, ["lambda"]), facet: pick(c, ["group"]) }),
      options: { xLabel: "此打席全壘打機率", yLabel: "期望失分", disclaimer: "模型估計：每個 λ 的信賴區域政策 · Model estimate, one trust-region policy per lambda" } },
    ablation: { label: "特徵消融 Feature ablation（對數損失改善）", type: "bars", guess: c => ({ label: pick(c, ["comparison"]), value: pick(c, ["delta_logloss", "gain"]), lo: pick(c, ["lo"]), hi: pick(c, ["hi"]), color: pick(c, ["mode"]), facet: pick(c, ["group"]) }),
      options: { xLabel: "對數損失改善（正＝球員描述有用）", disclaimer: "沒見過的球員上的預測改善 · Prediction gain on players the model has not seen" } },
    situationBars: { label: "情境條圖 Situation bars（每種球種的 Q）", type: "bars", guess: c => ({ label: pick(c, ["action"]), value: pick(c, ["q"]), lo: pick(c, ["q_lo"]), hi: pick(c, ["q_hi"]), color: pick(c, ["indistinct_group"]), low: pick(c, ["unsupported"]) }),
      options: { zeroBase: false, xLabel: "到半局結束的期望失分", disclaimer: "模型估計：此情境投該球種、之後照一般投法 · Model estimate: throw it now, then play as usual" } },
    prevNext: { label: "前一球→下一球熱圖 Previous→next heatmap", type: "heatmap", guess: c => ({ row: pick(c, ["prev1_pitch_type"]), col: pick(c, ["pitch_type"]), value: pick(c, ["whiff_per_swing", "mean_value", "hr_rate", "swing_rate"]), n: pick(c, ["n"]), low: pick(c, ["low_n"]), facet: pick(c, ["hand_group"]) }), options: { hideLow: true } },
    location: { label: "位置熱圖 Location heatmap", type: "heatmap", guess: c => ({ row: pick(c, ["loc_z_bin"]), col: pick(c, ["loc_x_bin"]), value: pick(c, ["whiff_per_swing", "mean_value", "hr_rate", "swing_rate"]), n: pick(c, ["n"]), low: pick(c, ["low_n"]), facet: pick(c, ["pitch_type", "hand_group"]) }), options: { reverseRows: true, hideLow: true } },
    evHr: { label: "期望值對全壘打 EV vs HR", type: "scatter", guess: c => ({ x: pick(c, ["mean_value"]), y: pick(c, ["hr_rate"]), xlo: pick(c, ["value_lo"]), xhi: pick(c, ["value_hi"]), ylo: pick(c, ["hr_lo"]), yhi: pick(c, ["hr_hi"]), color: pick(c, ["pitch_type"]), low: pick(c, ["low_n"]), facet: pick(c, ["hand_group"]) }), options: { hideLow: true } },
  };

  function build(type, rows, mapping, options = {}) {
    if (type === "line") return lineChart(rows, mapping, options);
    if (type === "heatmap") {
      const opts = { ...options };
      if (options.reverseRows) opts.rowOrder = uniqueOrdered(rows.map(r => r[mapping.row])).reverse();
      return heatmap(rows, mapping, opts);
    }
    if (type === "scatter") return scatter(rows, mapping, options);
    if (type === "bars") return bars(rows, mapping, options);
    throw new Error(`unknown chart type: ${type}`);
  }

  // ---- serialization / DOM -------------------------------------------------------------------------------------------
  const esc = s => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  function toSvgString(tree) {
    const attrs = Object.entries(tree.attrs || {}).filter(([, v]) => v !== null && v !== undefined).map(([k, v]) => ` ${k}="${esc(typeof v === "number" ? Math.round(v * 100) / 100 : v)}"`).join("");
    const inner = (tree.children || []).map(toSvgString).join("") + (tree.text ? esc(tree.text) : "");
    return `<${tree.tag}${attrs}>${inner}</${tree.tag}>`;
  }
  function toDom(tree, doc = document) {
    const el = doc.createElementNS("http://www.w3.org/2000/svg", tree.tag);
    Object.entries(tree.attrs || {}).forEach(([k, v]) => { if (v !== null && v !== undefined) el.setAttribute(k, typeof v === "number" ? String(Math.round(v * 100) / 100) : v); });
    (tree.children || []).forEach(child => el.append(toDom(child, doc)));
    if (tree.text) el.append(doc.createTextNode(tree.text));
    return el;
  }

  const api = { PALETTE, PRESETS, DISCLAIMER, niceTicks, linearScale, heatColor, lineChart, heatmap, scatter, bars, build, toSvgString, toDom };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (typeof window !== "undefined") window.TreepoloResearchCharts = api;
})();
