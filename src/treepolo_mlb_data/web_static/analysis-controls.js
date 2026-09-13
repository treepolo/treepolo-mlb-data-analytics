(() => {
  "use strict";

  const MODES = {
    basic: "basic-panel",
    sequence_pattern: "sequence-panel",
    follow_event: "follow-panel",
    arsenal: "arsenal-panel",
    pitch_role: "role-panel",
    temporal: "temporal-panel",
    percentile: "percentile-panel",
    cross_level: "cross-panel",
    arsenal_change: "arsenal-change-panel",
  };

  const LABELS = {
    pitch_uid: "逐球識別碼 Pitch ID",
    game_date: "比賽日期 Game Date",
    game_pk: "比賽識別碼 Game ID",
    at_bat_number: "打席序號 Plate Appearance Number",
    pitch_number: "打席內球數 Pitch Number",
    pitcher: "投手 Pitcher",
    batter: "打者 Batter",
    pitch_type: "球種 Pitch Type",
    release_speed: "出手球速 Release Speed",
    description: "逐球結果 Pitch Description",
    zone: "好球區編號 Zone",
    pitch_count: "球數 Pitch Count",
    total_pitch_count: "總球數 Total Pitch Count",
    usage_rate: "使用率 Usage Rate",
    role_rank: "球種角色順位 Pitch Role Rank",
    arsenal: "武器庫 Arsenal",
    role_metric: "球種角色指標 Pitch Role Metric",
    current_value: "目前數值 Current Value",
    reference_value: "參考數值 Reference Value",
    difference: "差值 Difference",
    percentile: "百分位 Percentile",
    unit_value: "分析單位數值 Unit Value",
    baseline_value: "基準數值 Baseline Value",
    between_1: "中間條件是否出現 Between Condition Present",
    row_count: "資料筆數 Row Count",
  };

  const METRIC_LABELS = {
    count: "筆數 Count",
    avg: "平均 Average",
    max: "最大 Maximum",
    min: "最小 Minimum",
    sum: "總和 Sum",
    median: "中位數 Median",
    stddev_pop: "母體標準差 Population SD",
    stddev_samp: "樣本標準差 Sample SD",
  };

  const DEFAULT_PITCH_FIELDS = [
    "pitch_uid", "game_date", "game_pk", "at_bat_number", "pitch_number",
    "pitcher", "batter", "pitch_type", "release_speed", "description", "zone",
  ];

  function multiValues(selector) {
    const control = document.querySelector(selector);
    if (!control) return [];
    if (window.treepoloMultiField?.isControl?.(control)) return window.treepoloMultiField.values(control);
    return String(control.value || "").split(",").map(item => item.trim()).filter(Boolean);
  }

  function optionLabel(field) {
    return window.treepoloFieldCatalog?.label?.(field) || LABELS[field] || field;
  }

  function basicMetricOutputs() {
    const used = new Set(multiValues("#basic-group"));
    const outputs = [];
    document.querySelectorAll("#basic-metrics .metric-row").forEach(row => {
      const fn = row.querySelector(".metric-function")?.value || "count";
      const field = row.querySelector(".metric-field")?.value || "";
      const hasExpression = fn !== "count" || Boolean(field);
      const base = hasExpression ? `${fn}_${field}` : "row_count";
      let alias = base;
      let suffix = 2;
      while (used.has(alias)) alias = `${base}_${suffix++}`;
      used.add(alias);
      const fieldText = field ? ` · ${optionLabel(field)}` : "";
      outputs.push([alias, `${METRIC_LABELS[fn] || fn}${fieldText}`]);
    });
    return outputs;
  }

  function allSchemaFields() {
    return (window.treepoloFieldCatalog?.fields?.() || []).map(field => field.name).filter(Boolean);
  }

  function outputFields(mode) {
    if (mode === "basic") {
      const groups = multiValues("#basic-group");
      const metrics = basicMetricOutputs();
      if (!groups.length && !metrics.length) return allSchemaFields().map(field => [field, optionLabel(field)]);
      return [...groups.map(field => [field, optionLabel(field)]), ...metrics];
    }
    if (mode === "sequence_pattern") return DEFAULT_PITCH_FIELDS.map(f => [f, optionLabel(f)]);
    if (mode === "follow_event") return [...DEFAULT_PITCH_FIELDS, "between_1"].map(f => [f, optionLabel(f)]);
    if (mode === "arsenal") {
      return [...multiValues("#arsenal-entities"), "pitch_type", "pitch_count", "total_pitch_count", "usage_rate", "role_rank", "arsenal"].map(f => [f, optionLabel(f)]);
    }
    if (mode === "pitch_role") {
      const fields = [...multiValues("#role-entities"), "pitch_type"];
      if (document.querySelector("#role-metric-kind")?.value === "usage_rate") {
        fields.push("pitch_count", "total_pitch_count", "usage_rate", "role_rank");
      } else {
        fields.push("role_metric", "role_rank");
      }
      return fields.map(f => [f, optionLabel(f)]);
    }
    if (mode === "temporal") {
      const period = document.querySelector("#temporal-period")?.value;
      const fields = [...multiValues("#temporal-entities")];
      if (period && !fields.includes(period)) fields.push(period);
      fields.push("current_value", "reference_value", "difference");
      return fields.map(f => [f, optionLabel(f)]);
    }
    if (mode === "percentile") return [...DEFAULT_PITCH_FIELDS, "percentile"].map(f => [f, optionLabel(f)]);
    if (mode === "cross_level") return [...multiValues("#cross-unit"), "unit_value", "baseline_value", "difference"].map(f => [f, optionLabel(f)]);
    if (mode === "arsenal_change") return [...multiValues("#change-entities"), "pitch_type"].map(f => [f, optionLabel(f)]);
    return [];
  }

  function addSortRow(mode, initial = {}) {
    const host = document.querySelector(`[data-result-sort-list="${mode}"]`);
    if (!host) return;
    const row = document.createElement("div");
    row.className = "result-sort-row";
    row.innerHTML = `
      <select class="result-sort-field"></select>
      <select class="result-sort-direction">
        <option value="asc">由小到大 Ascending</option>
        <option value="desc">由大到小 Descending</option>
      </select>
      <button type="button" class="remove-row" title="移除 Remove">×</button>`;
    row.querySelector(".result-sort-direction").value = initial.descending ? "desc" : "asc";
    row.querySelector(".remove-row").addEventListener("click", () => row.remove());
    host.append(row);
    refreshMode(mode);
    if (initial.field) row.querySelector(".result-sort-field").value = initial.field;
  }

  function refreshMode(mode) {
    const fields = outputFields(mode);
    document.querySelectorAll(`[data-result-sort-list="${mode}"] .result-sort-field`).forEach(select => {
      const previous = select.value;
      select.innerHTML = '<option value="">不指定 None</option>';
      fields.forEach(([field, label]) => {
        const option = document.createElement("option");
        option.value = field;
        option.textContent = `${label} (${field})`;
        select.append(option);
      });
      if (Array.from(select.options).some(option => option.value === previous)) select.value = previous;
    });
  }

  function inject(mode, panelId) {
    const panel = document.getElementById(panelId);
    if (!panel || panel.querySelector(`[data-result-ordering="${mode}"]`)) return;
    const runButton = panel.querySelector(`[data-run="${mode}"]`);
    const buttonRow = runButton?.closest(".button-row");
    if (!buttonRow) return;

    const box = document.createElement("div");
    box.className = "result-ordering";
    box.dataset.resultOrdering = mode;
    box.innerHTML = `
      <div class="subheading">結果排序 Result Ordering</div>
      <div class="repeat-list result-sort-list" data-result-sort-list="${mode}"></div>
      <button type="button" class="add-result-sort">＋ 新增排序 Add Sort Key</button>
      <p class="hint">可依輸出欄位或計算結果排序；多個排序條件會依上到下依序套用。 Sort by output fields or computed results; multiple keys apply top to bottom.</p>`;
    box.querySelector(".add-result-sort").addEventListener("click", () => addSortRow(mode));
    buttonRow.insertAdjacentElement("beforebegin", box);
  }

  function read(mode) {
    return Array.from(document.querySelectorAll(`[data-result-sort-list="${mode}"] .result-sort-row`))
      .map(row => ({
        field: row.querySelector(".result-sort-field")?.value || "",
        descending: row.querySelector(".result-sort-direction")?.value === "desc",
      }))
      .filter(item => item.field);
  }

  function refreshAll() {
    Object.keys(MODES).forEach(refreshMode);
  }

  function init() {
    Object.entries(MODES).forEach(([mode, panelId]) => inject(mode, panelId));

    ["basic-sort", "basic-sort-direction"].forEach(id => {
      const control = document.getElementById(id);
      const label = control?.closest("label");
      if (label) label.remove();
    });

    document.body.addEventListener("change", event => {
      if (event.target.matches("[data-multi-field], [data-field-select], .metric-function, .metric-field, #role-metric-kind")) refreshAll();
    });
    document.addEventListener("treepolo:fields-updated", refreshAll);
    document.addEventListener("treepolo:analysis-options-changed", refreshAll);
    refreshAll();
  }

  window.treepoloResultOrdering = { read, refresh: refreshAll };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init, { once: true });
  else init();
})();
