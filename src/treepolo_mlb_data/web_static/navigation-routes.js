(() => {
  "use strict";

  if (window.treepoloNavigationRoutes) return;
  window.treepoloNavigationRoutes = true;

  const upgraded = new WeakSet();
  let routeApplyTimer = null;

  function panels() { return window.treepoloPanels; }

  function upgradeButton(button) {
    if (!(button instanceof HTMLButtonElement) || upgraded.has(button) || !button.classList.contains("nav-item")) return null;
    upgraded.add(button);
    const panelId = button.dataset.panel;
    if (!panelId) return null;
    panels()?.register?.(panelId);

    const link = document.createElement("a");
    Array.from(button.attributes).forEach(attribute => {
      if (attribute.name !== "type" && attribute.name !== "disabled") link.setAttribute(attribute.name, attribute.value);
    });
    link.className = button.className;
    link.innerHTML = button.innerHTML;
    link.href = panels()?.hrefForPanel?.(panelId) || "#";
    link.dataset.route = panels()?.routeForPanel?.(panelId) || "";

    link.addEventListener("click", event => {
      if (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      if (document.body.classList.contains("busy")) return;
      panels()?.activate?.(panelId, { updateUrl:true, source:"route-link" });
    });

    button.replaceWith(link);
    return link;
  }

  function upgradeAll() {
    document.querySelectorAll("button.nav-item[data-panel]").forEach(upgradeButton);
    document.querySelectorAll("a.nav-item[data-panel]").forEach(link => {
      const panelId = link.dataset.panel;
      panels()?.register?.(panelId);
      link.dataset.route = panels()?.routeForPanel?.(panelId) || link.dataset.route || "";
      link.setAttribute("href", panels()?.hrefForPanel?.(panelId) || link.getAttribute("href") || "#");
    });
  }

  function applyRouteFromLocation(source = "route") {
    const route = new URL(window.location.href).searchParams.get("page");
    if (!route) return false;
    const panelId = panels()?.panelForRoute?.(route);
    if (!panelId || !document.getElementById(panelId)) return false;
    return Boolean(panels()?.activate?.(panelId, { updateUrl:false, source }));
  }

  function scheduleRouteApply() {
    clearTimeout(routeApplyTimer);
    routeApplyTimer = setTimeout(() => {
      upgradeAll();
      applyRouteFromLocation("route-discovery");
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
    applyRouteFromLocation("initial-route");

    const navigation = document.querySelector(".navigation-pane");
    if (navigation) new MutationObserver(scheduleRouteApply).observe(navigation, { childList:true, subtree:true });

    window.addEventListener("popstate", () => {
      upgradeAll();
      applyRouteFromLocation("popstate");
    });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", () => setTimeout(init, 0), { once:true });
  else setTimeout(init, 0);
})();
