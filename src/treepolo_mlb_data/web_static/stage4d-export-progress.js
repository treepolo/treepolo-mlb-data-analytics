(() => {
  "use strict";

  if (window.__treepoloStage4DExportProgressInstalled) return;
  window.__treepoloStage4DExportProgressInstalled = true;

  const originalFetch = window.fetch.bind(window);

  window.fetch = async function stage4dExportProgressFetch(input, init = {}) {
    const url = typeof input === "string" ? input : (input?.url || "");
    const method = String(init?.method || (typeof input !== "string" ? input?.method : "") || "GET").toUpperCase();
    const isVisualizationExport = method === "POST" && /(?:^|\/)api\/export(?:\?|$)/.test(url);
    if (!isVisualizationExport) return originalFetch(input, init);

    const button = document.querySelector("#viz-export-data");
    const priorText = button?.textContent || "";
    if (button) {
      button.disabled = true;
      button.textContent = "匯出中 Exporting…";
      button.setAttribute("aria-busy", "true");
    }

    try {
      return await originalFetch(input, init);
    } finally {
      if (button) {
        button.disabled = false;
        button.textContent = priorText || "匯出資料 Export Data";
        button.removeAttribute("aria-busy");
      }
    }
  };
})();
