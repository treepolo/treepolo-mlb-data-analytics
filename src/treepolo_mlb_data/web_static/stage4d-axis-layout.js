(() => {
  "use strict";

  if (window.__treepoloStage4DAxisLayout) return;
  window.__treepoloStage4DAxisLayout = true;

  let canvas = null;

  function labelFontPx() {
    const root = document.documentElement;
    const compat = getComputedStyle(root).getPropertyValue("--treepolo-cf-font-11").trim();
    const parsed = Number.parseFloat(compat);
    return Number.isFinite(parsed) && parsed > 0 ? parsed : 11;
  }

  function formatter(value) {
    if (!Number.isFinite(value)) return "—";
    if (Number.isInteger(value)) return value.toLocaleString("en-US");
    return value.toFixed(4).replace(/0+$/, "").replace(/\.$/, "");
  }

  function textWidth(value) {
    canvas ||= document.createElement("canvas");
    const context = canvas.getContext("2d");
    if (!context) return String(value).length * labelFontPx() * 0.6;
    const px = labelFontPx();
    context.font = `${px}px Arial, "Microsoft JhengHei", sans-serif`;
    return context.measureText(String(value)).width;
  }

  window.treepoloStage4DLeftMargin = function treepoloStage4DLeftMargin(yField, yValues, display = {}) {
    const values = Array.isArray(yValues) ? yValues.filter(Number.isFinite) : [];
    let min = display.y_min != null ? Number(display.y_min) : Math.min(...values);
    let max = display.y_max != null ? Number(display.y_max) : Math.max(...values);
    if (!Number.isFinite(min) || !Number.isFinite(max)) {
      min = 0;
      max = 1;
    }
    if (min === max) {
      const pad = Math.abs(min || 1) * 0.08;
      min -= pad;
      max += pad;
    } else {
      const pad = (max - min) * 0.06;
      if (display.y_min == null) min -= pad;
      if (display.y_max == null) max += pad;
    }

    const tickWidths = [];
    for (let index = 0; index <= 5; index += 1) {
      tickWidths.push(textWidth(formatter(min + (index / 5) * (max - min))));
    }
    const maxTickWidth = Math.max(0, ...tickWidths);
    const fontPx = labelFontPx();
    const titleThickness = fontPx * 1.35;
    const titleLeft = 18;
    const titleToTickGap = Math.max(10, fontPx * 0.8);
    const tickToAxisGap = 8;
    const required = titleLeft + titleThickness + titleToTickGap + maxTickWidth + tickToAxisGap;

    // The Y-axis title is rotated, so its string length affects vertical extent,
    // not horizontal margin. Clamp only pathological numeric tick widths.
    return Math.max(75, Math.min(210, Math.ceil(required)));
  };
})();
