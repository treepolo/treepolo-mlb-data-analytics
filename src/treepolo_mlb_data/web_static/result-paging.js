(() => {
  "use strict";

  if (window.treepoloResultPaging) return;

  const PAGE_SIZE = 200;
  const LEGACY_RESULT_LIMIT = 500;
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

  function displayValue(value) {
    if (value == null) return "—";
    if (typeof value === "number" && Number.isFinite(value)) {
      if (Number.isInteger(value)) return String(value);
      return value.toFixed(6).replace(/0+$/, "").replace(/\.$/, "");
    }
    return String(value);
  }

  function normalizedLimit(value, fallback = LEGACY_RESULT_LIMIT) {
    const parsed = Number(value);
    if (!Number.isFinite(parsed) || parsed <= 0) return fallback;
    return Math.min(Math.max(1, Math.trunc(parsed)), 5000);
  }

  function normalizeRetainedRows(result) {
    if (!result || typeof result !== "object") return result;
    const rootLimit = normalizedLimit(result?.result_limit, LEGACY_RESULT_LIMIT);
    const trim = section => {
      if (!section || typeof section !== "object") return section;
      const rows = section.rows;
      if (!Array.isArray(rows)) return section;
      const limit = normalizedLimit(section?.result_limit, rootLimit);
      if (rows.length > limit) rows.splice(limit);
      section.returned_row_count = rows.length;
      section.result_limit = limit;
      return section;
    };
    if (Array.isArray(result.sections)) result.sections.forEach(trim);
    else trim(result);
    return result;
  }

  function renderTablePage(table, section, page) {
    const columns = section?.columns || [];
    const rows = section?.rows || [];
    const start = page * PAGE_SIZE;
    const slice = rows.slice(start, start + PAGE_SIZE);
    let tbody = table?.querySelector("tbody");
    if (!tbody && table) {
      tbody = document.createElement("tbody");
      table.append(tbody);
    }
    if (!tbody) return;
    tbody.replaceChildren();
    const fragment = document.createDocumentFragment();
    slice.forEach(row => {
      const tr = document.createElement("tr");
      columns.forEach(column => {
        const td = document.createElement("td");
        td.textContent = displayValue(row[column]);
        tr.append(td);
      });
      fragment.append(tr);
    });
    tbody.append(fragment);
    window.treepoloPlayerNames?.enhanceTable?.(table);
  }

  function installPagerFor(table, section) {
    const rows = section?.rows || [];
    if (!table || rows.length <= PAGE_SIZE || table.dataset.taPager === "1") return;
    table.dataset.taPager = "1";
    const pager = document.createElement("div");
    pager.className = "ta-table-pager";
    let page = 0;
    const previous = document.createElement("button");
    previous.type = "button";
    previous.textContent = "‹ 上一頁 Prev";
    const next = document.createElement("button");
    next.type = "button";
    next.textContent = "下一頁 Next ›";
    const text = document.createElement("span");
    const update = () => {
      const start = page * PAGE_SIZE;
      const end = Math.min(rows.length, start + PAGE_SIZE);
      const total = Number(section.row_count ?? rows.length);
      text.textContent = `顯示 ${start + 1}–${end} / 已回傳 ${rows.length}${total > rows.length ? `（符合 ${total}）` : ""}`;
      previous.disabled = page === 0;
      next.disabled = end >= rows.length;
      renderTablePage(table, section, page);
    };
    previous.addEventListener("click", () => {
      if (page > 0) {
        page -= 1;
        update();
      }
    });
    next.addEventListener("click", () => {
      if ((page + 1) * PAGE_SIZE < rows.length) {
        page += 1;
        update();
      }
    });
    pager.append(previous, next, text);
    table.insertAdjacentElement("beforebegin", pager);
    update();
  }

  function installPagers(full) {
    const host = $("#result-content");
    if (!host || !full) return;
    const sections = Array.isArray(full.sections) ? full.sections : [full];
    const tables = $$("table.result-table", host);
    sections.forEach((section, index) => {
      const table = tables[index];
      window.treepoloPlayerNames?.enhanceTable?.(table);
      installPagerFor(table, section);
    });
  }

  function initialPage(result) {
    if (!result || typeof result !== "object") return result;
    const copySection = section => {
      const copy = { ...section };
      if (Array.isArray(section.rows)) copy.rows = section.rows.slice(0, PAGE_SIZE);
      return copy;
    };
    if (Array.isArray(result.sections)) {
      return { ...result, sections: result.sections.map(copySection) };
    }
    return copySection(result);
  }

  function schedule(full) {
    setTimeout(() => installPagers(full), 40);
  }

  function present(full, renderer) {
    const normalized = normalizeRetainedRows(full);
    const paged = initialPage(normalized);
    renderer(paged);
    schedule(normalized);
    return paged;
  }

  function panelForMode(mode) {
    const ids = {
      basic:"basic-panel", sequence_pattern:"sequence-panel", follow_event:"follow-panel",
      arsenal:"arsenal-panel", pitch_role:"role-panel", temporal:"temporal-panel",
      percentile:"percentile-panel", cross_level:"cross-panel", arsenal_change:"arsenal-change-panel",
      workflow:"workflow-panel", clustering:"clustering-panel", regression:"regression-panel",
      bootstrap:"bootstrap-panel", cluster_compare:"cluster-compare-panel",
    };
    return document.getElementById(ids[mode] || "");
  }

  function resultLimitForMode(mode) {
    const panel = panelForMode(mode);
    if (!panel) return 500;
    const basic = panel.querySelector("#basic-limit");
    if (basic) return Number(basic.value || 200);
    const workflow = panel.querySelector("#s4-workflow-limit");
    if (workflow) return Number(workflow.value || 500);
    return Number(panel.querySelector(".ta-result-limit input")?.value || 500);
  }

  function installFetchLimiter() {
    if (window.__taFetchLimiterInstalled) return;
    window.__taFetchLimiterInstalled = true;
    const priorFetch = window.fetch.bind(window);
    window.fetch = async function treepoloPagedAnalysisFetch(input, init = {}) {
      const url = typeof input === "string" ? input : input?.url || "";
      const method = String(init?.method || "GET").toUpperCase();
      let requestInit = init;
      if (url.includes("/api/analyze") && method === "POST" && typeof init.body === "string") {
        try {
          const payload = JSON.parse(init.body);
          if (payload && payload.result_limit == null) {
            payload.result_limit = resultLimitForMode(payload.mode);
            requestInit = { ...init, body: JSON.stringify(payload) };
          }
        } catch {}
      }

      const response = await priorFetch(input, requestInit);
      if (!(url.includes("/api/analyze") && method === "POST" && response.ok)) return response;
      try {
        const full = await response.clone().json();
        const normalized = normalizeRetainedRows(full);
        const paged = initialPage(normalized);
        schedule(normalized);
        const headers = new Headers(response.headers);
        headers.delete("content-length");
        return new Response(JSON.stringify(paged), {
          status: response.status,
          statusText: response.statusText,
          headers,
        });
      } catch {
        return response;
      }
    };
  }

  window.treepoloResultPaging = {
    PAGE_SIZE,
    LEGACY_RESULT_LIMIT,
    normalizeRetainedRows,
    initialPage,
    installPagers,
    schedule,
    present,
  };
  installFetchLimiter();
})();
