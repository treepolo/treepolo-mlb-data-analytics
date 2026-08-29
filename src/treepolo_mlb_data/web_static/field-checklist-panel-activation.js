(() => {
  "use strict";

  if (window.treepoloFieldChecklistPanelActivation) return;
  window.treepoloFieldChecklistPanelActivation = true;

  const observed = new WeakSet();
  let refreshQueued = false;

  function refreshChecklists() {
    window.treepoloFieldChecklistsApi?.refresh?.();
  }

  function scheduleRefresh() {
    if (refreshQueued) return;
    refreshQueued = true;
    queueMicrotask(() => {
      refreshQueued = false;
      refreshChecklists();
    });
  }

  function observePanel(panel) {
    if (!panel || observed.has(panel)) return;
    observed.add(panel);
    new MutationObserver(mutations => {
      if (!mutations.some(mutation => mutation.attributeName === "class")) return;
      if (panel.classList.contains("active-panel")) scheduleRefresh();
    }).observe(panel, { attributes:true, attributeFilter:["class"] });
  }

  function scanPanels(root = document) {
    if (root.matches?.(".main-pane > .panel")) observePanel(root);
    root.querySelectorAll?.(".main-pane > .panel").forEach(observePanel);
  }

  function init() {
    scanPanels(document);
    const main = document.querySelector(".main-pane");
    if (main) {
      new MutationObserver(mutations => {
        mutations.forEach(mutation => {
          Array.from(mutation.addedNodes || []).forEach(node => {
            if (node.nodeType === 1) scanPanels(node);
          });
        });
      }).observe(main, { childList:true });
    }
    scheduleRefresh();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init, { once:true });
  else init();
})();
