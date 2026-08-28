(() => {
  "use strict";

  if (window.treepoloAnalysisLoadMetadata) return;
  window.treepoloAnalysisLoadMetadata = true;

  let savedItems = [];
  let historyItems = [];
  let refreshPromise = null;
  let refreshTimer = null;

  async function api(path) {
    const response = await fetch(path, { headers: { "Content-Type": "application/json" } });
    let body = {};
    try { body = await response.json(); } catch {}
    if (!response.ok) throw new Error(body.error || `${response.status} ${response.statusText}`);
    return body;
  }

  async function refreshItems() {
    if (refreshPromise) return refreshPromise;
    refreshPromise = Promise.all([
      api("/api/analysis/saved"),
      api("/api/analysis/history?limit=100"),
    ]).then(([saved, history]) => {
      savedItems = saved.saved || [];
      historyItems = history.history || [];
    }).finally(() => {
      refreshPromise = null;
    });
    return refreshPromise;
  }

  function scheduleRefresh() {
    clearTimeout(refreshTimer);
    refreshTimer = setTimeout(() => refreshItems().catch(() => {}), 40);
  }

  function sourceAt(hostId, index) {
    const item = hostId === "analysis-history-list" ? historyItems[index] : savedItems[index];
    if (!item?.payload) return null;
    return {
      payload: item.payload,
      history_id: hostId === "analysis-history-list" ? (item.id || null) : null,
      cache_key: item.cache_key || null,
      data_revision: item.data_revision || null,
      loaded_source_kind: hostId === "analysis-history-list" ? "history" : "saved",
      loaded_source_id: item.id || null,
    };
  }

  function sameMode(a, b) {
    return Boolean(a?.mode && b?.mode && a.mode === b.mode);
  }

  function enrichAfterLoad(source, previousCurrent) {
    const started = performance.now();
    const attempt = () => {
      const current = window.treepoloLastAnalysis;
      if (current && current !== previousCurrent && sameMode(current.payload, source.payload)) {
        window.treepoloLastAnalysis = {
          ...current,
          history_id: source.history_id || current.history_id || null,
          cache_key: source.cache_key || current.cache_key || null,
          data_revision: source.data_revision || current.data_revision || null,
          loaded_source_kind: source.loaded_source_kind,
          loaded_source_id: source.loaded_source_id,
        };
        document.dispatchEvent(new CustomEvent("treepolo:analysis-current-source-updated", {
          detail: {
            kind: source.loaded_source_kind,
            id: source.loaded_source_id,
            history_id: source.history_id,
            cache_key: source.cache_key,
            data_revision: source.data_revision,
          },
        }));
        return;
      }
      if (performance.now() - started < 4000) setTimeout(attempt, 25);
    };
    attempt();
  }

  async function handleLoadClick(button) {
    if (!button?.textContent?.includes("載入") && !button?.textContent?.includes("Load")) return;
    const row = button.closest("tbody tr");
    const host = row?.closest("#saved-analysis-list,#analysis-history-list");
    if (!row || !host) return;
    const rows = Array.from(host.querySelectorAll("tbody tr"));
    const index = rows.indexOf(row);
    if (index < 0) return;

    const previousCurrent = window.treepoloLastAnalysis;
    let source = sourceAt(host.id, index);
    if (!source) {
      try {
        await refreshItems();
        source = sourceAt(host.id, index);
      } catch {
        return;
      }
    }
    if (source) enrichAfterLoad(source, previousCurrent);
  }

  function libraryStructureChanged(mutation) {
    const target = mutation.target;
    const inside = target?.closest?.("#saved-analysis-list,#analysis-history-list") ||
      target?.id === "saved-analysis-list" || target?.id === "analysis-history-list";
    if (!inside) return false;
    const nodes = [...Array.from(mutation.addedNodes || []), ...Array.from(mutation.removedNodes || [])];
    return nodes.some(node => {
      if (node.nodeType !== 1) return false;
      if (node.classList?.contains("analysis-stale-badge")) return false;
      return node.matches?.("table,tbody,tr") || Boolean(node.querySelector?.("table,tbody,tr"));
    });
  }

  function init() {
    scheduleRefresh();
    document.addEventListener("click", event => {
      const button = event.target.closest?.("#saved-analysis-list button,#analysis-history-list button");
      if (button) handleLoadClick(button).catch(() => {});
    }, true);

    const library = document.querySelector("#analysis-library-panel") || document.body;
    new MutationObserver(mutations => {
      if (mutations.some(libraryStructureChanged)) scheduleRefresh();
    }).observe(library, { childList: true, subtree: true });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init, { once: true });
  else init();
})();