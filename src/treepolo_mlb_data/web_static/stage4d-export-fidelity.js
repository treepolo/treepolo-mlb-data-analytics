(() => {
  "use strict";

  if (window.__treepoloStage4DExportFidelity) return;
  window.__treepoloStage4DExportFidelity = true;

  const SVG_NS = "http://www.w3.org/2000/svg";
  const STYLE_ID = "treepolo-stage4d-export-style";
  const EXPORT_STYLE = `
    .viz-label { font: 11px Arial, "Microsoft JhengHei", sans-serif; fill: #263849; }
    .viz-title { font: 600 16px Arial, "Microsoft JhengHei", sans-serif; fill: #172b3a; }
    .viz-subtitle { font: 11px Arial, "Microsoft JhengHei", sans-serif; fill: #617080; }
    .viz-axis { stroke: #76889a; stroke-width: 1; }
    .viz-gridline { stroke: #e2e7ec; stroke-width: 1; }
    .viz-reference { stroke: #9b5b45; stroke-width: 1; stroke-dasharray: 5 4; }
    .viz-legend { font: 10px Arial, "Microsoft JhengHei", sans-serif; fill: #314354; }
  `;

  function ensureEmbeddedStyles() {
    const svg = document.querySelector("#viz-canvas");
    if (!svg) return;
    let style = svg.querySelector(`#${STYLE_ID}`);
    if (!style) {
      style = document.createElementNS(SVG_NS, "style");
      style.setAttribute("id", STYLE_ID);
      style.setAttribute("type", "text/css");
      style.textContent = EXPORT_STYLE;
      svg.insertBefore(style, svg.firstChild);
    } else if (style.textContent !== EXPORT_STYLE) {
      style.textContent = EXPORT_STYLE;
    }
  }

  function boot() {
    const svg = document.querySelector("#viz-canvas");
    if (!svg) return;

    let scheduled = false;
    const schedule = () => {
      if (scheduled) return;
      scheduled = true;
      queueMicrotask(() => {
        scheduled = false;
        ensureEmbeddedStyles();
      });
    };

    const observer = new MutationObserver(schedule);
    observer.observe(svg, {childList: true, subtree: false});

    // Export handlers in the primary bundle run in bubble phase. Embed the
    // stylesheet in capture phase so SVG/PNG/copy/report serialization always
    // sees a self-contained standalone SVG, even immediately after a render.
    document.addEventListener("click", event => {
      if (event.target?.closest?.("#viz-svg,#viz-png,#viz-copy,#viz-report")) {
        ensureEmbeddedStyles();
      }
    }, true);

    ensureEmbeddedStyles();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot, {once: true});
  else boot();
})();
