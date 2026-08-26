(() => {
  "use strict";

  const PAGE_SIZE = 200;
  const CUSTOM_STAGES = {
    arsenal_signature: "武器庫簽名 Arsenal Signature",
    pitch_role_select: "相對球種篩選 Relative Pitch Selector",
    pitch_role_annotate: "相對球種標註 Relative Pitch Annotation",
    empirical_percentile: "群內百分位 Empirical Percentile",
    event_pattern_cohorts: "球序群組 Event Pattern Cohorts",
  };
  const COMPARE_OPTIONS = `
    <option value="eq">等於 Equals</option><option value="ne">不等於 Not Equal</option>
    <option value="gt">大於 Greater Than</option><option value="ge">大於等於 At Least</option>
    <option value="lt">小於 Less Than</option><option value="le">小於等於 At Most</option>
    <option value="in">包含於清單 In List</option><option value="not_in">不包含於清單 Not In List</option>
    <option value="is_null">沒有資料 Is Null</option><option value="not_null">有資料 Is Not Null</option>`;

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));
  const csv = value => String(value || "").split(",").map(x => x.trim()).filter(Boolean);
  const typed = value => {
    const text = String(value ?? "").trim();
    if (text === "") return "";
    if (text.toLowerCase() === "true") return true;
    if (text.toLowerCase() === "false") return false;
    const number = Number(text);
    return Number.isFinite(number) ? number : text;
  };
  const orderSpec = value => csv(value).map(token => ({
    field: token.replace(/^[+-]/, ""),
    descending: token.startsWith("-"),
  })).filter(item => item.field);
  const escapeHtml = value => {
    const div = document.createElement("div");
    div.textContent = String(value ?? "");
    return div.innerHTML;
  };

  function injectStyles() {
    if ($("#acceptance-fixes-styles")) return;
    const style = document.createElement("style");
    style.id = "acceptance-fixes-styles";
    style.textContent = `
      .ta-field-combo{width:100%}
      .ta-field-combo.ta-invalid{outline:2px solid #b12828;background:#fff3f3}
      .ta-stage-tools{display:flex;gap:4px;margin-left:auto}
      .ta-stage-tools button{white-space:nowrap}
      .ta-result-limit{margin-top:8px;max-width:280px}
      .ta-table-pager{position:sticky;left:0;display:flex;align-items:center;gap:7px;padding:4px 7px;background:#eef3f8;border-bottom:1px solid #aab5c2;font-size:11px;z-index:3}
      .ta-table-pager button{padding:2px 7px}
      .ta-history-note{padding:12px;line-height:1.55}
      .ta-pipeline-hint{font-size:11px;color:#596674;margin-top:2px}
      .ta-highlight{outline:2px solid #d68b00!important;outline-offset:-2px}
      .s4-stage-head{flex-wrap:wrap}
    `;
    document.head.append(style);
  }

  function baseFieldOptions() {
    const source = $("#basic-group");
    return source ? Array.from(source.options).filter(o => o.value).map(o => ({
      value: o.value,
      label: o.textContent || o.value,
    })) : [];
  }

  function decorateSingleFieldSelect(select) {
    if (!select || select.multiple || select.dataset.taCombo === "1") return;
    select.dataset.taCombo = "1";
    select.hidden = true;
    const input = document.createElement("input");
    input.type = "text";
    input.className = "ta-field-combo";
    input.autocomplete = "off";
    input.placeholder = "輸入欄位名稱搜尋 Type field name";
    const list = document.createElement("datalist");
    list.id = `ta-fields-${select.id || Math.random().toString(36).slice(2)}`;
    input.setAttribute("list", list.id);
    select.insertAdjacentElement("beforebegin", input);
    select.insertAdjacentElement("afterend", list);

    const refresh = () => {
      const options = Array.from(select.options);
      list.innerHTML = "";
      options.filter(option => option.value).forEach(option => {
        const item = document.createElement("option");
        item.value = option.value;
        item.label = option.textContent || option.value;
        list.append(item);
      });
      input.value = select.value || "";
    };
    const commit = () => {
      const text = input.value.trim();
      const options = Array.from(select.options);
      const match = options.find(option =>
        option.value.toLowerCase() === text.toLowerCase() ||
        String(option.textContent || "").toLowerCase() === text.toLowerCase()
      );
      if (match) {
        input.classList.remove("ta-invalid");
        if (select.value !== match.value) {
          select.value = match.value;
          select.dispatchEvent(new Event("change", { bubbles: true }));
        }
      } else if (!text && options.some(option => !option.value)) {
        input.classList.remove("ta-invalid");
        select.value = "";
        select.dispatchEvent(new Event("change", { bubbles: true }));
      } else {
        input.classList.toggle("ta-invalid", Boolean(text));
        select.value = "";
      }
    };
    input.addEventListener("input", commit);
    input.addEventListener("change", commit);
    select.addEventListener("change", () => {
      input.value = select.value || "";
      input.classList.remove("ta-invalid");
    });
    new MutationObserver(refresh).observe(select, { childList: true });
    refresh();
  }

  function pipelineFields(input) {
    const fields = new Map(baseFieldOptions().map(item => [item.value, item.label]));
    const stage = input.closest(".s4-stage");
    const list = stage?.parentElement;
    if (!stage || !list) return fields;
    for (const sibling of Array.from(list.children)) {
      if (sibling === stage) break;
      if (!sibling.classList?.contains("s4-stage")) continue;
      sibling.querySelectorAll(".s4-metric-alias,.s4-alias,.ta-custom-alias,.ta-cohort-alias").forEach(alias => {
        const value = alias.value?.trim();
        if (value) fields.set(value, `前一步輸出 Prior-stage alias · ${value}`);
      });
      const kind = sibling.querySelector(".s4-stage-kind")?.value;
      if (kind === "pitch_role_annotate") {
        const value = sibling.querySelector(".ta-custom-alias")?.value?.trim() || "selected_pitch_type";
        fields.set(value, `相對球種標註 Relative pitch · ${value}`);
      }
      if (kind === "event_pattern_cohorts") {
        const value = sibling.querySelector(".ta-cohort-alias")?.value?.trim() || "pattern_cohort";
        fields.set(value, `球序群組 Event cohort · ${value}`);
      }
    }
    return fields;
  }

  function decorateAdvancedFieldInput(input) {
    if (!input || input.dataset.taFieldAssist === "1") return;
    input.dataset.taFieldAssist = "1";
    const list = document.createElement("datalist");
    list.id = `ta-pipeline-${Math.random().toString(36).slice(2)}`;
    input.setAttribute("list", list.id);
    input.insertAdjacentElement("afterend", list);
    const refresh = () => {
      list.innerHTML = "";
      for (const [value, label] of pipelineFields(input)) {
        const option = document.createElement("option");
        option.value = value;
        option.label = label;
        list.append(option);
      }
    };
    input.addEventListener("focus", refresh);
    input.addEventListener("input", refresh);
    refresh();
  }

  function decorateFieldInputs() {
    $$('select[data-field-select]:not([multiple])').forEach(decorateSingleFieldSelect);
    const selectors = [
      ".s4-groups", ".s4-metric-field", ".s4-metric-cond-field", ".s4-left", ".s4-right-field",
      ".s4-field", ".s4-value-field", ".s4-partition", ".s4-order", ".s4-fields",
      "#s4-cluster-features", "#s4-cluster-ids", "#s4-cluster-partitions",
      "#s4-reg-dependent", "#s4-reg-independent", "#s4-boot-value", "#s4-boot-units",
      "#s4-boot-group", "#cc-entities", "#cc-features",
      ".ta-entity-fields", ".ta-pitch-field", ".ta-value-field", ".ta-percentile-field",
      ".ta-percentile-partition", ".ta-event-field",
    ];
    $$(selectors.join(",")).forEach(decorateAdvancedFieldInput);
  }

  function ensureMetricCompareField(row) {
    const condition = row?.querySelector(".s4-metric-condition");
    if (!condition || condition.querySelector(".ta-metric-cond-value-field")) return;
    const label = document.createElement("label");
    label.innerHTML = '或比較欄位 Or Compare Field<input class="ta-metric-cond-value-field" type="text" placeholder="例如 selected_pitch_type">';
    condition.append(label);
    decorateAdvancedFieldInput(label.querySelector("input"));
  }

  function addCustomOptions(select) {
    if (!select) return;
    for (const [value, label] of Object.entries(CUSTOM_STAGES)) {
      if (Array.from(select.options).some(option => option.value === value)) continue;
      const option = document.createElement("option");
      option.value = value;
      option.textContent = label;
      select.append(option);
    }
  }

  function customStageBody(stage, kind, preset = {}) {
    const body = stage.querySelector(".s4-stage-body");
    if (!body) return;
    const value = (key, fallback = "") => preset[key] ?? fallback;
    const listValue = (key, fallback = []) => (preset[key] || fallback).join(",");
    if (kind === "arsenal_signature") {
      body.innerHTML = `
        <label>實體欄位 Entity Fields<input class="ta-entity-fields" value="${escapeHtml(listValue("entity_fields", ["pitcher"]))}"></label>
        <label>球種欄位 Pitch Field<input class="ta-pitch-field" value="${escapeHtml(value("pitch_field", "pitch_type"))}"></label>
        <label>最低使用率 Minimum Usage<input class="ta-min-usage" type="number" min="0" max="1" step=".01" value="${escapeHtml(value("min_usage", .05))}"></label>
        <label>輸出欄位 Alias<input class="ta-custom-alias" value="${escapeHtml(value("alias", "arsenal"))}"></label>`;
    } else if (kind === "pitch_role_select" || kind === "pitch_role_annotate") {
      body.innerHTML = `
        <label>實體欄位 Entity Fields<input class="ta-entity-fields" value="${escapeHtml(listValue("entity_fields", ["pitcher"]))}"></label>
        <label>球種欄位 Pitch Field<input class="ta-pitch-field" value="${escapeHtml(value("pitch_field", "pitch_type"))}"></label>
        <label>排名依據 Ranking Basis<select class="ta-role-kind"><option value="usage_rate">使用率 Usage Rate</option><option value="field_metric">指定指標 Field Metric</option></select></label>
        <label>指標欄位 Value Field<input class="ta-value-field" value="${escapeHtml(value("value_field", "release_speed"))}"></label>
        <label>統計 Aggregate<select class="ta-role-fn"><option value="avg">Average</option><option value="max">Maximum</option><option value="min">Minimum</option><option value="sum">Sum</option><option value="count">Count</option><option value="median">Median</option></select></label>
        <label>方向 Direction<select class="ta-role-direction"><option value="desc">最高 Highest</option><option value="asc">最低 Lowest</option></select></label>
        <label>排除球種 Exclude Pitch Types<input class="ta-role-exclude" value="${escapeHtml((preset.exclude_pitch_types || []).join(","))}" placeholder="FF,SI"></label>
        <label>最低使用率 Minimum Usage<input class="ta-min-usage" type="number" min="0" max="1" step=".01" value="${escapeHtml(value("min_usage", ""))}" placeholder="選填 Optional"></label>
        <label>順位 Rank<input class="ta-role-rank" type="number" min="1" value="${escapeHtml(value("rank", 1))}"></label>
        <label>並列 Tie<select class="ta-role-tie"><option value="row_number">固定唯一 Deterministic One</option><option value="dense_rank">保留並列 Dense Rank</option><option value="rank">保留跳號 Rank</option></select></label>
        <label>輸出欄位 Alias<input class="ta-custom-alias" value="${escapeHtml(value("alias", kind === "pitch_role_annotate" ? "selected_pitch_type" : "selected_role_rank"))}"></label>`;
      body.querySelector(".ta-role-kind").value = value("metric_kind", "usage_rate");
      body.querySelector(".ta-role-fn").value = value("function", "avg");
      body.querySelector(".ta-role-direction").value = value("direction", "desc");
      body.querySelector(".ta-role-tie").value = value("tie_method", "row_number");
    } else if (kind === "empirical_percentile") {
      body.innerHTML = `
        <label>資料欄位 Field<input class="ta-percentile-field" value="${escapeHtml(value("field", ""))}"></label>
        <label>群內計算 Partition By<input class="ta-percentile-partition" value="${escapeHtml(listValue("partition_by"))}" placeholder="arsenal"></label>
        <label>輸出欄位 Alias<input class="ta-custom-alias" value="${escapeHtml(value("alias", "percentile"))}"></label>`;
    } else if (kind === "event_pattern_cohorts") {
      const event = preset.event || {};
      body.innerHTML = `
        <label>事件欄位 Event Field<input class="ta-event-field" value="${escapeHtml(event.field || value("event_field", "pitch_type"))}"></label>
        <label>事件條件 Comparison<select class="ta-event-op">${COMPARE_OPTIONS}</select></label>
        <label>事件值 Event Value<input class="ta-event-value" value="${escapeHtml(event.value ?? value("event_value", "ST"))}"></label>
        <label>選取第幾次 Occurrence<input class="ta-event-occurrence" type="number" min="1" value="${escapeHtml(value("occurrence", 3))}"></label>
        <label>精確出現次數 Exact Count<input class="ta-event-exact" type="number" min="1" value="${escapeHtml(value("exact_count", 3))}"></label>
        <label>群組排列 Cohort Arrangements<input class="ta-event-arrangements" value="${escapeHtml((preset.arrangements || ["consecutive","none_adjacent"]).join(","))}"></label>
        <label class="checkbox-line"><input class="ta-event-last" type="checkbox"> 最後一球也必須符合 Final Pitch Must Match</label>
        <label>群組欄位 Cohort Alias<input class="ta-cohort-alias" value="${escapeHtml(value("cohort_alias", "pattern_cohort"))}"></label>`;
      body.querySelector(".ta-event-op").value = event.op || value("event_op", "eq");
      body.querySelector(".ta-event-last").checked = Boolean(value("require_last_event", true));
    } else {
      return;
    }
    decorateFieldInputs();
  }

  function ensureStageTools(stage) {
    if (!stage || stage.dataset.taDecorated === "1") return;
    stage.dataset.taDecorated = "1";
    const head = stage.querySelector(".s4-stage-head");
    const select = stage.querySelector(".s4-stage-kind");
    if (!head || !select) return;
    addCustomOptions(select);
    const tools = document.createElement("span");
    tools.className = "ta-stage-tools";
    const make = (text, action) => {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = text;
      button.addEventListener("click", action);
      return button;
    };
    tools.append(
      make("↑", () => {
        const prev = stage.previousElementSibling;
        if (prev?.classList.contains("s4-stage")) stage.parentElement.insertBefore(stage, prev);
      }),
      make("↓", () => {
        const next = stage.nextElementSibling;
        if (next?.classList.contains("s4-stage")) stage.parentElement.insertBefore(next, stage);
      }),
      make("＋ 在此後新增 Add After", () => {
        const list = stage.parentElement;
        const panel = stage.closest(".panel");
        const add = panel?.querySelector(".s4-add-stage");
        if (!list || !add) return;
        const before = new Set(Array.from(list.children));
        add.click();
        const created = Array.from(list.children).find(node => !before.has(node) && node.classList?.contains("s4-stage"));
        if (created) stage.insertAdjacentElement("afterend", created);
      }),
    );
    head.append(tools);
  }

  function decorateStages() {
    $$(".s4-stage-kind").forEach(addCustomOptions);
    $$(".s4-stage").forEach(stage => {
      ensureStageTools(stage);
      stage.querySelectorAll(".s4-metric-row").forEach(ensureMetricCompareField);
      const kind = stage.querySelector(".s4-stage-kind")?.value;
      if (CUSTOM_STAGES[kind] && !stage.querySelector(".ta-custom-alias,.ta-cohort-alias")) customStageBody(stage, kind);
    });
    decorateFieldInputs();
  }

  function serializeMetric(row) {
    const result = {
      function: row.querySelector(".s4-metric-fn")?.value || "count",
      field: row.querySelector(".s4-metric-field")?.value?.trim() || "",
      alias: row.querySelector(".s4-metric-alias")?.value?.trim() || "",
      distinct: Boolean(row.querySelector(".s4-metric-distinct")?.checked),
    };
    const field = row.querySelector(".s4-metric-cond-field")?.value?.trim();
    if (field) {
      const op = row.querySelector(".s4-metric-cond-op")?.value || "eq";
      const compareField = row.querySelector(".ta-metric-cond-value-field")?.value?.trim();
      let value = row.querySelector(".s4-metric-cond-value")?.value ?? "";
      if (["in", "not_in"].includes(op)) value = csv(value);
      result.condition = { field, op };
      if (compareField) result.condition.value_field = compareField;
      else result.condition.value = typed(value);
    }
    return result;
  }

  function serializeStage(stage) {
    const kind = stage.querySelector(".s4-stage-kind")?.value || "";
    const q = selector => stage.querySelector(selector);
    if (kind === "aggregate") return { kind, group_by: csv(q(".s4-groups")?.value), metrics: $$(".s4-metric-row", stage).map(serializeMetric) };
    if (kind === "derive") {
      const result = { kind, alias:q(".s4-alias")?.value.trim(), left:q(".s4-left")?.value.trim(), operator:q(".s4-operator")?.value };
      const rightField = q(".s4-right-field")?.value.trim();
      if (rightField) result.right_field = rightField; else result.right_value = typed(q(".s4-right-value")?.value);
      return result;
    }
    if (kind === "filter") {
      const result = { kind, field:q(".s4-field")?.value.trim(), op:q(".s4-op")?.value, value:typed(q(".s4-value")?.value) };
      const compare = q(".s4-value-field")?.value.trim(); if (compare) result.value_field = compare; return result;
    }
    if (kind === "rolling") return { kind, alias:q(".s4-alias")?.value.trim(), function:q(".s4-function")?.value, field:q(".s4-field")?.value.trim(), partition_by:csv(q(".s4-partition")?.value), order_by:orderSpec(q(".s4-order")?.value), window_size:Number(q(".s4-window")?.value || 3) };
    if (kind === "offset") return { kind, alias:q(".s4-alias")?.value.trim(), field:q(".s4-field")?.value.trim(), direction:q(".s4-direction")?.value, offset:Number(q(".s4-offset")?.value || 1), partition_by:csv(q(".s4-partition")?.value), order_by:orderSpec(q(".s4-order")?.value) };
    if (kind === "trend") return { kind, alias:q(".s4-alias")?.value.trim(), field:q(".s4-field")?.value.trim(), direction:q(".s4-direction")?.value, periods:Number(q(".s4-periods")?.value || 3), partition_by:csv(q(".s4-partition")?.value), order_by:orderSpec(q(".s4-order")?.value), strict:Boolean(q(".s4-strict")?.checked) };
    if (kind === "nth") return { kind, partition_by:csv(q(".s4-partition")?.value), order_by:orderSpec(q(".s4-order")?.value), n:Number(q(".s4-n")?.value || 1), from_end:Boolean(q(".s4-from-end")?.checked) };
    if (kind === "rank") return { kind, alias:q(".s4-alias")?.value.trim(), partition_by:csv(q(".s4-partition")?.value), order_by:orderSpec(q(".s4-order")?.value), method:q(".s4-method")?.value, keep_rank:q(".s4-keep-rank")?.value ? Number(q(".s4-keep-rank")?.value) : null };
    if (kind === "project") return { kind, fields:csv(q(".s4-fields")?.value) };
    if (kind === "sort") return { kind, order_by:orderSpec(q(".s4-order")?.value) };
    if (kind === "arsenal_signature") return { kind, entity_fields:csv(q(".ta-entity-fields")?.value), pitch_field:q(".ta-pitch-field")?.value.trim() || "pitch_type", min_usage:Number(q(".ta-min-usage")?.value || .05), alias:q(".ta-custom-alias")?.value.trim() || "arsenal" };
    if (kind === "pitch_role_select" || kind === "pitch_role_annotate") {
      const result = {
        kind,
        entity_fields:csv(q(".ta-entity-fields")?.value),
        pitch_field:q(".ta-pitch-field")?.value.trim() || "pitch_type",
        metric_kind:q(".ta-role-kind")?.value || "usage_rate",
        value_field:q(".ta-value-field")?.value.trim() || "",
        function:q(".ta-role-fn")?.value || "avg",
        direction:q(".ta-role-direction")?.value || "desc",
        exclude_pitch_types:csv(q(".ta-role-exclude")?.value),
        rank:Number(q(".ta-role-rank")?.value || 1),
        tie_method:q(".ta-role-tie")?.value || "row_number",
        alias:q(".ta-custom-alias")?.value.trim() || (kind === "pitch_role_annotate" ? "selected_pitch_type" : "selected_role_rank"),
      };
      if (q(".ta-min-usage")?.value !== "") result.min_usage = Number(q(".ta-min-usage").value);
      return result;
    }
    if (kind === "empirical_percentile") return { kind, field:q(".ta-percentile-field")?.value.trim(), partition_by:csv(q(".ta-percentile-partition")?.value), alias:q(".ta-custom-alias")?.value.trim() || "percentile" };
    if (kind === "event_pattern_cohorts") {
      const exact = q(".ta-event-exact")?.value;
      return {
        kind,
        event:{ field:q(".ta-event-field")?.value.trim() || "pitch_type", op:q(".ta-event-op")?.value || "eq", value:typed(q(".ta-event-value")?.value) },
        occurrence:Number(q(".ta-event-occurrence")?.value || 1),
        exact_count:exact ? Number(exact) : null,
        require_last_event:Boolean(q(".ta-event-last")?.checked),
        arrangements:csv(q(".ta-event-arrangements")?.value),
        cohort_alias:q(".ta-cohort-alias")?.value.trim() || "pattern_cohort",
      };
    }
    throw new Error(`Unsupported stage: ${kind}`);
  }

  function readS4Filters(panel) {
    return $$(".s4-filter-row", panel).map(row => {
      const op = row.querySelector(".s4-filter-op")?.value || "eq";
      let value = row.querySelector(".s4-filter-value")?.value ?? "";
      if (["in","not_in"].includes(op)) value = csv(value); else value = typed(value);
      return { field:row.querySelector(".s4-filter-field")?.value || "", op, value };
    }).filter(item => item.field);
  }

  function resultLimitForPanel(panel) {
    if (!panel) return 500;
    const basic = panel.querySelector("#basic-limit");
    if (basic) return Number(basic.value || 200);
    const workflow = panel.querySelector("#s4-workflow-limit");
    if (workflow) return Number(workflow.value || 500);
    return Number(panel.querySelector(".ta-result-limit input")?.value || 500);
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

  function buildStage4Payload(mode, panel) {
    const payload = { mode, filters:readS4Filters(panel), result_limit:resultLimitForPanel(panel) };
    const stageList = selector => Array.from(panel.querySelector(selector)?.children || []).filter(node => node.classList?.contains("s4-stage")).map(serializeStage);
    if (mode === "workflow") {
      payload.stages = stageList(".s4-stage-list");
      payload.limit = Number($("#s4-workflow-limit")?.value || 500);
    } else {
      payload.input_stages = stageList(".s4-input-stage-list");
      if (mode === "clustering") Object.assign(payload, {
        features:csv($("#s4-cluster-features")?.value), id_fields:csv($("#s4-cluster-ids")?.value),
        partition_fields:csv($("#s4-cluster-partitions")?.value), method:$("#s4-cluster-method")?.value || "kmeans",
        clusters:Number($("#s4-cluster-k")?.value || 3), standardize:Boolean($("#s4-cluster-standardize")?.checked),
        seed:Number($("#s4-cluster-seed")?.value || 42), max_input_rows:Number($("#s4-cluster-max")?.value || 200000),
        assignment_limit:Number($("#s4-cluster-assign")?.value || 5000),
      });
      if (mode === "regression") Object.assign(payload, {
        dependent:$("#s4-reg-dependent")?.value.trim(), independent:csv($("#s4-reg-independent")?.value),
        model:$("#s4-reg-model")?.value || "linear", confidence:Number($("#s4-reg-confidence")?.value || .95),
        standardize_predictors:Boolean($("#s4-reg-standardize")?.checked), max_input_rows:Number($("#s4-reg-max")?.value || 200000),
      });
      if (mode === "bootstrap") Object.assign(payload, {
        value_field:$("#s4-boot-value")?.value.trim(), resample_unit_fields:csv($("#s4-boot-units")?.value),
        statistic:$("#s4-boot-stat")?.value || "mean", group_field:$("#s4-boot-group")?.value.trim() || null,
        group_a:typed($("#s4-boot-a")?.value), group_b:typed($("#s4-boot-b")?.value),
        success_value:typed($("#s4-boot-success")?.value), iterations:Number($("#s4-boot-iterations")?.value || 2000),
        confidence:Number($("#s4-boot-confidence")?.value || .95), seed:Number($("#s4-boot-seed")?.value || 42),
        max_input_rows:Number($("#s4-boot-max")?.value || 200000),
      });
    }
    return payload;
  }

  async function runStage4(mode, button) {
    const panel = panelForMode(mode);
    if (!panel) return;
    const payload = buildStage4Payload(mode, panel);
    const status = $("#status-message");
    button.disabled = true;
    if (status) status.textContent = "正在執行分析 Running analysis…";
    window.treepoloAnalysisProgress?.start?.();
    try {
      const response = await fetch("/api/analyze", {
        method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(payload),
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body.error || `${response.status} ${response.statusText}`);
      window.treepoloStage4Pages?.renderResult?.(body);
      if (status) status.textContent = "分析完成 Analysis complete";
      $("#result-window")?.scrollIntoView({ behavior:"smooth", block:"nearest" });
    } catch (error) {
      const summary = $("#result-summary"), host = $("#result-content");
      if (summary) summary.textContent = "錯誤 Error";
      if (host) host.innerHTML = `<div class="error-box"><strong>執行失敗 Analysis Failed</strong><div>${escapeHtml(error.message)}</div></div>`;
      if (status) status.textContent = "分析失敗 Analysis failed";
    } finally {
      window.treepoloAnalysisProgress?.finish?.();
      button.disabled = false;
    }
  }

  function ensureResultLimit(panel) {
    if (!panel || panel.id === "data-panel" || panel.id === "analysis-library-panel") return;
    if (panel.querySelector("#basic-limit,#s4-workflow-limit,.ta-result-limit")) return;
    const run = panel.querySelector("[data-run],.s4-run,#cc-run");
    if (!run) return;
    const fieldset = run.closest("fieldset") || panel.querySelector(".panel-body");
    if (!fieldset) return;
    const label = document.createElement("label");
    label.className = "short-label ta-result-limit";
    label.innerHTML = '結果顯示上限 Result Row Limit<input type="number" min="1" max="5000" value="500">';
    const row = run.closest(".button-row");
    if (row) fieldset.insertBefore(label, row); else fieldset.append(label);
  }

  function normalizeLabels() {
    const nav = $('[data-panel="percentile-panel"]');
    if (nav && nav.textContent !== "個別百分位門檻 Individual Percentile Threshold") nav.textContent = "個別百分位門檻 Individual Percentile Threshold";
    const heading = $("#percentile-panel .panel-heading");
    if (heading && heading.textContent !== "個別百分位門檻 Individual Percentile Threshold") heading.textContent = "個別百分位門檻 Individual Percentile Threshold";
    const legend = $("#percentile-panel fieldset legend");
    if (legend && legend.textContent.includes("Per-Entity") && legend.textContent !== "個別百分位門檻 Individual Percentile Threshold") legend.textContent = "個別百分位門檻 Individual Percentile Threshold";

    const gap = $("#follow-gap")?.closest("label");
    if (gap) {
      const textNode = Array.from(gap.childNodes).find(node => node.nodeType === Node.TEXT_NODE);
      if (textNode && textNode.nodeValue !== "往後最多幾球內 Target Within Next N Pitches") textNode.nodeValue = "往後最多幾球內 Target Within Next N Pitches";
    }

    $$("[data-run],.s4-run").forEach(button => { if (button.textContent !== "執行分析 Run Analysis") button.textContent = "執行分析 Run Analysis"; });
    const cluster = $("#cc-run"); if (cluster && cluster.textContent !== "執行分析 Run Analysis") cluster.textContent = "執行分析 Run Analysis";
    $$("#analysis-history-list button,#saved-analysis-list button").forEach(button => {
      if (button.textContent.includes("載入")) {
        if (button.textContent !== "載入設定／結果 Load") button.textContent = "載入設定／結果 Load";
        if (button.title !== "總是恢復分析設定；若快取結果仍存在則一併載入。") button.title = "總是恢復分析設定；若快取結果仍存在則一併載入。";
      }
    });
  }

  function ensureProgressPlacement() {
    const progress = $(".analysis-progress-panel");
    const result = $("#result-window");
    if (progress && result && progress.nextElementSibling !== result) result.insertAdjacentElement("beforebegin", progress);
  }

  function removeDefaultBasicMetric() {
    const host = $("#basic-metrics");
    if (!host || host.dataset.taDefaultCleared === "1") return;
    const rows = $$(".metric-row", host);
    if (rows.length !== 1) return;
    const row = rows[0];
    const fn = row.querySelector(".metric-function")?.value;
    const field = row.querySelector(".metric-field")?.value;
    const distinct = row.querySelector(".metric-distinct")?.checked;
    if (fn === "count" && !field && !distinct) {
      row.remove();
      host.dataset.taDefaultCleared = "1";
    }
  }

  function dedupeCacheBadges() {
    const summary = $("#result-summary");
    if (!summary) return;
    const stageBadge = summary.querySelector(".s4-result-note");
    const cacheBadges = $$(".cache-badge", summary);
    if (stageBadge && cacheBadges.length) cacheBadges.forEach(node => node.remove());
    else cacheBadges.slice(1).forEach(node => node.remove());
  }

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
    if (!tbody) { tbody = document.createElement("tbody"); table.append(tbody); }
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
    previous.type = "button"; previous.textContent = "‹ 上一頁 Prev";
    const next = document.createElement("button");
    next.type = "button"; next.textContent = "下一頁 Next ›";
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
    previous.addEventListener("click", () => { if (page > 0) { page -= 1; update(); } });
    next.addEventListener("click", () => { if ((page + 1) * PAGE_SIZE < rows.length) { page += 1; update(); } });
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

  function installFetchLimiter() {
    if (window.__taFetchLimiterInstalled) return;
    window.__taFetchLimiterInstalled = true;
    const priorFetch = window.fetch.bind(window);
    window.fetch = async function acceptanceFetch(input, init = {}) {
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
      if (!(url.includes("/api/analyze") && method === "POST" && response.ok)) return response;
      try {
        const full = await response.clone().json();
        const paged = initialPage(full);
        setTimeout(() => installPagers(full), 40);
        const headers = new Headers(response.headers);
        headers.delete("content-length");
        return new Response(JSON.stringify(paged), {
          status:response.status, statusText:response.statusText, headers,
        });
      } catch {
        return response;
      }
    };
  }

  async function handleLibraryLoadClick(button) {
    const row = button.closest("tbody tr");
    const host = row?.closest("#analysis-history-list,#saved-analysis-list");
    if (!row || !host) return;
    const rows = $$("tbody tr", host);
    const index = rows.indexOf(row);
    if (index < 0) return;
    try {
      const isHistory = host.id === "analysis-history-list";
      const listUrl = isHistory ? "/api/analysis/history?limit=100" : "/api/analysis/saved";
      const listBody = await (await fetch(listUrl, { cache:"no-store" })).json();
      const items = isHistory ? (listBody.history || []) : (listBody.saved || []);
      const item = items[index];
      if (!item) return;
      const detailUrl = isHistory ? `/api/analysis/history/${item.id}` : `/api/analysis/saved/${item.id}`;
      const detail = await (await fetch(detailUrl, { cache:"no-store" })).json();
      const loaded = detail.item;
      if (loaded && !loaded.result_available) {
        setTimeout(() => {
          const summary = $("#result-summary"), content = $("#result-content");
          if (summary) summary.textContent = "結果未保存 Result Not Stored";
          if (content) content.innerHTML = '<div class="ta-history-note"><strong>分析設定已載入 Analysis settings restored.</strong><br>這筆結果未被保存（例如先前結果過大而未快取）。請直接按「執行分析 Run Analysis」重新計算；不會沿用上一筆結果。</div>';
        }, 120);
      }
    } catch {}
  }

  function wrapStage4ApplyPayload() {
    const pages = window.treepoloStage4Pages;
    if (!pages || pages.__taWrapped || typeof pages.applyPayload !== "function") return;
    const base = pages.applyPayload;
    pages.applyPayload = payload => {
      const ok = base(payload);
      if (payload?.mode && ["workflow","clustering","regression","bootstrap"].includes(payload.mode)) {
        const panel = panelForMode(payload.mode);
        const specs = payload.mode === "workflow" ? (payload.stages || []) : (payload.input_stages || []);
        const list = panel?.querySelector(payload.mode === "workflow" ? ".s4-stage-list" : ".s4-input-stage-list");
        const stages = list ? Array.from(list.children).filter(node => node.classList?.contains("s4-stage")) : [];
        specs.forEach((spec, index) => {
          if (!CUSTOM_STAGES[spec.kind] || !stages[index]) return;
          const select = stages[index].querySelector(".s4-stage-kind");
          addCustomOptions(select); select.value = spec.kind;
          customStageBody(stages[index], spec.kind, spec);
        });
        setTimeout(decorateStages, 0);
      }
      return ok;
    };
    pages.__taWrapped = true;
  }

  function decorateAll() {
    normalizeLabels();
    ensureProgressPlacement();
    $$(".panel").forEach(ensureResultLimit);
    decorateStages();
    decorateFieldInputs();
    dedupeCacheBadges();
  }

  function init() {
    injectStyles();
    installFetchLimiter();
    wrapStage4ApplyPayload();
    setTimeout(removeDefaultBasicMetric, 120);
    decorateAll();

    document.addEventListener("change", event => {
      const select = event.target.closest?.(".s4-stage-kind");
      if (select && CUSTOM_STAGES[select.value]) {
        customStageBody(select.closest(".s4-stage"), select.value, {});
        setTimeout(decorateStages, 0);
      }
    });
    document.addEventListener("click", event => {
      const run = event.target.closest?.(".s4-run");
      if (run) {
        event.preventDefault();
        event.stopImmediatePropagation();
        runStage4(run.dataset.s4Mode, run);
        return;
      }
      const load = event.target.closest?.("#analysis-history-list button,#saved-analysis-list button");
      if (load && load.textContent.includes("載入")) handleLibraryLoadClick(load);
    }, true);

    document.addEventListener("treepolo:fields-updated", () => setTimeout(decorateAll, 0));
    const observer = new MutationObserver(() => {
      decorateAll();
      wrapStage4ApplyPayload();
    });
    observer.observe(document.body, { childList:true, subtree:true });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init, { once:true });
  else init();
})();
