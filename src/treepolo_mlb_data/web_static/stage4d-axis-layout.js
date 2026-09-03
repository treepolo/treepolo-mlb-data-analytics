(() => {
  "use strict";

  if (window.__treepoloStage4DAxisLayout) return;
  window.__treepoloStage4DAxisLayout = true;

  const MIN_VISUAL_GAP_PX = 10;
  const MIN_AXIS_TITLE_X = 4;
  let scheduled = false;
  let mutating = false;

  function svgUnitPerCssPixel(svg) {
    const rect = svg.getBoundingClientRect();
    const viewBox = svg.viewBox && svg.viewBox.baseVal;
    const width = viewBox && viewBox.width ? viewBox.width : Number(svg.getAttribute("width") || 0);
    if (!(rect.width > 0) || !(width > 0)) return 1;
    return width / rect.width;
  }

  function yAxisTitle(svg) {
    return svg.querySelector('text.viz-label[transform^="rotate(-90"]');
  }

  function yTickLabels(svg, title) {
    return Array.from(svg.querySelectorAll('text.viz-label[text-anchor="end"]')).filter((node) => node !== title);
  }

  function parseRotationCenterY(transform) {
    const match = String(transform || "").match(/rotate\(\s*-90(?:\.0+)?\s+[-+]?\d*\.?\d+\s+([-+]?\d*\.?\d+)\s*\)/i);
    return match ? Number(match[1]) : null;
  }

  function repairYAxisTitleSpacing() {
    const svg = document.querySelector("#viz-canvas");
    if (!svg || mutating) return;
    const title = yAxisTitle(svg);
    if (!title) return;
    const ticks = yTickLabels(svg, title);
    if (!ticks.length) return;

    const titleRect = title.getBoundingClientRect();
    const tickRects = ticks.map((node) => node.getBoundingClientRect()).filter((rect) => rect.width > 0 && rect.height > 0);
    if (!tickRects.length || !(titleRect.width > 0) || !(titleRect.height > 0)) return;

    const nearestTickLeft = Math.min(...tickRects.map((rect) => rect.left));
    const currentGap = nearestTickLeft - titleRect.right;
    if (currentGap >= MIN_VISUAL_GAP_PX) return;

    const unitsPerPx = svgUnitPerCssPixel(svg);
    const neededShift = (MIN_VISUAL_GAP_PX - currentGap) * unitsPerPx;
    const oldX = Number(title.getAttribute("x") || 18);
    const newX = Math.max(MIN_AXIS_TITLE_X, oldX - neededShift);
    if (!(newX < oldX - 0.01)) return;

    const centerY = parseRotationCenterY(title.getAttribute("transform"));
    mutating = true;
    title.setAttribute("x", String(newX));
    if (Number.isFinite(centerY)) {
      title.setAttribute("transform", `rotate(-90 ${newX} ${centerY})`);
    }
    requestAnimationFrame(() => { mutating = false; });
  }

  function scheduleRepair() {
    if (scheduled) return;
    scheduled = true;
    requestAnimationFrame(() => {
      scheduled = false;
      repairYAxisTitleSpacing();
    });
  }

  function boot() {
    const svg = document.querySelector("#viz-canvas");
    if (!svg) return;
    const observer = new MutationObserver(scheduleRepair);
    observer.observe(svg, {childList: true, subtree: true, attributes: true, attributeFilter: ["x", "transform", "viewBox", "width", "height"]});
    document.querySelector("#visualization-panel")?.addEventListener("click", (event) => {
      if (event.target?.closest?.("#viz-render")) scheduleRepair();
    });
    document.querySelector("#visualization-panel")?.addEventListener("change", scheduleRepair);
    window.addEventListener("resize", scheduleRepair, {passive: true});
    scheduleRepair();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot, {once: true});
  else boot();
})();
