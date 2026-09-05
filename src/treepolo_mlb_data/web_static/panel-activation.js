(() => {
  "use strict";

  if (window.treepoloPanels) return;

  const routes = new Map(Object.entries({
    "data-panel": "data",
    "basic-panel": "basic-analysis",
    "sequence-panel": "sequence-pattern",
    "follow-panel": "follow-up-event",
    "arsenal-panel": "pitch-arsenal",
    "role-panel": "pitch-role",
    "temporal-panel": "temporal-comparison",
    "percentile-panel": "percentile-threshold",
    "cross-panel": "level-comparison",
    "arsenal-change-panel": "arsenal-change",
    "workflow-panel": "research-workflow",
    "clustering-panel": "clustering",
    "regression-panel": "regression",
    "bootstrap-panel": "bootstrap",
    "cluster-compare-panel": "cluster-comparison",
    "analysis-library-panel": "analysis-library",
  }));

  function defaultRoute(panelId) {
    return String(panelId || "page")
      .replace(/-panel$/, "")
      .replace(/[^a-z0-9-]+/gi, "-")
      .toLowerCase();
  }

  function register(panelId, route = null) {
    if (!panelId) return;
    routes.set(panelId, route || routes.get(panelId) || defaultRoute(panelId));
  }

  function routeForPanel(panelId) {
    return routes.get(panelId) || defaultRoute(panelId);
  }

  function panelForRoute(route) {
    for (const [panelId, value] of routes.entries()) {
      if (value === route) return panelId;
    }
    return null;
  }

  function hrefForPanel(panelId) {
    const url = new URL(window.location.href);
    url.searchParams.set("page", routeForPanel(panelId));
    url.hash = "";
    return `${url.pathname}${url.search}`;
  }

  function activePanelId() {
    return document.querySelector(".main-pane > .panel.active-panel")?.id || null;
  }

  function activate(panelId, { updateUrl = false, replaceUrl = false, source = "programmatic" } = {}) {
    const panel = document.getElementById(panelId);
    if (!panel) return false;
    register(panelId);

    document.querySelectorAll(".nav-item[data-panel]").forEach(item => {
      item.classList.toggle("active", item.dataset.panel === panelId);
    });
    document.querySelectorAll(".main-pane > .panel").forEach(item => {
      item.classList.toggle("active-panel", item.id === panelId);
    });

    if (updateUrl) {
      const href = hrefForPanel(panelId);
      const state = { panel:panelId };
      if (replaceUrl) window.history.replaceState(state, "", href);
      else window.history.pushState(state, "", href);
    }

    document.dispatchEvent(new CustomEvent("treepolo:panel-activated", {
      detail: { panelId, route:routeForPanel(panelId), source },
    }));
    return true;
  }

  window.treepoloPanels = {
    activate,
    register,
    routeForPanel,
    panelForRoute,
    hrefForPanel,
    activePanelId,
  };
})();
