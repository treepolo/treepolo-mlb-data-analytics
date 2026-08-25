(() => {
  "use strict";

  let lastPayload = null;
  let lastResult = null;
  const nativeFetch = window.fetch.bind(window);

  const MODE_PANELS = {
    basic: "basic-panel",
    sequence_pattern: "sequence-panel",
    follow_event: "follow-panel",
    arsenal: "arsenal-panel",
    pitch_role: "role-panel",
    temporal: "temporal-panel",
    percentile: "percentile-panel",
    cross_level: "cross-panel",
    arsenal_change: "arsenal-change-panel",
    workflow: "workflow-panel",
    clustering: "clustering-panel",
    regression: "regression-panel",
    bootstrap: "bootstrap-panel",
  };

  const METRIC_NAMES = {
    avg: ["Average", "平均值"], sum: ["Sum", "總和"], min: ["Minimum", "最小值"],
    max: ["Maximum", "最大值"], median: ["Median", "中位數"],
    stddev_pop: ["Population standard deviation", "母體標準差"],
    stddev_samp: ["Sample standard deviation", "樣本標準差"],
  };

  function api(path, options = {}) {
    return nativeFetch(path, { headers: { "Content-Type": "application/json" }, ...options }).then(async response => {
      let body = {};
      try { body = await response.json(); } catch { /* ignore */ }
      if (!response.ok) throw new Error(body.error || `${response.status} ${response.statusText}`);
      return body;
    });
  }

  window.fetch = async function treepoloStage4Fetch(input, init = {}) {
    const response = await nativeFetch(input, init);
    const url = typeof input === "string" ? input : input?.url || "";
    const method = String(init?.method || "GET").toUpperCase();
    if (url.includes("/api/analyze") && method === "POST") {
      try {
        lastPayload = typeof init.body === "string" ? JSON.parse(init.body) : null;
      } catch {
        lastPayload = null;
      }
      if (response.ok) {
        try {
          lastResult = await response.clone().json();
          window.treepoloLastAnalysis = { payload: lastPayload, result: lastResult };
          setTimeout(() => {
            decorateCache(lastResult);
            refreshLibrary().catch(() => {});
          }, 30);
        } catch {
          lastResult = null;
        }
      }
    }
    return response;
  };

  function injectStyles() {
    if (document.getElementById("stage4-workspace-styles")) return;
    const style = document.createElement("style");
    style.id = "stage4-workspace-styles";
    style.textContent = `
      .analysis-library-toolbar { display:grid; grid-template-columns:minmax(180px,1fr) minmax(240px,2fr) auto; gap:8px; align-items:end; margin-bottom:12px; }
      .analysis-library-toolbar label { display:flex; flex-direction:column; gap:4px; }
      .analysis-library-table { width:100%; border-collapse:collapse; font-size:12px; }
      .analysis-library-table th,.analysis-library-table td { border:1px solid #aeb7c4; padding:5px 7px; vertical-align:top; }
      .analysis-library-table th { background:#e8eef7; text-align:left; }
      .analysis-library-actions { white-space:nowrap; }
      .analysis-library-actions button { margin-right:4px; }
      .cache-badge { display:inline-block; margin-left:8px; padding:1px 6px; border:1px solid #7893b5; background:#eef5ff; font-size:11px; font-weight:600; }
      .metric-row.metric-invalid .metric-field { outline:2px solid #b12828; background:#fff3f3; }
      .library-empty { padding:10px; color:#5b6572; }
      .library-section { margin-top:12px; }
    `;
    document.head.append(style);
  }

  function syncMetricRow(row) {
    if (!row) return;
    const fn = row.querySelector(".metric-function")?.value || "count";
    const field = row.querySelector(".metric-field");
    if (!field) return;
    const empty = Array.from(field.options).find(option => option.value === "");
    if (empty) {
      if (fn === "count") {
        empty.textContent = "不指定 None";
        empty.disabled = false;
      } else {
        empty.textContent = "請選擇欄位 Select Field";
        empty.disabled = false;
      }
    }
    field.required = fn !== "count";
    row.classList.toggle("metric-invalid", fn !== "count" && !field.value);
  }

  function validateBasicMetrics() {
    const invalid = [];
    document.querySelectorAll("#basic-metrics .metric-row").forEach(row => {
      syncMetricRow(row);
      const fn = row.querySelector(".metric-function")?.value || "count";
      const field = row.querySelector(".metric-field")?.value || "";
      if (fn !== "count" && !field) invalid.push(fn);
    });
    return invalid;
  }

  function showMetricError(fn) {
    const [en, zh] = METRIC_NAMES[fn] || [fn, "此指標"];
    const message = `${en} requires a metric field / ${zh}必須指定計算欄位`;
    const summary = document.querySelector("#result-summary");
    const content = document.querySelector("#result-content");
    if (summary) summary.textContent = "錯誤 Error";
    if (content) {
      content.innerHTML = "";
      const box = document.createElement("div");
      box.className = "error-box";
      const strong = document.createElement("strong");
      strong.textContent = "執行失敗 Analysis Failed";
      const detail = document.createElement("div");
      detail.textContent = message;
      box.append(strong, detail);
      content.append(box);
    }
    const status = document.querySelector("#status-message");
    if (status) status.textContent = message;
  }

  function installMetricValidation() {
    const host = document.querySelector("#basic-metrics");
    if (!host) return;
    host.querySelectorAll(".metric-row").forEach(syncMetricRow);
    new MutationObserver(() => host.querySelectorAll(".metric-row").forEach(syncMetricRow))
      .observe(host, { childList: true, subtree: true });
    host.addEventListener("change", event => {
      const row = event.target.closest?.(".metric-row");
      if (row) syncMetricRow(row);
    });
    document.addEventListener("click", event => {
      const button = event.target.closest?.('[data-run="basic"]');
      if (!button) return;
      const invalid = validateBasicMetrics();
      if (!invalid.length) return;
      event.preventDefault();
      event.stopImmediatePropagation();
      showMetricError(invalid[0]);
    }, true);
  }

  function decorateCache(result) {
    if (!result?.cache) return;
    const summary = document.querySelector("#result-summary");
    if (!summary) return;
    summary.querySelector?.(".cache-badge")?.remove?.();
    const badge = document.createElement("span");
    badge.className = "cache-badge";
    badge.textContent = result.cache.hit
      ? "快取命中 Cache Hit"
      : result.cache.stored ? "已寫入快取 Cached" : "結果過大未快取 Not Cached";
    summary.append(" ", badge);
  }

  function switchPanel(panelId) {
    document.querySelectorAll(".nav-item").forEach(item => item.classList.toggle("active", item.dataset.panel === panelId));
    document.querySelectorAll(".panel").forEach(panel => panel.classList.toggle("active-panel", panel.id === panelId));
  }

  function injectLibrary() {
    if (document.getElementById("analysis-library-panel")) return;
    const navigation = document.querySelector(".navigation-pane");
    const mainPane = document.querySelector(".main-pane");
    const resultWindow = document.querySelector("#result-window");
    if (!navigation || !mainPane || !resultWindow) return;

    const group = document.createElement("div");
    group.className = "task-group";
    group.innerHTML = '<div class="task-group-title">工作區 Workspace</div><button class="nav-item" data-panel="analysis-library-panel">分析紀錄 Analysis Library</button>';
    navigation.append(group);
    group.querySelector(".nav-item").addEventListener("click", () => {
      switchPanel("analysis-library-panel");
      refreshLibrary().catch(error => setLibraryError(error.message));
    });

    const panel = document.createElement("div");
    panel.id = "analysis-library-panel";
    panel.className = "panel";
    panel.innerHTML = `
      <div class="panel-heading">分析紀錄 Analysis Library</div>
      <div class="panel-body">
        <fieldset>
          <legend>儲存目前分析 Save Current Analysis</legend>
          <div class="analysis-library-toolbar">
            <label>名稱 Name<input id="analysis-save-name" type="text" placeholder="例如 FF 逐季平均球速"></label>
            <label>備註 Notes<input id="analysis-save-notes" type="text" placeholder="選填 Optional"></label>
            <button id="analysis-save-current" type="button">儲存 Save</button>
          </div>
          <p class="hint">儲存的是完整分析設定；若相同資料版本的結果仍在快取，也會直接保留可回看的結果。 Saves the complete analysis specification and reuses its cached result when available.</p>
        </fieldset>
        <fieldset class="library-section"><legend>已儲存分析 Saved Analyses</legend><div id="saved-analysis-list" class="library-empty">讀取中 Loading…</div></fieldset>
        <fieldset class="library-section"><legend>最近分析 History</legend><div id="analysis-history-list" class="library-empty">讀取中 Loading…</div></fieldset>
      </div>`;
    mainPane.insertBefore(panel, resultWindow);
    panel.querySelector("#analysis-save-current").addEventListener("click", saveCurrentAnalysis);
  }

  async function saveCurrentAnalysis() {
    if (!lastPayload) throw new Error("目前沒有可儲存的分析。 Run or load an analysis first.");
    const name = document.querySelector("#analysis-save-name")?.value?.trim() || "";
    if (!name) {
      document.querySelector("#analysis-save-name")?.focus();
      throw new Error("請輸入分析名稱。 Analysis name is required.");
    }
    const notes = document.querySelector("#analysis-save-notes")?.value || "";
    await api("/api/analysis/saved", {
      method: "POST",
      body: JSON.stringify({
        name,
        notes,
        analysis_payload: lastPayload,
        cache_key: lastResult?.cache?.key || null,
        data_revision: lastResult?.cache?.data_revision || null,
      }),
    });
    document.querySelector("#analysis-save-name").value = "";
    document.querySelector("#analysis-save-notes").value = "";
    await refreshLibrary();
  }

  function setLibraryError(message) {
    const saved = document.querySelector("#saved-analysis-list");
    const history = document.querySelector("#analysis-history-list");
    if (saved) saved.textContent = message;
    if (history) history.textContent = message;
  }

  function modeLabel(mode) {
    return ({
      basic: "基本分析 Basic", sequence_pattern: "球序模式 Sequence", follow_event: "後續事件 Follow-up",
      arsenal: "球種武器庫 Arsenal", pitch_role: "球種角色 Pitch Role", temporal: "時間序列 Temporal",
      percentile: "個人門檻 Threshold", cross_level: "層級比較 Level Comparison", arsenal_change: "武器庫變化 Arsenal Change",
      workflow: "研究工作流 Workflow", clustering: "自動分群 Clustering", regression: "迴歸 Regression", bootstrap: "Bootstrap",
    })[mode] || mode;
  }

  function formatTime(value) {
    try { return new Intl.DateTimeFormat("zh-TW", { dateStyle: "short", timeStyle: "medium" }).format(new Date(value)); }
    catch { return value || "—"; }
  }

  function button(text, handler) {
    const el = document.createElement("button");
    el.type = "button";
    el.textContent = text;
    el.addEventListener("click", handler);
    return el;
  }

  function renderSaved(items) {
    const host = document.querySelector("#saved-analysis-list");
    if (!host) return;
    host.innerHTML = "";
    if (!items.length) { host.className = "library-empty"; host.textContent = "尚未儲存分析。 No saved analyses."; return; }
    host.className = "";
    const table = document.createElement("table");
    table.className = "analysis-library-table";
    table.innerHTML = "<thead><tr><th>名稱 Name</th><th>模式 Mode</th><th>更新 Updated</th><th>備註 Notes</th><th>操作 Actions</th></tr></thead>";
    const body = document.createElement("tbody");
    items.forEach(item => {
      const tr = document.createElement("tr");
      [item.name, modeLabel(item.payload?.mode), formatTime(item.updated_at), item.notes || "—"].forEach(value => {
        const td = document.createElement("td"); td.textContent = value; tr.append(td);
      });
      const actions = document.createElement("td"); actions.className = "analysis-library-actions";
      actions.append(
        button("載入 Load", () => loadSaved(item.id)),
        button("刪除 Delete", () => deleteSaved(item.id)),
      );
      tr.append(actions); body.append(tr);
    });
    table.append(body); host.append(table);
  }

  function renderHistory(items) {
    const host = document.querySelector("#analysis-history-list");
    if (!host) return;
    host.innerHTML = "";
    if (!items.length) { host.className = "library-empty"; host.textContent = "尚無分析紀錄。 No analysis history."; return; }
    host.className = "";
    const table = document.createElement("table");
    table.className = "analysis-library-table";
    table.innerHTML = "<thead><tr><th>時間 Time</th><th>模式 Mode</th><th>結果 Rows</th><th>執行器 Backend</th><th>狀態 Status</th><th>操作 Actions</th></tr></thead>";
    const body = document.createElement("tbody");
    items.forEach(item => {
      const tr = document.createElement("tr");
      [formatTime(item.created_at), modeLabel(item.mode), item.row_count ?? "—", item.backend || "—", item.status].forEach(value => {
        const td = document.createElement("td"); td.textContent = value; tr.append(td);
      });
      const actions = document.createElement("td"); actions.className = "analysis-library-actions";
      actions.append(button("載入 Load", () => loadHistory(item.id)));
      tr.append(actions); body.append(tr);
    });
    table.append(body); host.append(table);
  }

  async function refreshLibrary() {
    if (!document.getElementById("analysis-library-panel")) return;
    const [saved, history] = await Promise.all([
      api("/api/analysis/saved"),
      api("/api/analysis/history?limit=100"),
    ]);
    renderSaved(saved.saved || []);
    renderHistory(history.history || []);
  }

  async function deleteSaved(id) {
    await api(`/api/analysis/saved/${id}`, { method: "DELETE" });
    await refreshLibrary();
  }

  async function loadSaved(id) {
    const body = await api(`/api/analysis/saved/${id}`);
    await loadAnalysisItem(body.item);
  }

  async function loadHistory(id) {
    const body = await api(`/api/analysis/history/${id}`);
    await loadAnalysisItem(body.item);
  }

  async function loadAnalysisItem(item) {
    if (!item?.payload) return;
    lastPayload = item.payload;
    if (item.result_available && item.result) {
      lastResult = item.result;
      renderStoredResult(item.result);
    }
    applyPayload(item.payload);
    const panelId = MODE_PANELS[item.payload.mode];
    if (panelId && document.getElementById(panelId)) switchPanel(panelId);
  }

  function setSelect(id, value) {
    const select = document.getElementById(id);
    if (!select) return;
    if (select.multiple) {
      const wanted = new Set(Array.isArray(value) ? value : [value]);
      Array.from(select.options).forEach(option => { option.selected = wanted.has(option.value); });
    } else {
      select.value = value == null ? "" : String(value);
    }
    select.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function setInput(id, value, checked = false) {
    const input = document.getElementById(id);
    if (!input) return;
    if (checked) input.checked = Boolean(value);
    else input.value = value == null ? "" : String(value);
    input.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function setFilters(name, filters = []) {
    const host = document.querySelector(`[data-filter-box="${name}"]`);
    const list = host?.querySelector(".filter-list");
    const add = host?.querySelector(".add-filter");
    if (!list || !add) return;
    list.innerHTML = "";
    (filters || []).forEach(spec => {
      add.click();
      const row = list.lastElementChild;
      if (!row) return;
      row.querySelector(".condition-field").value = spec.field || "";
      row.querySelector(".condition-op").value = spec.op || "eq";
      row.querySelector(".condition-value").value = Array.isArray(spec.value) ? spec.value.join(",") : (spec.value ?? "");
    });
  }

  function setSingleCondition(containerId, spec = {}) {
    const row = document.querySelector(`#${containerId} .condition-row`);
    if (!row) return;
    row.querySelector(".condition-field").value = spec.field || "";
    row.querySelector(".condition-op").value = spec.op || "eq";
    row.querySelector(".condition-value").value = Array.isArray(spec.value) ? spec.value.join(",") : (spec.value ?? "");
  }

  function setBasicMetrics(metrics = []) {
    const host = document.querySelector("#basic-metrics");
    const add = document.querySelector("[data-add-metric]");
    if (!host || !add) return;
    host.innerHTML = "";
    const values = metrics.length ? metrics : [{ function: "count", field: "", distinct: false }];
    values.forEach(spec => {
      add.click();
      const row = host.lastElementChild;
      row.querySelector(".metric-function").value = spec.function || "count";
      row.querySelector(".metric-field").value = spec.field || "";
      row.querySelector(".metric-distinct").checked = Boolean(spec.distinct);
      syncMetricRow(row);
    });
  }

  function setResultSort(mode, sorts = []) {
    const box = document.querySelector(`[data-result-ordering="${mode}"]`);
    const list = box?.querySelector(`[data-result-sort-list="${mode}"]`);
    const add = box?.querySelector(".add-result-sort");
    if (!list || !add) return;
    list.innerHTML = "";
    (sorts || []).forEach(spec => {
      add.click();
      const row = list.lastElementChild;
      row.querySelector(".result-sort-field").value = spec.field || "";
      row.querySelector(".result-sort-direction").value = spec.descending ? "desc" : "asc";
    });
  }

  function applyPayload(payload) {
    const mode = payload.mode;
    if (mode === "basic") {
      setFilters("basic", payload.filters); setSelect("basic-group", payload.group_by || []);
      setBasicMetrics(payload.metrics || []); setInput("basic-limit", payload.limit ?? 200);
    } else if (mode === "sequence_pattern") {
      setFilters("sequence", payload.filters); setSingleCondition("sequence-event", payload.event);
      setInput("sequence-occurrence", payload.occurrence ?? 1); setInput("sequence-exact", payload.exact_count ?? "");
      setSelect("sequence-arrangement", payload.arrangement || "any"); setInput("sequence-last", payload.require_last_event, true);
    } else if (mode === "follow_event") {
      setFilters("follow", payload.filters); setSingleCondition("follow-anchor", payload.anchor); setSingleCondition("follow-target", payload.target);
      setSingleCondition("follow-between", (payload.between || [])[0] || {}); setInput("follow-gap", payload.max_gap ?? 3);
    } else if (mode === "arsenal") {
      setFilters("arsenal", payload.filters); setSelect("arsenal-entities", payload.entity_fields || []); setInput("arsenal-min-usage", payload.min_usage ?? .05); setSelect("arsenal-tie", payload.tie_method || "dense_rank");
    } else if (mode === "pitch_role") {
      setFilters("role", payload.filters); setSelect("role-entities", payload.entity_fields || []); setSelect("role-metric-kind", payload.metric_kind || "usage_rate");
      setSelect("role-value-field", payload.value_field || "release_speed"); setSelect("role-function", payload.function || "avg"); setInput("role-rank", payload.rank ?? 1);
      setSelect("role-direction", payload.descending === false ? "asc" : "desc"); setInput("role-exclude", (payload.exclude_pitch_types || []).join(", ")); setSelect("role-tie", payload.tie_method || "dense_rank");
    } else if (mode === "temporal") {
      setFilters("temporal", payload.filters); setSelect("temporal-entities", payload.entity_fields || []); setSelect("temporal-period", payload.period_field || "game_pk");
      setSelect("temporal-value", payload.value_field || "release_speed"); setSelect("temporal-function", payload.function || "avg"); setSelect("temporal-direction", payload.direction || "previous"); setInput("temporal-offset", payload.offset ?? 1);
    } else if (mode === "percentile") {
      setFilters("percentile", payload.filters); setSelect("percentile-entities", payload.entity_fields || []); setSelect("percentile-value", payload.value_field || "release_speed");
      setInput("percentile-threshold", Number(payload.threshold ?? .8) * 100); setSelect("percentile-side", payload.side || "high");
    } else if (mode === "cross_level") {
      setFilters("cross", payload.filters); setSelect("cross-unit", payload.unit_fields || []); setSelect("cross-baseline", payload.baseline_fields || []);
      setSelect("cross-value", payload.value_field || "release_speed"); setSelect("cross-function", payload.function || "avg");
    } else if (mode === "arsenal_change") {
      setFilters("arsenal-change", payload.filters); setSelect("change-entities", payload.entity_fields || []); setInput("change-min-usage", payload.min_usage ?? .05);
      setInput("change-a-start", payload.period_a?.start || ""); setInput("change-a-end", payload.period_a?.end || ""); setInput("change-b-start", payload.period_b?.start || ""); setInput("change-b-end", payload.period_b?.end || "");
    }
    setResultSort(mode, payload.result_sort || []);
    document.dispatchEvent(new CustomEvent("treepolo:analysis-options-changed"));
  }

  function renderStoredResult(result) {
    const host = document.querySelector("#result-content");
    const summary = document.querySelector("#result-summary");
    if (!host || !summary) return;
    host.innerHTML = "";
    const renderSection = section => {
      const wrapper = document.createElement("div");
      if (section.title) { const h = document.createElement("div"); h.className = "result-section-title"; h.textContent = section.title; wrapper.append(h); }
      const table = document.createElement("table"); table.className = "result-table";
      const head = document.createElement("thead"); const hr = document.createElement("tr");
      (section.columns || []).forEach(column => { const th = document.createElement("th"); th.textContent = column; hr.append(th); }); head.append(hr); table.append(head);
      const body = document.createElement("tbody");
      (section.rows || []).forEach(row => { const tr = document.createElement("tr"); (section.columns || []).forEach(column => { const td = document.createElement("td"); td.textContent = row[column] == null ? "—" : String(row[column]); tr.append(td); }); body.append(tr); });
      table.append(body); wrapper.append(table); return wrapper;
    };
    if (result.sections) result.sections.forEach(section => host.append(renderSection(section)));
    else host.append(renderSection(result));
    const count = result.row_count ?? (result.sections || []).reduce((sum, section) => sum + Number(section.row_count || 0), 0);
    summary.textContent = `${count} 列 rows · 已載入保存結果 Loaded Stored Result`;
    decorateCache(result);
  }

  function init() {
    injectStyles();
    installMetricValidation();
    injectLibrary();
    refreshLibrary().catch(() => {});
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init, { once: true });
  else init();
})();
