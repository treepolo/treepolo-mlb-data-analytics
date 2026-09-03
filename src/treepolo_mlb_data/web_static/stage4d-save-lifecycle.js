(() => {
  "use strict";

  if (window.__treepoloStage4DSaveLifecycle) return;
  window.__treepoloStage4DSaveLifecycle = true;

  const upstreamFetch = window.fetch.bind(window);

  function isVisualizationSave(url, method) {
    return method === "POST" && /\/api\/visualizations(?:\/\d+)?(?:\?.*)?$/.test(url);
  }

  function isExplicitSavedVisualizationEdit() {
    return document.querySelector("#viz-source-kind")?.value === "visualization";
  }

  function collectionUrl(url) {
    const parsed = new URL(url, window.location.href);
    return `${parsed.origin}/api/visualizations${parsed.search}`;
  }

  function setSaving(active) {
    const button = document.querySelector("#viz-save");
    if (button) {
      if (active) {
        if (!button.dataset.treepoloIdleText) button.dataset.treepoloIdleText = button.textContent || "儲存視覺化 Save Visualization";
        button.textContent = "儲存中 Saving…";
        button.disabled = true;
      } else {
        button.textContent = button.dataset.treepoloIdleText || "儲存視覺化 Save Visualization";
        button.disabled = false;
      }
    }
    if (active) {
      const status = document.querySelector("#viz-status");
      const appStatus = document.querySelector("#status-message");
      if (status) status.textContent = "正在儲存視覺化 Saving visualization…";
      if (appStatus) appStatus.textContent = "正在儲存視覺化 Saving visualization…";
    }
  }

  window.fetch = async function treepoloStage4DSaveLifecycleFetch(input, init = {}) {
    let url = typeof input === "string" ? input : input?.url || "";
    const method = String(init?.method || (typeof input !== "string" ? input?.method : "GET") || "GET").toUpperCase();
    if (!isVisualizationSave(url, method)) return upstreamFetch(input, init);

    let nextInput = input;
    if (/\/api\/visualizations\/\d+/.test(url) && !isExplicitSavedVisualizationEdit()) {
      // The primary Stage 4D bundle remembers the most recently created id.
      // For an analysis/history/saved-analysis source, Save means "create another
      // saved visualization", not "overwrite the last one". Only a visualization
      // explicitly loaded from Saved Visualizations is an in-place edit target.
      const nextUrl = collectionUrl(url);
      if (typeof input === "string") nextInput = nextUrl;
      else nextInput = new Request(nextUrl, input);
      url = nextUrl;
    }

    setSaving(true);
    try {
      return await upstreamFetch(nextInput, init);
    } finally {
      setSaving(false);
    }
  };
})();
