(() => {
  "use strict";

  if (window.__treepoloMinimumFontCompat) return;
  window.__treepoloMinimumFontCompat = true;

  // The desktop UI intentionally uses a 10–16px typography scale. Chrome's
  // minimum-font setting clamps every smaller declaration before page zoom is
  // applied, so compensating from the 12px body size alone collapses the whole
  // hierarchy. Base the compatibility zoom on the smallest designed size and
  // pre-expand each intended font size before zooming back down.
  const BODY_FONT_PX = 12;
  const DESIGN_MIN_FONT_PX = 10;
  const ENABLE_THRESHOLD_PX = 14;
  const MIN_SCALE = 0.40;

  function measureEffectiveMinimumFontPx() {
    const host = document.createElement("div");
    host.setAttribute("aria-hidden", "true");
    Object.assign(host.style, {
      position: "fixed",
      left: "-100000px",
      top: "-100000px",
      visibility: "hidden",
      whiteSpace: "nowrap",
      pointerEvents: "none",
      fontFamily: 'Tahoma, "Microsoft JhengHei", "Segoe UI", Arial, sans-serif',
      fontWeight: "400",
      fontStyle: "normal",
      letterSpacing: "0",
    });

    // Mixed Latin/CJK content mirrors this application's bilingual labels.
    const sample = "MMMMii000資料視覺化分析球種設定";
    const small = document.createElement("span");
    const large = document.createElement("span");
    small.textContent = sample;
    large.textContent = sample;
    small.style.fontSize = `${DESIGN_MIN_FONT_PX}px`;
    large.style.fontSize = "120px";
    host.append(small, large);
    document.body.append(host);
    const smallWidth = small.getBoundingClientRect().width;
    const largeWidth = large.getBoundingClientRect().width;
    host.remove();
    if (!(smallWidth > 0) || !(largeWidth > 0)) return BODY_FONT_PX;
    return 120 * (smallWidth / largeWidth);
  }

  function injectStyles() {
    if (document.querySelector("#treepolo-minimum-font-compat-style")) return;
    const style = document.createElement("style");
    style.id = "treepolo-minimum-font-compat-style";
    style.textContent = `
      html.treepolo-minimum-font-compat .app-window {
        zoom: var(--treepolo-font-compat-scale);
        max-width: none;
        font-size: var(--treepolo-cf-font-12);
      }

      /* Restore the original 10/11/12/13/16px visual typography hierarchy.
         Values are expanded before zoom so Chrome's minimum-font clamp no
         longer changes them. */
      html.treepolo-minimum-font-compat .title-icon { font-size: var(--treepolo-cf-font-16); }
      html.treepolo-minimum-font-compat .title-text,
      html.treepolo-minimum-font-compat .panel-heading { font-size: var(--treepolo-cf-font-13); }
      html.treepolo-minimum-font-compat .status-key,
      html.treepolo-minimum-font-compat .result-table,
      html.treepolo-minimum-font-compat .viz-source-meta,
      html.treepolo-minimum-font-compat .viz-sampling-note,
      html.treepolo-minimum-font-compat .viz-library-table,
      html.treepolo-minimum-font-compat .viz-mini { font-size: var(--treepolo-cf-font-11); }
      html.treepolo-minimum-font-compat .stage4d-controls label { font-size: var(--treepolo-cf-font-12); }
      html.treepolo-minimum-font-compat .viz-label { font-size: var(--treepolo-cf-font-11); }
      html.treepolo-minimum-font-compat .viz-title { font-size: var(--treepolo-cf-font-16); }
      html.treepolo-minimum-font-compat .viz-subtitle { font-size: var(--treepolo-cf-font-11); }
      html.treepolo-minimum-font-compat .viz-legend { font-size: var(--treepolo-cf-font-10); }

      html.treepolo-minimum-font-compat .workspace {
        grid-template-columns: var(--treepolo-cf-nav-width) minmax(0, 1fr);
      }
      html.treepolo-minimum-font-compat .title-bar { height: var(--treepolo-cf-title-height); }
      html.treepolo-minimum-font-compat .menu-bar { height: var(--treepolo-cf-menu-height); }
      html.treepolo-minimum-font-compat .toolbar { min-height: var(--treepolo-cf-toolbar-height); }
      html.treepolo-minimum-font-compat button,
      html.treepolo-minimum-font-compat select,
      html.treepolo-minimum-font-compat input[type="text"],
      html.treepolo-minimum-font-compat input[type="number"],
      html.treepolo-minimum-font-compat input[type="date"] { min-height: var(--treepolo-cf-control-height); }
      html.treepolo-minimum-font-compat select[multiple],
      html.treepolo-minimum-font-compat .field-checklist {
        min-height: var(--treepolo-cf-checklist-min);
      }
      html.treepolo-minimum-font-compat .field-checklist { max-height: var(--treepolo-cf-checklist-max); }
      html.treepolo-minimum-font-compat .field-check-item { min-height: var(--treepolo-cf-check-item-height); }
      html.treepolo-minimum-font-compat .analysis-progress-track { height: var(--treepolo-cf-progress-height); }
      html.treepolo-minimum-font-compat .result-content { max-height: var(--treepolo-cf-result-max-height); }
      html.treepolo-minimum-font-compat .form-grid {
        grid-template-columns: repeat(2, minmax(var(--treepolo-cf-form-min), 1fr));
      }
      html.treepolo-minimum-font-compat .form-grid.four-cols {
        grid-template-columns: repeat(4, minmax(var(--treepolo-cf-four-col-min), 1fr));
      }
      html.treepolo-minimum-font-compat .compact-grid {
        grid-template-columns: repeat(2, minmax(var(--treepolo-cf-compact-min), var(--treepolo-cf-compact-max)));
      }
      html.treepolo-minimum-font-compat .status-grid {
        grid-template-columns: repeat(auto-fit, minmax(var(--treepolo-cf-status-min), 1fr));
      }
      html.treepolo-minimum-font-compat .stage4d-grid {
        grid-template-columns: minmax(var(--treepolo-cf-viz-left-min), var(--treepolo-cf-viz-left-max)) minmax(var(--treepolo-cf-viz-canvas-min), 1fr);
      }
      html.treepolo-minimum-font-compat .stage4d-canvas-frame { min-height: var(--treepolo-cf-viz-frame-height); }
      @media (max-width: 799px) {
        html.treepolo-minimum-font-compat .stage4d-grid { grid-template-columns: 1fr; }
      }
    `;
    document.head.append(style);
  }

  function setLengthVariable(root, name, intendedPx, scale) {
    root.style.setProperty(name, `${(intendedPx / scale).toFixed(3)}px`);
  }

  function applyGeometry(scale, effectiveFontPx) {
    const root = document.documentElement;
    root.classList.add("treepolo-minimum-font-compat");
    root.dataset.treepoloEffectiveMinimumFont = effectiveFontPx.toFixed(2);
    root.dataset.treepoloFontCompatScale = scale.toFixed(4);
    root.style.setProperty("--treepolo-font-compat-scale", scale.toFixed(5));

    // Typography is geometry too: pre-expand intended sizes so after app zoom
    // they land exactly on the original design sizes instead of the browser's
    // clamped minimum.
    for (const px of [10, 11, 12, 13, 16]) {
      setLengthVariable(root, `--treepolo-cf-font-${px}`, px, scale);
    }

    const dimensions = {
      "--treepolo-cf-nav-width": 228,
      "--treepolo-cf-title-height": 34,
      "--treepolo-cf-menu-height": 27,
      "--treepolo-cf-toolbar-height": 37,
      "--treepolo-cf-control-height": 24,
      "--treepolo-cf-checklist-min": 112,
      "--treepolo-cf-checklist-max": 178,
      "--treepolo-cf-check-item-height": 22,
      "--treepolo-cf-progress-height": 18,
      "--treepolo-cf-result-max-height": 470,
      "--treepolo-cf-form-min": 220,
      "--treepolo-cf-four-col-min": 150,
      "--treepolo-cf-compact-min": 180,
      "--treepolo-cf-compact-max": 320,
      "--treepolo-cf-status-min": 210,
      "--treepolo-cf-viz-left-min": 245,
      "--treepolo-cf-viz-left-max": 310,
      "--treepolo-cf-viz-canvas-min": 420,
      "--treepolo-cf-viz-frame-height": 520,
    };
    Object.entries(dimensions).forEach(([name, value]) => setLengthVariable(root, name, value, scale));

    const app = document.querySelector(".app-window");
    const workspace = document.querySelector(".workspace");
    if (!app) return;
    const availableWidth = Math.max(320, window.innerWidth - 36);
    const intendedWidth = Math.min(1500, availableWidth);
    app.style.width = `${(intendedWidth / scale).toFixed(2)}px`;
    app.style.minHeight = `${(Math.max(320, window.innerHeight - 36) / scale).toFixed(2)}px`;
    if (workspace) {
      workspace.style.minHeight = `${(Math.max(200, window.innerHeight - 166) / scale).toFixed(2)}px`;
    }
  }

  function boot() {
    injectStyles();
    const effectiveFontPx = measureEffectiveMinimumFontPx();
    if (!(effectiveFontPx > ENABLE_THRESHOLD_PX)) return;

    // At Chrome minimum font 20px this resolves to 10/20 = 0.50. Because the
    // designed fonts are pre-expanded by 1/scale, every intended size from
    // 10px upward stays at or above the browser minimum before zoom.
    const scale = Math.max(MIN_SCALE, Math.min(1, DESIGN_MIN_FONT_PX / effectiveFontPx));
    applyGeometry(scale, effectiveFontPx);
    window.addEventListener("resize", () => applyGeometry(scale, effectiveFontPx), {passive: true});
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot, {once: true});
  else boot();
})();
