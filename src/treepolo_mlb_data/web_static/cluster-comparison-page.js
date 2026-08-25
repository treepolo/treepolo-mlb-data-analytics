(() => {
  "use strict";

  const MODE = "cluster_compare";
  const PANEL_ID = "cluster-compare-panel";

  function csv(value) {
    return String(value || "").split(",").map(item => item.trim()).filter(Boolean);
  }
  function typed(value) {
    const text = String(value ?? "").trim();
    if (text === "") return "";
    const number = Number(text);
    return Number.isFinite(number) ? number : text;
  }
  function escapeHtml(value) {
    const div = document.createElement("div"); div.textContent = String(value ?? ""); return div.innerHTML;
  }

  function injectStyles() {
    if (document.getElementById("cluster-compare-styles")) return;
    const style = document.createElement("style");
    style.id = "cluster-compare-styles";
    style.textContent = `
      .cc-grid { display:grid; grid-template-columns:repeat(3,minmax(180px,1fr)); gap:8px 12px; }
      .cc-grid label { display:flex; flex-direction:column; gap:3px; }
      .cc-filter-list { display:flex; flex-direction:column; gap:6px; }
      .cc-filter-row { display:grid; grid-template-columns:minmax(180px,1fr) 170px minmax(160px,1fr) auto; gap:7px; }
      @media (max-width:900px) { .cc-grid,.cc-filter-row { grid-template-columns:1fr; } }
    `;
    document.head.append(style);
  }

  function fieldOptions() {
    const source = document.querySelector("#basic-group");
    const options = source ? Array.from(source.options) : [];
    return '<option value="">請選擇 Select Field</option>' + options.map(option =>
      `<option value="${escapeHtml(option.value)}">${escapeHtml(option.textContent)}</option>`
    ).join("");
  }

  function refreshFields() {
    const html = fieldOptions();
    document.querySelectorAll(`#${PANEL_ID} .cc-field`).forEach(select => {
      const value = select.value;
      select.innerHTML = html;
      if (Array.from(select.options).some(option => option.value === value)) select.value = value;
    });
    const selection = document.querySelector("#cc-selection-field");
    const evaluation = document.querySelector("#cc-evaluation-field");
    if (selection && !selection.value) selection.value = "estimated_woba_using_speedangle";
    if (evaluation && !evaluation.value) evaluation.value = "estimated_woba_using_speedangle";
  }

  function addFilter(preset = {}) {
    const list = document.querySelector("#cc-filter-list");
    if (!list) return;
    const row = document.createElement("div");
    row.className = "cc-filter-row";
    row.innerHTML = `
      <select class="cc-filter-field cc-field"></select>
      <select class="cc-filter-op">
        <option value="eq">等於 Equals</option><option value="ne">不等於 Not Equal</option>
        <option value="gt">大於 Greater Than</option><option value="ge">大於等於 At Least</option>
        <option value="lt">小於 Less Than</option><option value="le">小於等於 At Most</option>
        <option value="in">包含於清單 In List</option><option value="not_in">不包含於清單 Not In List</option>
        <option value="is_null">沒有資料 Is Null</option><option value="not_null">有資料 Is Not Null</option>
      </select>
      <input class="cc-filter-value" type="text" placeholder="數值 Value">
      <button type="button">×</button>`;
    list.append(row); refreshFields();
    row.querySelector(".cc-filter-field").value = preset.field || "";
    row.querySelector(".cc-filter-op").value = preset.op || "eq";
    row.querySelector(".cc-filter-value").value = Array.isArray(preset.value) ? preset.value.join(",") : (preset.value ?? "");
    row.querySelector("button").addEventListener("click", () => row.remove());
  }

  function readFilters() {
    return Array.from(document.querySelectorAll("#cc-filter-list .cc-filter-row")).map(row => {
      const op = row.querySelector(".cc-filter-op").value;
      let value = row.querySelector(".cc-filter-value").value;
      if (["in", "not_in"].includes(op)) value = csv(value);
      else value = typed(value);
      return { field: row.querySelector(".cc-filter-field").value, op, value };
    }).filter(item => item.field);
  }

  function buildPayload() {
    const selectionField = document.querySelector("#cc-selection-field")?.value || "";
    const evaluationField = document.querySelector("#cc-evaluation-field")?.value || selectionField;
    return {
      mode: MODE,
      filters: readFilters(),
      entity_fields: csv(document.querySelector("#cc-entities")?.value || "pitcher"),
      min_usage: Number(document.querySelector("#cc-min-usage")?.value || .05),
      reference_pitch_type: document.querySelector("#cc-reference")?.value?.trim() || "FF",
      selection_value_field: selectionField,
      selection_function: document.querySelector("#cc-selection-function")?.value || "avg",
      selection_direction: document.querySelector("#cc-selection-direction")?.value || "asc",
      tie_method: document.querySelector("#cc-tie")?.value || "row_number",
      features: csv(document.querySelector("#cc-features")?.value),
      method: document.querySelector("#cc-method")?.value || "kmeans",
      clusters: Number(document.querySelector("#cc-clusters")?.value || 3),
      standardize: Boolean(document.querySelector("#cc-standardize")?.checked),
      seed: Number(document.querySelector("#cc-seed")?.value || 42),
      evaluation_field: evaluationField,
      evaluation_direction: document.querySelector("#cc-evaluation-direction")?.value || "asc",
      max_input_rows: Number(document.querySelector("#cc-max-rows")?.value || 200000),
    };
  }

  function applyPayload(payload) {
    if (payload?.mode !== MODE) return false;
    const set = (id, value) => { const el = document.getElementById(id); if (el) el.value = value == null ? "" : String(value); };
    set("cc-entities", (payload.entity_fields || ["pitcher"]).join(","));
    set("cc-min-usage", payload.min_usage ?? .05);
    set("cc-reference", payload.reference_pitch_type || "FF");
    refreshFields();
    set("cc-selection-field", payload.selection_value_field || "");
    set("cc-selection-function", payload.selection_function || "avg");
    set("cc-selection-direction", payload.selection_direction || "asc");
    set("cc-tie", payload.tie_method || "row_number");
    set("cc-features", (payload.features || []).join(","));
    set("cc-method", payload.method || "kmeans");
    set("cc-clusters", payload.clusters ?? 3);
    const standardize = document.getElementById("cc-standardize"); if (standardize) standardize.checked = payload.standardize !== false;
    set("cc-seed", payload.seed ?? 42);
    set("cc-evaluation-field", payload.evaluation_field || payload.selection_value_field || "");
    set("cc-evaluation-direction", payload.evaluation_direction || "asc");
    set("cc-max-rows", payload.max_input_rows ?? 200000);
    const list = document.getElementById("cc-filter-list"); if (list) list.innerHTML = "";
    (payload.filters || []).forEach(addFilter);
    switchPanel();
    return true;
  }

  function switchPanel() {
    document.querySelectorAll(".nav-item").forEach(item => item.classList.toggle("active", item.dataset.panel === PANEL_ID));
    document.querySelectorAll(".panel").forEach(panel => panel.classList.toggle("active-panel", panel.id === PANEL_ID));
  }

  function render(result) {
    const host = document.querySelector("#result-content");
    const summary = document.querySelector("#result-summary");
    if (!host || !summary) return;
    host.innerHTML = "";
    (result.sections || []).forEach(section => {
      const wrapper = document.createElement("div");
      const heading = document.createElement("div"); heading.className = "result-section-title"; heading.textContent = section.title || "Result"; wrapper.append(heading);
      const table = document.createElement("table"); table.className = "result-table";
      const thead = document.createElement("thead"); const hr = document.createElement("tr");
      (section.columns || []).forEach(column => { const th = document.createElement("th"); th.textContent = column; hr.append(th); }); thead.append(hr); table.append(thead);
      const tbody = document.createElement("tbody");
      (section.rows || []).forEach(row => {
        const tr = document.createElement("tr");
        (section.columns || []).forEach(column => { const td = document.createElement("td"); td.textContent = row[column] == null ? "—" : String(row[column]); tr.append(td); });
        tbody.append(tr);
      });
      table.append(tbody); wrapper.append(table); host.append(wrapper);
    });
    const total = (result.sections || []).reduce((sum, section) => sum + Number(section.row_count || 0), 0);
    summary.textContent = `${total} 列 rows · numerical (input: ${result.input_backend || "—"})`;
  }

  async function run() {
    const payload = buildPayload();
    if (!payload.selection_value_field || !payload.evaluation_field || !payload.features.length || !payload.entity_fields.length) {
      throw new Error("請指定分析個體、選球指標、分群特徵與評估欄位。 Entity, selection metric, features and evaluation field are required.");
    }
    const progress = window.treepoloAnalysisProgress;
    progress?.start?.();
    const response = await fetch("/api/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const body = await response.json();
    progress?.finish?.();
    if (!response.ok) throw new Error(body.error || "Cluster comparison failed");
    render(body);
  }

  function inject() {
    if (document.getElementById(PANEL_ID)) return;
    injectStyles();
    const analysisGroup = Array.from(document.querySelectorAll(".task-group")).find(group => group.querySelector(".task-group-title")?.textContent.includes("進階研究"));
    const navHost = analysisGroup || document.querySelector(".navigation-pane");
    const button = document.createElement("button");
    button.className = "nav-item"; button.dataset.panel = PANEL_ID;
    button.textContent = "多階段分群比較 Cluster Comparison";
    navHost.append(button); button.addEventListener("click", switchPanel);

    const panel = document.createElement("div"); panel.id = PANEL_ID; panel.className = "panel";
    panel.innerHTML = `
      <div class="panel-heading">多階段分群比較 Multi-stage Cluster Comparison</div>
      <div class="panel-body">
        <p class="hint">把武器庫群組選球、每個投手獨立 movement clustering、最佳 cluster 選擇與參考球種比較一次完成。 Designed to complete the full selector → per-entity clustering → best-cluster → reference comparison workflow.</p>
        <fieldset><legend>共同篩選條件 Common Filters</legend><div id="cc-filter-list" class="cc-filter-list"></div><button id="cc-add-filter" type="button">＋ 新增篩選條件 Add Filter</button></fieldset>
        <fieldset><legend>武器庫與候選球種 Arsenal & Candidate Pitch</legend><div class="cc-grid">
          <label>分析個體 Entity Fields<input id="cc-entities" value="pitcher" placeholder="pitcher"></label>
          <label>武器庫最低使用率 Minimum Usage<input id="cc-min-usage" type="number" min="0" max="1" step=".01" value=".05"></label>
          <label>參考球種 Reference Pitch<input id="cc-reference" value="FF"></label>
          <label>候選球種排名欄位 Selection Value<select id="cc-selection-field" class="cc-field"></select></label>
          <label>候選球種統計 Selection Aggregate<select id="cc-selection-function"><option value="avg">平均 Average</option><option value="min">最小 Minimum</option><option value="max">最大 Maximum</option><option value="sum">總和 Sum</option><option value="median">中位數 Median</option></select></label>
          <label>最佳方向 Best Direction<select id="cc-selection-direction"><option value="asc">越低越好 Lower</option><option value="desc">越高越好 Higher</option></select></label>
          <label>並列處理 Tie Handling<select id="cc-tie"><option value="row_number">固定唯一 Deterministic One</option><option value="dense_rank">保留並列 Keep Ties</option></select></label>
        </div></fieldset>
        <fieldset><legend>每個個體獨立分群 Per-Entity Clustering</legend><div class="cc-grid">
          <label>分群特徵 Features<input id="cc-features" value="release_speed,pfx_x,pfx_z,release_spin_rate" placeholder="release_speed,pfx_x,pfx_z"></label>
          <label>方法 Method<select id="cc-method"><option value="kmeans">K-means</option><option value="gmm">Gaussian Mixture</option></select></label>
          <label>每個個體群數 Clusters per Entity<input id="cc-clusters" type="number" min="2" max="50" value="3"></label>
          <label class="checkbox-line"><input id="cc-standardize" type="checkbox" checked> 特徵標準化 Standardize Features</label>
          <label>隨機種子 Random Seed<input id="cc-seed" type="number" value="42"></label>
          <label>最佳 Cluster 評估欄位 Evaluation Field<select id="cc-evaluation-field" class="cc-field"></select></label>
          <label>最佳 Cluster 方向 Best Cluster Direction<select id="cc-evaluation-direction"><option value="asc">越低越好 Lower</option><option value="desc">越高越好 Higher</option></select></label>
          <label>最大輸入列數 Max Input Rows<input id="cc-max-rows" type="number" min="100" max="1000000" value="200000"></label>
        </div><p class="hint">分群會依「分析個體」分開建模，不會把不同投手混成同一組 cluster。 Cluster labels are fitted independently inside each entity.</p>
        <div class="button-row"><button id="cc-run" class="primary" type="button">執行多階段比較 Run Cluster Comparison</button></div></fieldset>
      </div>`;
    document.querySelector(".main-pane")?.insertBefore(panel, document.querySelector("#result-window"));
    document.getElementById("cc-add-filter")?.addEventListener("click", () => addFilter());
    document.getElementById("cc-run")?.addEventListener("click", () => run().catch(error => {
      const summary = document.querySelector("#result-summary"); if (summary) summary.textContent = "錯誤 Error";
      const host = document.querySelector("#result-content"); if (host) host.textContent = error.message;
    }));
    refreshFields();

    document.addEventListener("treepolo:fields-updated", refreshFields);
    const source = document.querySelector("#basic-group");
    if (source) new MutationObserver(refreshFields).observe(source, { childList: true });

    const base = window.treepoloStage4Pages?.applyPayload;
    if (window.treepoloStage4Pages && typeof base === "function") {
      window.treepoloStage4Pages.applyPayload = payload => payload?.mode === MODE ? applyPayload(payload) : base(payload);
    }
    window.treepoloClusterComparePage = { buildPayload, applyPayload, switchPanel };
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", inject, { once: true });
  else inject();
})();