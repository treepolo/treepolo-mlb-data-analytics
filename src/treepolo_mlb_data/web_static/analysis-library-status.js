(() => {
  "use strict";

  let currentRevision = null;
  let savedItems = [];
  let historyItems = [];
  let refreshTimer = null;

  function api(path) {
    return fetch(path, { headers: { "Content-Type": "application/json" } }).then(async response => {
      const body = await response.json();
      if (!response.ok) throw new Error(body.error || `${response.status} ${response.statusText}`);
      return body;
    });
  }

  function injectStyles() {
    if (document.getElementById("analysis-library-status-styles")) return;
    const style = document.createElement("style");
    style.id = "analysis-library-status-styles";
    style.textContent = `
      .analysis-stale-badge { display:inline-block; margin-left:6px; padding:1px 5px; border:1px solid #a66a20; background:#fff3dc; font-size:11px; font-weight:600; white-space:nowrap; }
      .analysis-stale-warning { display:inline-block; margin-left:8px; padding:2px 7px; border:1px solid #a66a20; background:#fff3dc; font-size:11px; font-weight:600; }
      #analysis-history-list .analysis-history-id { width:48px; white-space:nowrap; text-align:right; font-variant-numeric:tabular-nums; }
      #saved-analysis-list .analysis-library-edit { margin-left:4px; margin-right:4px; }
    `;
    document.head.append(style);
  }

  function stale(item) {
    return Boolean(currentRevision && item?.data_revision && item.data_revision !== currentRevision);
  }

  function decorateRows(hostId, items, targetCellIndex) {
    const host = document.getElementById(hostId);
    if (!host) return;
    const rows = Array.from(host.querySelectorAll("tbody tr"));
    rows.forEach((row, index) => {
      const badges = Array.from(row.querySelectorAll(".analysis-stale-badge"));
      const existingBadge = badges.shift() || null;
      badges.forEach(node => node.remove());
      const item = items[index];
      if (!stale(item)) {
        existingBadge?.remove();
        return;
      }

      const text = "舊資料版本 Historical Data";
      const title = `Saved data revision: ${item.data_revision}; current: ${currentRevision}`;
      if (existingBadge) {
        if (existingBadge.textContent !== text) existingBadge.textContent = text;
        if (existingBadge.title !== title) existingBadge.title = title;
        return;
      }

      const badge = document.createElement("span");
      badge.className = "analysis-stale-badge";
      badge.textContent = text;
      badge.title = title;
      const cell = row.children[targetCellIndex] || row.lastElementChild;
      cell?.append(" ", badge);
    });
  }

  function decorateHistoryIds() {
    const host = document.getElementById("analysis-history-list");
    if (!host) return;
    const table = host.querySelector("table");
    if (!table) return;

    const headerRow = table.querySelector("thead tr");
    if (headerRow && !headerRow.querySelector(".analysis-history-id")) {
      const th = document.createElement("th");
      th.className = "analysis-history-id";
      th.textContent = "#";
      th.title = "分析紀錄編號 Analysis History ID";
      headerRow.prepend(th);
    }

    const rows = Array.from(table.querySelectorAll("tbody tr"));
    rows.forEach((row, index) => {
      const item = historyItems[index];
      let cell = row.querySelector(":scope > .analysis-history-id");
      if (!cell) {
        cell = document.createElement("td");
        cell.className = "analysis-history-id";
        row.prepend(cell);
      }
      cell.textContent = item?.id != null ? `#${item.id}` : "—";
      if (item?.id != null) cell.title = `Analysis History #${item.id}`;
      else cell.removeAttribute("title");
    });
  }

  function decorateSavedEditButtons() {
    const host = document.getElementById("saved-analysis-list");
    if (!host) return;
    const rows = Array.from(host.querySelectorAll("tbody tr"));
    rows.forEach((row, index) => {
      const item = savedItems[index];
      const actionCell = row.lastElementChild;
      if (!actionCell) return;
      let button = actionCell.querySelector(".analysis-library-edit");
      if (!item?.id) {
        button?.remove();
        return;
      }
      if (!button) {
        button = document.createElement("button");
        button.type = "button";
        button.className = "analysis-library-edit";
        button.textContent = "編輯 Edit";
        const deleteButton = Array.from(actionCell.querySelectorAll("button")).find(candidate => candidate.textContent.includes("刪除"));
        if (deleteButton) actionCell.insertBefore(button, deleteButton);
        else actionCell.append(button);
      }
      button.title = `編輯名稱與備註 Edit name and notes · ${item.name || `#${item.id}`}`;
    });
  }

  function decorate() {
    decorateHistoryIds();
    decorateSavedEditButtons();
    decorateRows("saved-analysis-list", savedItems, 2);
    // History receives the ID column at index 0, so Status shifts from 4 to 5.
    decorateRows("analysis-history-list", historyItems, 5);
  }

  function selectedItemFromClick(target) {
    const row = target.closest?.("tbody tr");
    if (!row) return null;
    const host = row.closest("#saved-analysis-list,#analysis-history-list");
    if (!host) return null;
    const rows = Array.from(host.querySelectorAll("tbody tr"));
    const index = rows.indexOf(row);
    if (index < 0) return null;
    return host.id === "saved-analysis-list" ? savedItems[index] : historyItems[index];
  }

  function showLoadedRevisionWarning(item) {
    const summary = document.getElementById("result-summary");
    if (!summary) return;
    summary.querySelectorAll(".analysis-stale-warning").forEach(node => node.remove());
    if (!stale(item) || !item?.result_available) return;
    const warning = document.createElement("span");
    warning.className = "analysis-stale-warning";
    warning.textContent = "舊資料版本結果 Historical Result — 重新執行會使用目前資料";
    warning.title = `Result revision: ${item.data_revision}; current revision: ${currentRevision}`;
    summary.append(" ", warning);
  }

  async function refresh() {
    try {
      const [status, saved, history] = await Promise.all([
        api("/api/data/status"),
        api("/api/analysis/saved"),
        api("/api/analysis/history?limit=100"),
      ]);
      currentRevision = status.data_revision || null;
      savedItems = saved.saved || [];
      historyItems = history.history || [];
      decorate();
    } catch {
      // Staleness badges, edit actions, and history IDs are supplemental. Analysis Library remains usable if status refresh fails.
    }
  }

  function scheduleRefresh() {
    clearTimeout(refreshTimer);
    refreshTimer = setTimeout(refresh, 40);
  }

  function libraryStructureChanged(mutation) {
    const target = mutation.target;
    const inside = target?.closest?.("#saved-analysis-list,#analysis-history-list") ||
      target?.id === "saved-analysis-list" || target?.id === "analysis-history-list";
    if (!inside) return false;
    const nodes = [...Array.from(mutation.addedNodes || []), ...Array.from(mutation.removedNodes || [])];
    return nodes.some(node => {
      if (node.nodeType !== 1) return false;
      if (node.classList?.contains("analysis-stale-badge") || node.classList?.contains("analysis-history-id") || node.classList?.contains("analysis-library-edit")) return false;
      return node.matches?.("table,tbody,tr") || Boolean(node.querySelector?.("table,tbody,tr"));
    });
  }

  function init() {
    injectStyles();
    scheduleRefresh();
    const library = document.querySelector("#analysis-library-panel") || document.body;
    const observer = new MutationObserver(mutations => {
      if (mutations.some(libraryStructureChanged)) scheduleRefresh();
    });
    observer.observe(library, { childList: true, subtree: true });

    document.addEventListener("treepolo:analysis-library-refresh-request", scheduleRefresh);
    document.addEventListener("click", event => {
      const editButton = event.target.closest?.("#saved-analysis-list .analysis-library-edit");
      if (editButton) {
        event.preventDefault();
        const item = selectedItemFromClick(editButton);
        if (!item) return;
        document.dispatchEvent(new CustomEvent("treepolo:analysis-edit-request", { detail: { item } }));
        return;
      }

      const button = event.target.closest?.("#saved-analysis-list button,#analysis-history-list button");
      if (!button || !button.textContent.includes("載入")) return;
      const item = selectedItemFromClick(button);
      if (!item) return;
      setTimeout(() => showLoadedRevisionWarning(item), 120);
    }, true);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init, { once: true });
  else init();
})();