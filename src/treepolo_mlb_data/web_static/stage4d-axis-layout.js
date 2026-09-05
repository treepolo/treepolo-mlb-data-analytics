(() => {
  "use strict";

  if (window.__treepoloStage4DAxisLayout) return;
  window.__treepoloStage4DAxisLayout = true;

  const MIN_GAP_PX = 12;
  const OUTER_PADDING_PX = 8;
  const processedTitles = new WeakSet();
  let scheduled = false;
  let mutating = false;

  function yAxisTitle(svg) {
    return svg.querySelector('text.viz-label[transform^="rotate(-90"]');
  }

  function yTickLabels(svg, title) {
    return Array.from(svg.querySelectorAll('text.viz-label[text-anchor="end"]'))
      .filter((node) => node !== title);
  }

  function parseRotationCenterY(transform) {
    const match = String(transform || "").match(/rotate\(\s*-90(?:\.0+)?\s+[-+]?\d*\.?\d+\s+([-+]?\d*\.?\d+)\s*\)/i);
    return match ? Number(match[1]) : null;
  }

  function svgUnitsPerCssPixel(svg) {
    const rect = svg.getBoundingClientRect();
    const viewBox = svg.viewBox?.baseVal;
    if (!(rect.width > 0) || !viewBox || !(viewBox.width > 0)) return 1;
    return viewBox.width / rect.width;
  }

  function ensureYAxisClearance() {
    const svg = document.querySelector("#viz-canvas");
    if (!svg || mutating) return;
    const title = yAxisTitle(svg);
    if (!title || processedTitles.has(title)) return;

    const ticks = yTickLabels(svg, title);
    const titleRect = title.getBoundingClientRect();
    const tickRects = ticks
      .map((node) => node.getBoundingClientRect())
      .filter((rect) => rect.width > 0 && rect.height > 0);
    if (!tickRects.length || !(titleRect.width > 0) || !(titleRect.height > 0)) return;

    const tickLeft = Math.min(...tickRects.map((rect) => rect.left));
    const currentGap = tickLeft - titleRect.right;
    processedTitles.add(title);
    if (currentGap >= MIN_GAP_PX) return;

    const shiftCss = MIN_GAP_PX - currentGap;
    const unitsPerPx = svgUnitsPerCssPixel(svg);
    const shiftUnits = shiftCss * unitsPerPx;
    const outerUnits = (shiftCss + OUTER_PADDING_PX) * unitsPerPx;
    const oldX = Number(title.getAttribute("x") || 18);
    const newX = oldX - shiftUnits;
    const centerY = parseRotationCenterY(title.getAttribute("transform"));

    const width = Number(svg.getAttribute("width") || 1000);
    const height = Number(svg.getAttribute("height") || 620);
    const baseViewBox = svg.viewBox?.baseVal;
    const baseMinX = baseViewBox?.x || 0;
    const baseMinY = baseViewBox?.y || 0;
    const baseWidth = baseViewBox?.width || width;
    const baseHeight = baseViewBox?.height || height;

    mutating = true;
    title.setAttribute("x", String(newX));
    if (Number.isFinite(centerY)) {
      title.setAttribute("transform", `rotate(-90 ${newX} ${centerY})`);
    }
    // Expand the SVG viewport to the left so the title keeps its normal font size
    // and remains fully visible instead of being pushed outside and clipped.
    svg.setAttribute(
      "viewBox",
      `${baseMinX - outerUnits} ${baseMinY} ${baseWidth + outerUnits} ${baseHeight}`,
    );
    requestAnimationFrame(() => { mutating = false; });
  }

  function schedule() {
    if (scheduled) return;
    scheduled = true;
    requestAnimationFrame(() => {
      scheduled = false;
      ensureYAxisClearance();
    });
  }

  function boot() {
    const svg = document.querySelector("#viz-canvas");
    if (!svg) return;
    const observer = new MutationObserver(schedule);
    observer.observe(svg, {childList: true, subtree: true});
    document.querySelector("#visualization-panel")?.addEventListener("click", (event) => {
      if (event.target?.closest?.("#viz-render")) schedule();
    });
    document.querySelector("#visualization-panel")?.addEventListener("change", schedule);
    window.addEventListener("resize", schedule, {passive: true});
    schedule();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot, {once: true});
  else boot();
})();
