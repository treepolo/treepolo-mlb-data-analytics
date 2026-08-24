(() => {
  "use strict";

  const state = { meta: null, busy: false };

  const FIELD_LABELS = {
    pitch_uid: "逐球識別碼 Pitch ID",
    game_date: "比賽日期 Game Date",
    game_pk: "比賽識別碼 Game ID",
    at_bat_number: "打席序號 Plate Appearance Number",
    pitch_number: "打席內球數 Pitch Number",
    pitcher: "投手 Pitcher",
    batter: "打者 Batter",
    pitch_type: "球種 Pitch Type",
    release_speed: "出手球速 Release Speed",
    release_spin_rate: "旋轉速率 Spin Rate",
    spin_axis: "旋轉軸 Spin Axis",
    pfx_x: "水平位移 Horizontal Movement",
    pfx_z: "垂直位移 Vertical Movement",
    description: "逐球結果 Pitch Description",
    events: "打席結果 Plate Appearance Result",
    zone: "好球區編號 Zone",
    stand: "打者站位 Batter Side",
    p_throws: "投手慣用手 Pitcher Hand",
    balls: "壞球數 Balls",
    strikes: "好球數 Strikes",
    game_year: "球季 Season",
    inning: "局數 Inning",
    inning_topbot: "局上／局下 Inning Half",
    launch_speed: "擊球初速 Exit Velocity",
    launch_angle: "擊球仰角 Launch Angle",
    estimated_ba_using_speedangle: "預期打擊率 Expected Batting Average",
    usage_rate: "使用率 Usage Rate",
    pitch_count: "球數 Pitch Count",
    total_pitch_count: "總球數 Total Pitch Count",
    role_rank: "球種角色順位 Pitch Role Rank",
    arsenal: "武器庫 Arsenal",
    percentile: "百分位 Percentile",
    current_value: "目前數值 Current Value",
    reference_value: "參考數值 Reference Value",
    difference: "差值 Difference",
    unit_value: "分析單位數值 Unit Value",
    baseline_value: "基準數值 Baseline Value",
    row_count: "資料筆數 Row Count",
    between_1: "中間條件是否出現 Between Condition Present",
  };

  const STATUS_LABELS = {
    pitch_rows: "逐球資料筆數 Pitch Rows",
    games: "比賽數 Games",
    duplicate_pitch_uid: "重複逐球識別碼 Duplicate Pitch IDs",
    missing_natural_key: "缺少自然鍵 Missing Natural Keys",
    missing_game_pk: "缺少比賽識別碼 Missing Game IDs",
    missing_at_bat_number: "缺少打席序號 Missing Plate Appearance Numbers",
    missing_pitch_number: "缺少球序號 Missing Pitch Numbers",
    latest_game_date: "最新資料日期 Latest Data Date",
    failed_chunks: "失敗區段 Failed Chunks",
    schema_columns: "資料欄位數 Schema Columns",
    undocumented_columns: "新增／未記錄欄位 New / Undocumented Columns",
    columns_missing_from_latest_snapshot: "最新快照缺少欄位 Columns Missing from Latest Snapshot",
    raw_snapshots: "原始快照數 Raw Snapshots",
    auto_update_enabled: "自動更新 Auto Update",
    database_path: "資料庫位置 Database Path",
  };

  const GRAIN_LABELS = {
    pitch: "逐球 Pitch",
    plate_appearance: "打席 Plate Appearance",
    game: "比賽 Game",
    grouped: "分組結果 Grouped Result",
    scalar: "單一結果 Scalar Result",
    arsenal: "武器庫 Arsenal",
    pitch_role: "球種角色 Pitch Role",
    temporal: "時間序列 Temporal",
    unit: "分析單位 Unit",
    baseline: "基準 Baseline",
    cross_level: "跨層級比較 Cross-Level Comparison",
    arsenal_pitch: "武器庫球種 Arsenal Pitch",
  };

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

  function fieldLabel(name) {
    return FIELD_LABELS[name] || `資料欄位 Data Field · ${name}`;
  }

  function setStatus(message) {
    $("#status-message").textContent = message;
  }

  function setBusy(busy, message = "處理中 Working…") {
    state.busy = busy;
    document.body.classList.toggle("busy", busy);
    $$('button').forEach(button => button.disabled = busy);
    if (busy) setStatus(message);
  }

  async function api(path, options = {}) {
    const init = { headers: { "Content-Type": "application/json" }, ...options };
    const response = await fetch(path, init);
    let body;
    try { body = await response.json(); } catch { body = {}; }
    if (!response.ok) {
      throw new Error(body.error || `${response.status} ${response.statusText}`);
    }
    return body;
  }

  function selectedValues(select) {
    return Array.from(select.selectedOptions).map(option => option.value).filter(Boolean);
  }

  function fillFieldSelect(select) {
    if (!state.meta) return;
    const previous = new Set(selectedValues(select));
    const preselect = (select.dataset.preselect || "").split(",").map(x => x.trim()).filter(Boolean);
    const allowEmpty = select.hasAttribute("data-allow-empty");
    select.innerHTML = "";
    if (allowEmpty) {
      const option = document.createElement("option");
      option.value = "";
      option.textContent = "不指定 None";
      select.append(option);
    }
    for (const field of state.meta.fields) {
      const option = document.createElement("option");
      option.value = field.name;
      option.textContent = `${fieldLabel(field.name)} (${field.name})`;
      if (previous.has(field.name) || (!previous.size && preselect.includes(field.name))) option.selected = true;
      select.append(option);
    }
  }

  function fillAllFieldSelects() {
    $$('[data-field-select]').forEach(fillFieldSelect);
  }

  function cloneCondition(removable = true) {
    const fragment = $("#condition-template").content.cloneNode(true);
    const row = $(".condition-row", fragment);
    if (!removable) $(".remove-row", row).style.display = "none";
    $(".remove-row", row).addEventListener("click", () => row.remove());
    $(".condition-op", row).addEventListener("change", event => {
      const hide = ["is_null", "not_null"].includes(event.target.value);
      $(".condition-value", row).disabled = hide;
    });
    if (state.meta) fillFieldSelect($(".condition-field", row));
    return row;
  }

  function conditionData(row) {
    return {
      field: $(".condition-field", row).value,
      op: $(".condition-op", row).value,
      value: $(".condition-value", row).value,
    };
  }

  function setupFilterBoxes() {
    $$('[data-filter-box]').forEach(host => {
      const template = $("#filter-box-template").content.cloneNode(true);
      const list = $(".filter-list", template);
      $(".add-filter", template).addEventListener("click", () => list.append(cloneCondition(true)));
      host.append(template);
    });
  }

  function filtersFor(name) {
    const host = $(`[data-filter-box="${name}"]`);
    return $$(".condition-row", host).map(conditionData).filter(item => item.field);
  }

  function setupSingleConditions() {
    ["sequence-event", "follow-anchor", "follow-target", "follow-between"].forEach(id => {
      $("#" + id).append(cloneCondition(false));
    });
    const defaults = {
      "sequence-event": ["pitch_type", "eq", "ST"],
      "follow-anchor": ["pitch_type", "eq", "ST"],
      "follow-target": ["pitch_type", "eq", "ST"],
      "follow-between": ["pitch_type", "eq", "FF"],
    };
    for (const [id, values] of Object.entries(defaults)) {
      const row = $(".condition-row", $("#" + id));
      $(".condition-field", row).dataset.pendingDefault = values[0];
      $(".condition-op", row).value = values[1];
      $(".condition-value", row).value = values[2];
    }
  }

  function applyPendingDefaults() {
    $$('[data-pending-default]').forEach(select => {
      if (Array.from(select.options).some(option => option.value === select.dataset.pendingDefault)) {
        select.value = select.dataset.pendingDefault;
      }
      delete select.dataset.pendingDefault;
    });
  }

  function addMetric() {
    const fragment = $("#metric-template").content.cloneNode(true);
    const row = $(".metric-row", fragment);
    $(".remove-row", row).addEventListener("click", () => row.remove());
    if (state.meta) fillFieldSelect($(".metric-field", row));
    $("#basic-metrics").append(row);
  }

  function metricData(row) {
    return {
      function: $(".metric-function", row).value,
      field: $(".metric-field", row).value,
      distinct: $(".metric-distinct", row).checked,
    };
  }

  function setupNavigation() {
    $$(".nav-item").forEach(button => {
      button.addEventListener("click", () => {
        $$(".nav-item").forEach(item => item.classList.remove("active"));
        button.classList.add("active");
        $$(".panel").forEach(panel => panel.classList.remove("active-panel"));
        $("#" + button.dataset.panel).classList.add("active-panel");
      });
    });
  }

  function buildPayload(mode) {
    if (mode === "basic") {
      const sortField = $("#basic-sort").value;
      return {
        mode,
        filters: filtersFor("basic"),
        group_by: selectedValues($("#basic-group")),
        metrics: $$(".metric-row", $("#basic-metrics")).map(metricData),
        sort: sortField ? { field: sortField, descending: $("#basic-sort-direction").value === "desc" } : {},
        limit: Number($("#basic-limit").value || 200),
      };
    }
    if (mode === "sequence_pattern") {
      return {
        mode,
        filters: filtersFor("sequence"),
        event: conditionData($(".condition-row", $("#sequence-event"))),
        occurrence: Number($("#sequence-occurrence").value || 1),
        exact_count: $("#sequence-exact").value ? Number($("#sequence-exact").value) : null,
        arrangement: $("#sequence-arrangement").value,
        require_last_event: $("#sequence-last").checked,
      };
    }
    if (mode === "follow_event") {
      return {
        mode,
        filters: filtersFor("follow"),
        anchor: conditionData($(".condition-row", $("#follow-anchor"))),
        target: conditionData($(".condition-row", $("#follow-target"))),
        between: [conditionData($(".condition-row", $("#follow-between")))],
        max_gap: Number($("#follow-gap").value || 3),
      };
    }
    if (mode === "arsenal") {
      return {
        mode,
        filters: filtersFor("arsenal"),
        entity_fields: selectedValues($("#arsenal-entities")),
        min_usage: Number($("#arsenal-min-usage").value || 0.05),
        tie_method: $("#arsenal-tie").value,
      };
    }
    if (mode === "pitch_role") {
      return {
        mode,
        filters: filtersFor("role"),
        entity_fields: selectedValues($("#role-entities")),
        metric_kind: $("#role-metric-kind").value,
        value_field: $("#role-value-field").value,
        function: $("#role-function").value,
        rank: Number($("#role-rank").value || 1),
        descending: $("#role-direction").value === "desc",
        exclude_pitch_types: $("#role-exclude").value.split(",").map(x => x.trim()).filter(Boolean),
        tie_method: $("#role-tie").value,
      };
    }
    if (mode === "temporal") {
      return {
        mode,
        filters: filtersFor("temporal"),
        entity_fields: selectedValues($("#temporal-entities")),
        period_field: $("#temporal-period").value,
        value_field: $("#temporal-value").value,
        function: $("#temporal-function").value,
        direction: $("#temporal-direction").value,
        offset: Number($("#temporal-offset").value || 1),
      };
    }
    if (mode === "percentile") {
      return {
        mode,
        filters: filtersFor("percentile"),
        entity_fields: selectedValues($("#percentile-entities")),
        value_field: $("#percentile-value").value,
        threshold: Number($("#percentile-threshold").value || 80) / 100,
        side: $("#percentile-side").value,
      };
    }
    if (mode === "cross_level") {
      return {
        mode,
        filters: filtersFor("cross"),
        unit_fields: selectedValues($("#cross-unit")),
        baseline_fields: selectedValues($("#cross-baseline")),
        value_field: $("#cross-value").value,
        function: $("#cross-function").value,
      };
    }
    if (mode === "arsenal_change") {
      return {
        mode,
        filters: filtersFor("arsenal-change"),
        entity_fields: selectedValues($("#change-entities")),
        min_usage: Number($("#change-min-usage").value || 0.05),
        period_a: { start: $("#change-a-start").value, end: $("#change-a-end").value },
        period_b: { start: $("#change-b-start").value, end: $("#change-b-end").value },
      };
    }
    throw new Error(`未知分析模式 Unknown analysis mode: ${mode}`);
  }

  function renderValue(value) {
    if (value === null || value === undefined) return "—";
    if (typeof value === "number" && Number.isFinite(value)) {
      if (Math.abs(value) < 1 && value !== 0) return value.toFixed(4).replace(/0+$/, "").replace(/\.$/, "");
      return Number.isInteger(value) ? String(value) : value.toFixed(3).replace(/0+$/, "").replace(/\.$/, "");
    }
    return String(value);
  }

  function renderTable(result) {
    const wrapper = document.createElement("div");
    const table = document.createElement("table");
    table.className = "result-table";
    const thead = document.createElement("thead");
    const headRow = document.createElement("tr");
    for (const column of result.columns || []) {
      const th = document.createElement("th");
      th.textContent = fieldLabel(column);
      th.title = column;
      headRow.append(th);
    }
    thead.append(headRow);
    table.append(thead);
    const tbody = document.createElement("tbody");
    for (const row of result.rows || []) {
      const tr = document.createElement("tr");
      for (const column of result.columns || []) {
        const td = document.createElement("td");
        td.textContent = renderValue(row[column]);
        tr.append(td);
      }
      tbody.append(tr);
    }
    table.append(tbody);
    wrapper.append(table);
    return wrapper;
  }

  function renderResult(result) {
    const host = $("#result-content");
    host.innerHTML = "";
    if (result.sections) {
      let total = 0;
      for (const section of result.sections) {
        total += section.row_count || 0;
        const title = document.createElement("div");
        title.className = "result-section-title";
        title.textContent = `${section.title} · ${section.row_count || 0} 列 rows`;
        host.append(title, renderTable(section));
      }
      $("#result-summary").textContent = `${total} 列 rows`;
      return;
    }
    const grain = result.grain?.label ? (GRAIN_LABELS[result.grain.label] || `資料層級 Data Level · ${result.grain.label}`) : "—";
    $("#result-summary").textContent = `${result.row_count || 0} 列 rows · ${grain}`;
    if (!(result.rows || []).length) {
      host.innerHTML = '<div class="empty-state">沒有符合條件的資料。 No matching data.</div>';
      return;
    }
    host.append(renderTable(result));
  }

  function renderError(error) {
    $("#result-summary").textContent = "錯誤 Error";
    $("#result-content").innerHTML = `<div class="error-box"><strong>執行失敗 Analysis Failed</strong><br>後端訊息 Backend Message: ${escapeHtml(error.message)}</div>`;
  }

  function escapeHtml(value) {
    const div = document.createElement("div");
    div.textContent = value;
    return div.innerHTML;
  }

  async function runAnalysis(mode) {
    setBusy(true, "正在執行分析 Running analysis…");
    try {
      const result = await api("/api/analyze", { method: "POST", body: JSON.stringify(buildPayload(mode)) });
      renderResult(result);
      setStatus("分析完成 Analysis complete");
      $("#result-window").scrollIntoView({ behavior: "smooth", block: "nearest" });
    } catch (error) {
      renderError(error);
      setStatus("分析失敗 Analysis failed");
    } finally {
      setBusy(false);
    }
  }

  function renderDataStatus(status) {
    const host = $("#data-status");
    host.innerHTML = "";
    const keys = [
      "pitch_rows", "games", "latest_game_date", "failed_chunks", "duplicate_pitch_uid",
      "missing_natural_key", "schema_columns", "raw_snapshots", "auto_update_enabled", "database_path"
    ];
    for (const key of keys) {
      if (!(key in status)) continue;
      const cell = document.createElement("div");
      cell.className = "status-cell";
      const label = document.createElement("div");
      label.className = "status-key";
      label.textContent = STATUS_LABELS[key] || `狀態 Status · ${key}`;
      const value = document.createElement("div");
      value.className = "status-value";
      value.textContent = typeof status[key] === "boolean" ? (status[key] ? "開啟 Enabled" : "關閉 Disabled") : renderValue(status[key]);
      cell.append(label, value);
      host.append(cell);
    }
    $("#auto-update").checked = Boolean(status.auto_update_enabled);
  }

  async function refreshStatus() {
    const status = await api("/api/data/status");
    renderDataStatus(status);
  }

  async function runDataAction(action, payload = {}, busyLabel = "處理資料中 Processing data…") {
    setBusy(true, busyLabel);
    try {
      const result = await api(`/api/data/${action}`, { method: "POST", body: JSON.stringify(payload) });
      setStatus("資料作業完成 Data operation complete");
      await loadMeta();
      await refreshStatus();
      return result;
    } catch (error) {
      renderError(error);
      setStatus("資料作業失敗 Data operation failed");
      throw error;
    } finally {
      setBusy(false);
    }
  }

  async function loadMeta() {
    state.meta = await api("/api/meta");
    $("#db-indicator").textContent = `資料庫 Database: ${state.meta.database}`;
    fillAllFieldSelects();
    applyPendingDefaults();
    if (!state.meta.ready) setStatus("資料庫尚無逐球資料 Pitch data not initialized");
  }

  function bindActions() {
    $("#refresh-meta").addEventListener("click", async () => {
      setBusy(true, "重新整理 Refreshing…");
      try { await loadMeta(); await refreshStatus(); setStatus("已重新整理 Refreshed"); }
      catch (error) { renderError(error); }
      finally { setBusy(false); }
    });
    $("#status-refresh").addEventListener("click", async () => {
      setBusy(true, "讀取狀態 Reading status…");
      try { await refreshStatus(); setStatus("狀態已更新 Status refreshed"); }
      catch (error) { renderError(error); }
      finally { setBusy(false); }
    });
    $("#update-now").addEventListener("click", () => runDataAction("update", {}, "正在更新 Statcast 資料 Updating Statcast data…"));
    $("#auto-update").addEventListener("change", event => runDataAction("auto-update", { enabled: event.target.checked }, "正在更新自動更新設定 Updating Auto Update setting…"));
    $("#run-backfill").addEventListener("click", () => runDataAction("backfill", {
      start: $("#backfill-start").value || null,
      end: $("#backfill-end").value || null,
      resume: $("#backfill-resume").checked,
      fail_fast: $("#backfill-fail-fast").checked,
    }, "正在歷史回補 Running historical backfill…"));
    $("#retry-failed").addEventListener("click", () => runDataAction("retry-failed", {}, "正在重試失敗區段 Retrying failed chunks…"));
    $("#run-rebuild").addEventListener("click", () => runDataAction("rebuild", { confirmation: $("#rebuild-confirmation").value }, "正在重建資料庫 Rebuilding database…"));
    $$('[data-run]').forEach(button => button.addEventListener("click", () => runAnalysis(button.dataset.run)));
    $('[data-add-metric]').addEventListener("click", addMetric);
  }

  function updateClock() {
    $("#status-clock").textContent = new Intl.DateTimeFormat("zh-TW", { dateStyle: "medium", timeStyle: "medium" }).format(new Date());
  }

  async function init() {
    setupNavigation();
    setupFilterBoxes();
    setupSingleConditions();
    addMetric();
    bindActions();
    updateClock();
    setInterval(updateClock, 1000);
    try {
      await loadMeta();
      await refreshStatus();
      setStatus("就緒 Ready");
    } catch (error) {
      renderError(error);
      setStatus("初始化失敗 Initialization failed");
    }
  }

  document.addEventListener("DOMContentLoaded", init);
})();
