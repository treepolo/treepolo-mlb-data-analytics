(() => {
  "use strict";

  if (window.treepoloNavigationRoutes) return;
  window.treepoloNavigationRoutes = true;

  const ROUTES = {
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
  };

  const upgraded = new WeakSet();
  let routeApplyTimer = null;

  function routeForPanel(panelId) {
    if (ROUTES[panelId]) return ROUTES[panelId];
    return String(panelId || "page").replace(/-panel$/, "").replace(/[^a-z0-9-]+/gi, "-").toLowerCase();
  }

  function hrefForPanel(panelId) {
    const url = new URL(window.location.href);
    url.searchParams.set("page", routeForPanel(panelId));
    url.hash = "";
    return `${url.pathname}${url.search}`;
  }

  function markActive(panelId) {
    document.querySelectorAll(".nav-item").forEach(item => {
      item.classList.toggle("active", item.dataset.panel === panelId);
    });
    document.querySelectorAll(".panel").forEach(panel => {
      panel.classList.toggle("active-panel", panel.id === panelId);
    });
  }

  function activateLink(link, { updateUrl = false } = {}) {
    if (!link) return false;
    const legacyButton = link.__treepoloLegacyButton;
    const panelId = link.dataset.panel;
    if (!panelId || !document.getElementById(panelId)) return false;

    if (updateUrl) window.history.pushState({ panel: panelId }, "", link.getAttribute("href"));
    if (legacyButton && !legacyButton.disabled) legacyButton.click();
    markActive(panelId);
    return true;
  }

  function upgradeButton(button) {
    if (!(button instanceof HTMLButtonElement) || upgraded.has(button) || !button.classList.contains("nav-item")) return null;
    upgraded.add(button);

    const link = document.createElement("a");
    Array.from(button.attributes).forEach(attribute => {
      if (attribute.name !== "type" && attribute.name !== "disabled") link.setAttribute(attribute.name, attribute.value);
    });
    link.className = button.className;
    link.innerHTML = button.innerHTML;
    link.href = hrefForPanel(button.dataset.panel);
    link.dataset.route = routeForPanel(button.dataset.panel);
    link.__treepoloLegacyButton = button;

    link.addEventListener("click", event => {
      if (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      if (document.body.classList.contains("busy") || button.disabled) return;
      activateLink(link, { updateUrl: true });
    });

    button.replaceWith(link);
    return link;
  }

  function upgradeAll() {
    document.querySelectorAll("button.nav-item[data-panel]").forEach(upgradeButton);
    document.querySelectorAll("a.nav-item[data-panel]").forEach(link => {
      if (!link.dataset.route) link.dataset.route = routeForPanel(link.dataset.panel);
      if (!link.getAttribute("href")) link.setAttribute("href", hrefForPanel(link.dataset.panel));
    });
  }

  function applyRouteFromLocation() {
    const route = new URL(window.location.href).searchParams.get("page");
    if (!route) return false;
    const link = Array.from(document.querySelectorAll("a.nav-item[data-panel]")).find(item => item.dataset.route === route);
    return activateLink(link, { updateUrl: false });
  }

  function scheduleRouteApply() {
    clearTimeout(routeApplyTimer);
    routeApplyTimer = setTimeout(() => {
      upgradeAll();
      applyRouteFromLocation();
    }, 0);
  }

  function injectStyles() {
    if (document.getElementById("navigation-route-styles")) return;
    const style = document.createElement("style");
    style.id = "navigation-route-styles";
    style.textContent = `
      a.nav-item { text-decoration:none; cursor:pointer; }
      a.nav-item:visited { color:#143a70; }
      a.nav-item.active, a.nav-item.active:visited { color:#fff; }
      body.busy a.nav-item { cursor:default; }
    `;
    document.head.append(style);
  }

  function init() {
    injectStyles();
    upgradeAll();
    applyRouteFromLocation();

    const navigation = document.querySelector(".navigation-pane");
    if (navigation) {
      new MutationObserver(scheduleRouteApply).observe(navigation, { childList: true, subtree: true });
    }

    window.addEventListener("popstate", () => {
      upgradeAll();
      applyRouteFromLocation();
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => setTimeout(init, 0), { once: true });
  } else {
    setTimeout(init, 0);
  }
})();
