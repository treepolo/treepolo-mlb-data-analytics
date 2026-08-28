(() => {
  "use strict";

  if (window.treepoloResultPaging) return;

  const PAGE_SIZE = 200;
  const LOAD_INTENT_MS = 4000;
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));
  let loadIntentUntil = 0;

  function displayValue(value) {
    if (value == null) return "—";
    if (typeof value === "number" && Number.isFinite(value)) {
      if (Number.isInteger(value)) return String(value);
      return value.toFixed(6).replace(/0+$/, "").replace(/\.$/, "");
    }
    return String(value);
  }

  function renderTablePage(table, section, page) {
    const columns = section.columns || [];
    const rows = section.rows || [];
    const start = page * PAGE_SIZE;
    const slice = rows.slice(start, start + PAGE_SIZE);
    let tbody = table.querySelector("tbody");
    if (!tbody) {
      tbody = document.createElement("tbody");
      table.append(tbody);
    }
    tbody.innerHTML = "";
    slice.forEach(row => {
      const tr = document.createElement("tr");
      columns.forEach(column => {
        const td = document.createElement("td");
        td.textContent = displayValue(row[column]);
        tr.append(td);
      });
      tbody.append(tr);
    });
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
    sections.forEach((section, index) => installPagerFor(tables[index], section));
  }

  function initialPage(result) {
    if (!result || typeof result !== "object") return result;
    const copySection = section => {
      const copy = { ...section };
      if (Array.isArray(section.rows)) copy.rows = section.rows.slice(0, PAGE_SIZE);
      return copy;
    };
    if (Array.isArray(result.sections)) return { ...result, sections:result.sections.map(copySection) };
    return copySection(result);
  }

  function prepareForRender(full) {
    if (!full || typeof full !== "object") return full;
    setTimeout(() => installPagers(full), 40);
    return initialPage(full);
  }

  function panelForMode(mode) {
    const map = {
      basic:"basic-panel", sequence_pattern:"sequence-panel", follow_event:"follow-panel",
      arsenal:"arsenal-panel", pitch_role:"role-panel", temporal:"temporal-panel",
      percentile:"percentile-panel", cross_level:"cross-panel", arsenal_change:"arsenal-change-panel",
      workflow:"workflow-panel", clustering:"clustering-panel", regression:"regression-panel",
      bootstrap:"bootstrap-panel", cluster_compare:"cluster-compare-panel",
    };
    return document.getElementById(map[mode] || "");
  }

  function resultLimitForPanel(panel) {
    if (!panel) return 500;
    const basic = panel.querySelector("#basic-limit");
    if (basic) return Number(basic.value || 200);
    const workflow = panel.querySelector("#s4-workflow-limit");
    if (workflow) return Number(workflow.value || 500);
    return Number(panel.querySelector(".ta-result-limit input")?.value || 500);
  }

  function isStoredDetail(url, method) {
    return method === "GET" && /\/api\/analysis\/(?:history|saved)\/\d+(?:\?|$)/.test(url);
  }

  function responseWithJson(response, body) {
    const headers = new Headers(response.headers);
    headers.delete("content-length");
    return new Response(JSON.stringify(body), {
      status: response.status,
      statusText: response.statusText,
      headers,
    });
  }

  // Mark intent before the older Library click handlers run. Only true Load
  // actions may page a stored detail response; Save As metadata reads stay raw.
  document.addEventListener("click", event => {
    const button = event.target.closest?.("#analysis-history-list button,#saved-analysis-list button");
    if (!button) return;
    const text = button.textContent || "";
    if (text.includes("載入") || /^\s*Load\s*$/i.test(text)) {
      loadIntentUntil = performance.now() + LOAD_INTENT_MS;
    }
  }, true);

  const priorFetch = window.fetch.bind(window);
  window.__taFetchLimiterInstalled = true;
  window.fetch = async function treepoloPagedFetch(input, init = {}) {
    const url = typeof input === "string" ? input : input?.url || "";
    const method = String(init?.method || "GET").toUpperCase();
    let requestInit = init;

    if (url.includes("/api/analyze") && method === "POST" && typeof init.body === "string") {
      try {
        const payload = JSON.parse(init.body);
        if (payload && payload.result_limit == null) {
          payload.result_limit = resultLimitForPanel(panelForMode(payload.mode));
          requestInit = { ...init, body:JSON.stringify(payload) };
        }
      } catch {}
    }

    const response = await priorFetch(input, requestInit);
    if (!response.ok) return response;

    if (url.includes("/api/analyze") && method === "POST") {
      try {
        const full = await response.clone().json();
        return responseWithJson(response, prepareForRender(full));
      } catch {
        return response;
      }
    }

    if (isStoredDetail(url, method) && performance.now() <= loadIntentUntil) {
      try {
        const body = await response.clone().json();
        const item = body?.item;
        if (!item?.result_available || !item.result) return response;
        return responseWithJson(response, {
          ...body,
          item: { ...item, result:prepareForRender(item.result) },
        });
      } catch {
        return response;
      }
    }

    return response;
  };

  window.treepoloResultPaging = {
    pageSize: PAGE_SIZE,
    initialPage,
    installPagers,
    prepareForRender,
  };
})();
