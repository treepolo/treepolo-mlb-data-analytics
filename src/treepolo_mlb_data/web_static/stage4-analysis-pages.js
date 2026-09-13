(() => {
  "use strict";

  const MODE_LABELS = {
    workflow: "研究工作流 Research Workflow",
    clustering: "自動分群 Clustering",
    regression: "迴歸分析 Regression",
    bootstrap: "Bootstrap / 信賴區間 Confidence Interval",
  };
  const PANEL_IDS = {
    workflow: "workflow-panel", clustering: "clustering-panel",
    regression: "regression-panel", bootstrap: "bootstrap-panel",
  };
  const AGG_OPTIONS = `
    <option value="count">筆數 Count</option><option value="avg">平均 Average</option>
    <option value="sum">總和 Sum</option><option value="min">最小 Minimum</option><option value="max">最大 Maximum</option>
    <option value="median">中位數 Median</option><option value="stddev_pop">母體標準差 Population SD</option><option value="stddev_samp">樣本標準差 Sample SD</option>`;
  const COMPARE_OPTIONS = `
    <option value="eq">等於 Equals</option><option value="ne">不等於 Not Equal</option>
    <option value="gt">大於 Greater Than</option><option value="ge">大於等於 At Least</option>
    <option value="lt">小於 Less Than</option><option value="le">小於等於 At Most</option>
    <option value="in">包含於清單 In List</option><option value="not_in">不包含於清單 Not In List</option>
    <option value="is_null">沒有資料 Is Null</option><option value="not_null">有資料 Is Not Null</option>`;
  const STAGE_OPTIONS = `
    <option value="aggregate">彙總／分組 Aggregate</option>
    <option value="derive">建立衍生欄位 Derived Field</option>
    <option value="filter">篩選目前結果 Filter Current Result</option>
    <option value="rolling">移動視窗統計 Rolling Window</option>
    <option value="offset">前／後期欄位 Lag / Lead</option>
    <option value="trend">連續期趨勢 Consecutive Trend</option>
    <option value="nth">選取第 N 筆／首尾 Nth / First / Last</option>
    <option value="rank">群內順位 Within-group Rank</option>
    <option value="project">保留欄位 Keep Fields</option>
    <option value="sort">中間結果排序 Sort</option>`;

  const csv = value => window.treepoloMultiField?.parse?.(value) || String(value || "").split(",").map(item => item.trim()).filter(Boolean);
  function numberValue(value, fallback) { const n = Number(value); return Number.isFinite(n) ? n : fallback; }
  function typedValue(value) {
    const text = String(value ?? "").trim();
    if (text === "") return "";
    if (text.toLowerCase() === "true") return true;
    if (text.toLowerCase() === "false") return false;
    const n = Number(text); return Number.isFinite(n) ? n : text;
  }
  function orderSpec(value) {
    return csv(value).map(token => {
      if (token.startsWith("-")) return { field: token.slice(1), descending:true };
      if (token.startsWith("+")) return { field: token.slice(1), descending:false };
      return { field:token, descending:false };
    }).filter(item => item.field);
  }
  function escapeHtml(value) { const div=document.createElement("div"); div.textContent=String(value ?? ""); return div.innerHTML; }
  function escapeAttr(value) { return escapeHtml(value).replace(/"/g, "&quot;"); }

  function injectStyles() {
    if (document.getElementById("stage4-analysis-page-styles")) return;
    const style = document.createElement("style");
    style.id = "stage4-analysis-page-styles";
    style.textContent = `
      .s4-grid { display:grid; grid-template-columns:repeat(3,minmax(180px,1fr)); gap:8px 12px; }
      .s4-grid.two { grid-template-columns:repeat(2,minmax(220px,1fr)); }
      .s4-grid label { display:flex; flex-direction:column; gap:3px; }
      .s4-stage-list,.s4-filter-list,.s4-metric-list { display:flex; flex-direction:column; gap:7px; }
      .s4-stage { border:1px solid #adb8c7; background:#f7f8fa; padding:7px; }
      .s4-stage-head { display:flex; gap:7px; align-items:center; margin-bottom:7px; }
      .s4-stage-head select { flex:1; }
      .s4-stage-body { display:grid; grid-template-columns:repeat(3,minmax(150px,1fr)); gap:6px 8px; }
      .s4-stage-body label { display:flex; flex-direction:column; gap:2px; font-size:12px; }
      .s4-metric-row { display:grid; grid-template-columns:130px 1fr 1fr auto; gap:6px; align-items:end; border-top:1px dotted #bbc3ce; padding-top:6px; }
      .s4-metric-condition { grid-column:1 / -1; display:grid; grid-template-columns:1fr 150px 1fr; gap:6px; }
      .s4-filter-row { display:grid; grid-template-columns:minmax(180px,1fr) 180px minmax(150px,1fr) auto; gap:7px; }
      .s4-prep { margin-top:10px; }
      .s4-run-note { margin-top:7px; color:#505b68; font-size:12px; }
      .s4-result-note { margin-left:8px; font-size:11px; padding:1px 5px; border:1px solid #8098b5; background:#eef5ff; }
      @media (max-width:900px) { .s4-grid,.s4-grid.two,.s4-stage-body { grid-template-columns:1fr; } .s4-filter-row { grid-template-columns:1fr; } }
    `;
    document.head.append(style);
  }

  function fieldOptionsHtml(includeEmpty = true) {
    const fields = window.treepoloFieldCatalog?.fields?.() || [];
    const options = fields.map(field => {
      const name = field.name;
      const label = window.treepoloFieldCatalog?.label?.(name) || name;
      return `<option value="${escapeAttr(name)}">${escapeHtml(label)} (${escapeHtml(name)})</option>`;
    }).join("");
    return (includeEmpty ? '<option value="">不指定 None</option>' : "") + options;
  }

  function refreshFieldSelects() {
    const html = fieldOptionsHtml(true);
    document.querySelectorAll(".s4-field-select").forEach(select => {
      const old = select.value;
      select.innerHTML = html;
      if (Array.from(select.options).some(option => option.value === old)) select.value = old;
    });
  }

  function filterBoxHtml() {
    return `<fieldset class="filter-box s4-filter-box"><legend>共同篩選條件 Common Filters</legend>
      <div class="s4-filter-list"></div><button type="button" class="s4-add-filter">＋ 新增篩選條件 Add Filter</button></fieldset>`;
  }
  function addFilter(box, preset = {}) {
    const list = box.querySelector(".s4-filter-list");
    const row = document.createElement("div"); row.className = "s4-filter-row";
    row.innerHTML = `<select class="s4-filter-field s4-field-select"></select><select class="s4-filter-op">${COMPARE_OPTIONS}</select>
      <input class="s4-filter-value" type="text" placeholder="數值 Value"><button type="button">×</button>`;
    list.append(row); refreshFieldSelects();
    row.querySelector(".s4-filter-field").value = preset.field || "";
    row.querySelector(".s4-filter-op").value = preset.op || "eq";
    row.querySelector(".s4-filter-value").value = Array.isArray(preset.value) ? preset.value.join(",") : (preset.value ?? "");
    row.querySelector("button").addEventListener("click", () => row.remove());
  }
  function filtersFrom(panel) {
    return Array.from(panel.querySelectorAll(".s4-filter-row")).map(row => {
      const op = row.querySelector(".s4-filter-op").value;
      let value = row.querySelector(".s4-filter-value").value;
      if (["in", "not_in"].includes(op)) value = csv(value);
      return { field:row.querySelector(".s4-filter-field").value, op, value };
    }).filter(item => item.field);
  }

  function stageListHtml(className = "s4-stage-list") {
    return `<div class="${className}"></div><button type="button" class="s4-add-stage">＋ 新增分析步驟 Add Stage</button>
      <p class="hint">欄位可使用前一步產生的別名。排序欄位以逗號分隔；前綴「-」代表降冪，例如 pitcher,-usage_rate。 Fields may reference aliases created by earlier stages.</p>`;
  }

  function addMetric(stage, preset = {}) {
    const list = stage.querySelector(".s4-metric-list");
    const row = document.createElement("div"); row.className = "s4-metric-row";
    row.innerHTML = `<label>統計 Aggregate<select class="s4-metric-fn">${AGG_OPTIONS}</select></label>
      <label>欄位 Field<input class="s4-metric-field" type="text" placeholder="Count 可留空"></label>
      <label>結果名稱 Alias<input class="s4-metric-alias" type="text" placeholder="例如 target_count"></label>
      <label class="checkbox-line mini"><input class="s4-metric-distinct" type="checkbox"> Distinct</label>
      <div class="s4-metric-condition">
        <label>條件欄位（選填） Conditional Field<input class="s4-metric-cond-field" type="text" placeholder="例如 pitch_type"></label>
        <label>條件 Comparison<select class="s4-metric-cond-op">${COMPARE_OPTIONS}</select></label>
        <label>條件值 Value<input class="s4-metric-cond-value" type="text" placeholder="例如 FF"></label>
      </div><button type="button" class="remove-row">移除此指標 Remove Metric</button>`;
    list.append(row);
    row.querySelector(".s4-metric-fn").value = preset.function || "count";
    row.querySelector(".s4-metric-field").value = preset.field || "";
    row.querySelector(".s4-metric-alias").value = preset.alias || "";
    row.querySelector(".s4-metric-distinct").checked = Boolean(preset.distinct);
    if (preset.condition) {
      row.querySelector(".s4-metric-cond-field").value = preset.condition.field || "";
      row.querySelector(".s4-metric-cond-op").value = preset.condition.op || "eq";
      row.querySelector(".s4-metric-cond-value").value = preset.condition.value ?? "";
    }
    row.querySelector(".remove-row").addEventListener("click", () => row.remove());
  }

  function setStageValues(body, preset) {
    const mapping = {
      ".s4-alias":"alias", ".s4-left":"left", ".s4-operator":"operator", ".s4-right-field":"right_field", ".s4-right-value":"right_value",
      ".s4-field":"field", ".s4-op":"op", ".s4-value":"value", ".s4-value-field":"value_field", ".s4-function":"function",
      ".s4-partition":"partition_by", ".s4-order":"order_by", ".s4-window":"window_size", ".s4-direction":"direction", ".s4-offset":"offset",
      ".s4-periods":"periods", ".s4-n":"n", ".s4-method":"method", ".s4-keep-rank":"keep_rank", ".s4-fields":"fields",
    };
    Object.entries(mapping).forEach(([selector,key]) => {
      const el = body.querySelector(selector); if (!el || preset[key] == null) return;
      if (key === "partition_by" || key === "fields") el.value = (preset[key] || []).join(",");
      else if (key === "order_by") el.value = (preset[key] || []).map(item => `${item.descending ? "-" : ""}${item.field}`).join(",");
      else el.value = preset[key];
    });
    if (body.querySelector(".s4-strict")) body.querySelector(".s4-strict").checked = preset.strict !== false;
    if (body.querySelector(".s4-from-end")) body.querySelector(".s4-from-end").checked = Boolean(preset.from_end);
  }

  function stageBody(stage, kind, preset = {}) {
    const body = stage.querySelector(".s4-stage-body"); body.innerHTML = "";
    if (kind === "aggregate") {
      body.innerHTML = `<label>分組欄位 Group By<input class="s4-groups" data-multi-field type="text" placeholder="pitcher,game_pk"></label>
        <div style="grid-column:1/-1"><div class="subheading">統計指標 Metrics</div><div class="s4-metric-list"></div><button type="button" class="s4-add-metric">＋ 新增指標 Add Metric</button></div>`;
      body.querySelector(".s4-groups").value = (preset.group_by || []).join(",");
      body.querySelector(".s4-add-metric").addEventListener("click", () => addMetric(stage));
      (preset.metrics?.length ? preset.metrics : [{ function:"count", alias:"row_count" }]).forEach(metric => addMetric(stage, metric));
    } else if (kind === "derive") {
      body.innerHTML = `<label>新欄位名稱 Alias<input class="s4-alias" type="text" placeholder="usage_rate"></label>
        <label>左側欄位 Left Field<input class="s4-left" type="text" placeholder="target_count"></label>
        <label>運算 Operator<select class="s4-operator"><option>/</option><option>*</option><option>+</option><option>-</option><option>%</option></select></label>
        <label>右側欄位 Right Field<input class="s4-right-field" type="text" placeholder="total_count"></label>
        <label>或固定值 Or Constant<input class="s4-right-value" type="text" placeholder="留空時使用右側欄位"></label>`;
      setStageValues(body,preset);
    } else if (kind === "filter") {
      body.innerHTML = `<label>目前欄位 Field<input class="s4-field" type="text"></label><label>條件 Comparison<select class="s4-op">${COMPARE_OPTIONS}</select></label>
        <label>固定值 Value<input class="s4-value" type="text"></label><label>或另一欄位 Or Compare Field<input class="s4-value-field" type="text"></label>`;
      setStageValues(body,preset);
    } else if (kind === "rolling") {
      body.innerHTML = `<label>新欄位名稱 Alias<input class="s4-alias" type="text" placeholder="rolling_avg"></label><label>統計 Aggregate<select class="s4-function"><option value="avg">Average</option><option value="sum">Sum</option><option value="count">Count</option><option value="min">Min</option><option value="max">Max</option></select></label>
        <label>資料欄位 Field<input class="s4-field" type="text"></label><label>各自計算 Partition By<input class="s4-partition" data-multi-field type="text" placeholder="pitcher"></label>
        <label>時間／順序 Order By<input class="s4-order" type="text" placeholder="game_pk"></label><label>視窗筆數 Window Size<input class="s4-window" type="number" min="1" value="3"></label>`;
      setStageValues(body,preset);
    } else if (kind === "offset") {
      body.innerHTML = `<label>新欄位名稱 Alias<input class="s4-alias" type="text"></label><label>資料欄位 Field<input class="s4-field" type="text"></label>
        <label>方向 Direction<select class="s4-direction"><option value="lag">前一期 Lag</option><option value="lead">後一期 Lead</option></select></label>
        <label>間隔 Offset<input class="s4-offset" type="number" min="1" value="1"></label><label>各自計算 Partition By<input class="s4-partition" data-multi-field type="text"></label><label>順序 Order By<input class="s4-order" type="text"></label>`;
      setStageValues(body,preset);
    } else if (kind === "trend") {
      body.innerHTML = `<label>結果欄位 Alias<input class="s4-alias" type="text" placeholder="usage_rising"></label><label>資料欄位 Field<input class="s4-field" type="text"></label>
        <label>方向 Direction<select class="s4-direction"><option value="up">連續上升 Rising</option><option value="down">連續下降 Falling</option></select></label>
        <label>連續期數 Consecutive Values<input class="s4-periods" type="number" min="2" value="3"></label><label>各自計算 Partition By<input class="s4-partition" data-multi-field type="text"></label><label>順序 Order By<input class="s4-order" type="text"></label>
        <label class="checkbox-line"><input class="s4-strict" type="checkbox" checked> 必須嚴格上升／下降 Strict</label>`;
      setStageValues(body,preset);
    } else if (kind === "nth") {
      body.innerHTML = `<label>各自選取 Partition By<input class="s4-partition" data-multi-field type="text"></label><label>順序 Order By<input class="s4-order" type="text"></label>
        <label>第 N 筆 N<input class="s4-n" type="number" min="1" value="1"></label><label class="checkbox-line"><input class="s4-from-end" type="checkbox"> 從尾端倒數 Count From End</label>`;
      setStageValues(body,preset);
    } else if (kind === "rank") {
      body.innerHTML = `<label>順位欄位 Alias<input class="s4-alias" type="text" value="rank"></label><label>各自排名 Partition By<input class="s4-partition" data-multi-field type="text"></label>
        <label>排名依據 Order By<input class="s4-order" type="text"></label><label>並列方法 Method<select class="s4-method"><option value="row_number">固定唯一順位 Row Number</option><option value="dense_rank">保留並列 Dense Rank</option><option value="rank">保留跳號 Rank</option></select></label>
        <label>只保留順位（選填） Keep Rank<input class="s4-keep-rank" type="number" min="1"></label>`;
      setStageValues(body,preset);
    } else if (kind === "project") {
      body.innerHTML = `<label style="grid-column:1/-1">保留欄位 Keep Fields<input class="s4-fields" data-multi-field type="text" placeholder="pitcher,game_pk,usage_rate"></label>`;
      setStageValues(body,preset);
    } else if (kind === "sort") {
      body.innerHTML = `<label style="grid-column:1/-1">排序欄位 Order By<input class="s4-order" type="text" placeholder="pitcher,-usage_rate"></label>`;
      setStageValues(body,preset);
    }
  }

  function addStage(list,preset={}) {
    const stage=document.createElement("div"); stage.className="s4-stage";
    stage.innerHTML=`<div class="s4-stage-head"><select class="s4-stage-kind">${STAGE_OPTIONS}</select><button type="button">× 移除 Remove</button></div><div class="s4-stage-body"></div>`;
    list.append(stage);
    const kind=preset.kind||"aggregate"; stage.querySelector(".s4-stage-kind").value=kind; stageBody(stage,kind,preset);
    stage.querySelector(".s4-stage-kind").addEventListener("change",event=>stageBody(stage,event.target.value,{}));
    stage.querySelector(".s4-stage-head button").addEventListener("click",()=>stage.remove());
  }

  function metricFrom(row) {
    const functionName=row.querySelector(".s4-metric-fn").value;
    const field=row.querySelector(".s4-metric-field").value.trim();
    const alias=row.querySelector(".s4-metric-alias").value.trim();
    const result={function:functionName,field,alias,distinct:row.querySelector(".s4-metric-distinct").checked};
    const conditionField=row.querySelector(".s4-metric-cond-field").value.trim();
    if(conditionField){const op=row.querySelector(".s4-metric-cond-op").value;let value=row.querySelector(".s4-metric-cond-value").value;if(["in","not_in"].includes(op))value=csv(value);result.condition={field:conditionField,op,value};}
    return result;
  }

  function stageFrom(stage) {
    const kind=stage.querySelector(".s4-stage-kind").value; const q=selector=>stage.querySelector(selector);
    if(kind==="aggregate")return{kind,group_by:csv(q(".s4-groups").value),metrics:Array.from(stage.querySelectorAll(".s4-metric-row")).map(metricFrom)};
    if(kind==="derive"){const result={kind,alias:q(".s4-alias").value.trim(),left:q(".s4-left").value.trim(),operator:q(".s4-operator").value};const rightField=q(".s4-right-field").value.trim();if(rightField)result.right_field=rightField;else result.right_value=typedValue(q(".s4-right-value").value);return result;}
    if(kind==="filter"){const result={kind,field:q(".s4-field").value.trim(),op:q(".s4-op").value,value:typedValue(q(".s4-value").value)};const compare=q(".s4-value-field").value.trim();if(compare)result.value_field=compare;return result;}
    if(kind==="rolling")return{kind,alias:q(".s4-alias").value.trim(),function:q(".s4-function").value,field:q(".s4-field").value.trim(),partition_by:csv(q(".s4-partition").value),order_by:orderSpec(q(".s4-order").value),window_size:numberValue(q(".s4-window").value,3)};
    if(kind==="offset")return{kind,alias:q(".s4-alias").value.trim(),field:q(".s4-field").value.trim(),direction:q(".s4-direction").value,offset:numberValue(q(".s4-offset").value,1),partition_by:csv(q(".s4-partition").value),order_by:orderSpec(q(".s4-order").value)};
    if(kind==="trend")return{kind,alias:q(".s4-alias").value.trim(),field:q(".s4-field").value.trim(),direction:q(".s4-direction").value,periods:numberValue(q(".s4-periods").value,3),partition_by:csv(q(".s4-partition").value),order_by:orderSpec(q(".s4-order").value),strict:q(".s4-strict").checked};
    if(kind==="nth")return{kind,partition_by:csv(q(".s4-partition").value),order_by:orderSpec(q(".s4-order").value),n:numberValue(q(".s4-n").value,1),from_end:q(".s4-from-end").checked};
    if(kind==="rank")return{kind,alias:q(".s4-alias").value.trim(),partition_by:csv(q(".s4-partition").value),order_by:orderSpec(q(".s4-order").value),method:q(".s4-method").value,keep_rank:q(".s4-keep-rank").value?Number(q(".s4-keep-rank").value):null};
    if(kind==="project")return{kind,fields:csv(q(".s4-fields").value)};
    if(kind==="sort")return{kind,order_by:orderSpec(q(".s4-order").value)};
    throw new Error(`Unsupported stage: ${kind}`);
  }

  function stagesFrom(panel,selector=".s4-stage-list") { return Array.from(panel.querySelector(selector).querySelectorAll(":scope > .s4-stage")).map(stageFrom); }
  function wirePanelBasics(panel) {
    panel.querySelectorAll(".s4-filter-box").forEach(box=>box.querySelector(".s4-add-filter").addEventListener("click",()=>addFilter(box)));
    panel.querySelectorAll(".s4-add-stage").forEach(button=>{const list=button.previousElementSibling;button.addEventListener("click",()=>addStage(list));});
  }

  function workflowPanelHtml() {
    return `<div id="workflow-panel" class="panel"><div class="panel-heading">研究工作流 Research Workflow</div><div class="panel-body">
      ${filterBoxHtml()}<fieldset><legend>分析流程 Analysis Pipeline</legend>${stageListHtml()}
      <label class="short-label">最多回傳列數 Row Limit<input id="s4-workflow-limit" type="number" min="1" max="5000" value="500"></label>
      <p class="s4-run-note">每一步的輸出會成為下一步輸入，可直接組合「分組 → 條件式指標 → 比例 → 連續趨勢 → 下一期 → 篩選」等完整研究問題。</p>
      <div class="button-row"><button class="primary s4-run" data-s4-mode="workflow">執行研究 Run Workflow</button></div></fieldset></div></div>`;
  }
  function prepHtml() {
    return `<fieldset class="s4-prep"><legend>輸入前處理 Input Preparation（選填 Optional）</legend>${stageListHtml("s4-stage-list s4-input-stage-list")}
      <p class="hint">可先依投手／比賽彙總、建立比例、選順位或套用連續趨勢，再送入數值分析。 Numerical analysis consumes the typed result of these stages.</p></fieldset>`;
  }
  function clusteringPanelHtml() {
    return `<div id="clustering-panel" class="panel"><div class="panel-heading">自動分群 Clustering</div><div class="panel-body">${filterBoxHtml()}${prepHtml()}
      <fieldset><legend>分群設定 Clustering Setup</legend><div class="s4-grid">
        <label>特徵欄位 Features<input id="s4-cluster-features" data-multi-field type="text" value="release_speed,pfx_x,pfx_z,release_spin_rate"></label>
        <label>識別欄位 ID Fields<input id="s4-cluster-ids" data-multi-field type="text" value="pitch_uid,pitcher"></label>
        <label>方法 Method<select id="s4-cluster-method"><option value="kmeans">K-means</option><option value="gmm">Gaussian Mixture</option></select></label>
        <label>群數 Clusters<input id="s4-cluster-k" type="number" min="2" max="50" value="3"></label>
        <label>隨機種子 Seed<input id="s4-cluster-seed" type="number" value="42"></label>
        <label class="checkbox-line"><input id="s4-cluster-standardize" type="checkbox" checked> 標準化特徵 Standardize</label>
        <label>最大輸入列數安全門檻 Max Input Rows<input id="s4-cluster-max" type="number" min="100" max="1000000" value="200000"></label>
        <label>回傳指派列數 Assignment Rows<input id="s4-cluster-assign" type="number" min="0" max="50000" value="5000"></label>
      </div><p class="hint">安全門檻不是抽樣：若完整輸入超過門檻會拒絕執行，避免悄悄截斷造成統計偏差。 Narrow/filter/aggregate instead of silent truncation.</p>
      <div class="button-row"><button class="primary s4-run" data-s4-mode="clustering">執行分群 Run Clustering</button></div></fieldset></div></div>`;
  }
  function regressionPanelHtml() {
    return `<div id="regression-panel" class="panel"><div class="panel-heading">迴歸分析 Regression</div><div class="panel-body">${filterBoxHtml()}${prepHtml()}
      <fieldset><legend>模型設定 Model Setup</legend><div class="s4-grid">
        <label>應變數 Dependent<input id="s4-reg-dependent" type="text" placeholder="例如 release_speed"></label>
        <label>自變數 Independent<input id="s4-reg-independent" data-multi-field type="text" placeholder="pfx_x,pfx_z,release_spin_rate"></label>
        <label>模型 Model<select id="s4-reg-model"><option value="linear">線性 Linear</option><option value="logistic">二元 Logistic</option></select></label>
        <label>信賴水準 Confidence<input id="s4-reg-confidence" type="number" min="0.5" max="0.999" step="0.01" value="0.95"></label>
        <label class="checkbox-line"><input id="s4-reg-standardize" type="checkbox"> 標準化自變數 Standardize Predictors</label>
        <label>最大輸入列數 Max Input Rows<input id="s4-reg-max" type="number" min="100" max="1000000" value="200000"></label>
      </div><div class="button-row"><button class="primary s4-run" data-s4-mode="regression">執行迴歸 Run Regression</button></div></fieldset></div></div>`;
  }
  function bootstrapPanelHtml() {
    return `<div id="bootstrap-panel" class="panel"><div class="panel-heading">Bootstrap / 信賴區間 Confidence Interval</div><div class="panel-body">${filterBoxHtml()}${prepHtml()}
      <fieldset><legend>重抽樣設定 Resampling Setup</legend><div class="s4-grid">
        <label>數值欄位 Value Field<input id="s4-boot-value" type="text" placeholder="release_speed"></label>
        <label>重抽樣單位 Resample Unit Fields<input id="s4-boot-units" data-multi-field type="text" placeholder="game_pk 或 pitcher,game_pk"></label>
        <label>統計量 Statistic<select id="s4-boot-stat"><option value="mean">平均 Mean</option><option value="median">中位數 Median</option><option value="proportion">比例 Proportion</option></select></label>
        <label>比較分組欄位 Group Field（選填）<input id="s4-boot-group" type="text"></label>
        <label>組 A Group A<input id="s4-boot-a" type="text"></label><label>組 B Group B<input id="s4-boot-b" type="text"></label>
        <label>比例成功值 Proportion Success Value<input id="s4-boot-success" type="text" value="1"></label>
        <label>重抽樣次數 Iterations<input id="s4-boot-iterations" type="number" min="100" max="100000" value="2000"></label>
        <label>信賴水準 Confidence<input id="s4-boot-confidence" type="number" min="0.5" max="0.999" step="0.01" value="0.95"></label>
        <label>隨機種子 Seed<input id="s4-boot-seed" type="number" value="42"></label>
        <label>最大輸入列數 Max Input Rows<input id="s4-boot-max" type="number" min="100" max="1000000" value="200000"></label>
      </div><p class="hint"><strong>必須明確指定重抽樣單位。</strong>例如同場逐球不是彼此獨立時，可按 game_pk 或 pitcher × game_pk 為單位重抽樣；系統不會默認把每顆球當獨立樣本。</p>
      <div class="button-row"><button class="primary s4-run" data-s4-mode="bootstrap">執行 Bootstrap Run Bootstrap</button></div></fieldset></div></div>`;
  }

  function injectPanels() {
    if(document.getElementById("workflow-panel"))return;
    const navigation=document.querySelector(".navigation-pane"),main=document.querySelector(".main-pane"),result=document.querySelector("#result-window");
    if(!navigation||!main||!result)return;
    const group=document.createElement("div"); group.className="task-group";
    group.innerHTML=`<div class="task-group-title">進階研究 Advanced Research</div>${Object.entries(PANEL_IDS).map(([mode,id])=>`<button class="nav-item" data-panel="${id}" data-s4-nav="${mode}">${MODE_LABELS[mode]}</button>`).join("")}`;
    const workspaceGroup=Array.from(navigation.querySelectorAll(".task-group-title")).find(node=>node.textContent.includes("工作區"))?.parentElement;
    navigation.insertBefore(group,workspaceGroup||null);
    const holder=document.createElement("div");holder.innerHTML=workflowPanelHtml()+clusteringPanelHtml()+regressionPanelHtml()+bootstrapPanelHtml();
    Array.from(holder.children).forEach(panel=>main.insertBefore(panel,result));
    Object.values(PANEL_IDS).forEach(id=>window.treepoloPanels?.register?.(id));
    group.querySelectorAll("[data-s4-nav]").forEach(button=>button.addEventListener("click",()=>window.treepoloPanels?.activate(button.dataset.panel,{updateUrl:true,source:"navigation"})));
    document.querySelectorAll("#workflow-panel,#clustering-panel,#regression-panel,#bootstrap-panel").forEach(wirePanelBasics);
    document.querySelectorAll(".s4-run").forEach(button=>button.addEventListener("click",()=>runMode(button.dataset.s4Mode,button)));
    refreshFieldSelects();
  }

  function buildPayload(mode) {
    const panel=document.getElementById(PANEL_IDS[mode]); const payload={mode,filters:filtersFrom(panel)};
    if(mode==="workflow"){payload.stages=stagesFrom(panel);payload.limit=Number(document.getElementById("s4-workflow-limit").value||500);}
    else{
      payload.input_stages=stagesFrom(panel,".s4-input-stage-list");
      if(mode==="clustering")Object.assign(payload,{features:csv(document.getElementById("s4-cluster-features").value),id_fields:csv(document.getElementById("s4-cluster-ids").value),method:document.getElementById("s4-cluster-method").value,clusters:Number(document.getElementById("s4-cluster-k").value),standardize:document.getElementById("s4-cluster-standardize").checked,seed:Number(document.getElementById("s4-cluster-seed").value),max_input_rows:Number(document.getElementById("s4-cluster-max").value),assignment_limit:Number(document.getElementById("s4-cluster-assign").value)});
      if(mode==="regression")Object.assign(payload,{dependent:document.getElementById("s4-reg-dependent").value.trim(),independent:csv(document.getElementById("s4-reg-independent").value),model:document.getElementById("s4-reg-model").value,confidence:Number(document.getElementById("s4-reg-confidence").value),standardize_predictors:document.getElementById("s4-reg-standardize").checked,max_input_rows:Number(document.getElementById("s4-reg-max").value)});
      if(mode==="bootstrap")Object.assign(payload,{value_field:document.getElementById("s4-boot-value").value.trim(),resample_unit_fields:csv(document.getElementById("s4-boot-units").value),statistic:document.getElementById("s4-boot-stat").value,group_field:document.getElementById("s4-boot-group").value.trim()||null,group_a:typedValue(document.getElementById("s4-boot-a").value),group_b:typedValue(document.getElementById("s4-boot-b").value),success_value:typedValue(document.getElementById("s4-boot-success").value),iterations:Number(document.getElementById("s4-boot-iterations").value),confidence:Number(document.getElementById("s4-boot-confidence").value),seed:Number(document.getElementById("s4-boot-seed").value),max_input_rows:Number(document.getElementById("s4-boot-max").value)});
    }
    return payload;
  }

  async function runMode(mode,button) {
    const payload=buildPayload(mode);button.disabled=true;const status=document.querySelector("#status-message");if(status)status.textContent="正在執行分析 Running analysis…";window.treepoloAnalysisProgress?.start();
    try{const response=await fetch("/api/analyze",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});const body=await response.json();if(!response.ok)throw new Error(body.error||`${response.status} ${response.statusText}`);renderResult(body);if(status)status.textContent="分析完成 Analysis complete";document.querySelector("#result-window")?.scrollIntoView({behavior:"smooth",block:"nearest"});}
    catch(error){renderError(error);if(status)status.textContent="分析失敗 Analysis failed";}
    finally{window.treepoloAnalysisProgress?.finish();button.disabled=false;}
  }

  function renderResult(result) {
    const host=document.querySelector("#result-content"),summary=document.querySelector("#result-summary");if(!host||!summary)return;host.innerHTML="";const sections=result.sections||[result];let total=0;
    sections.forEach(section=>{total+=Number(section.row_count??section.rows?.length??0);if(section.title){const title=document.createElement("div");title.className="result-section-title";title.textContent=`${section.title} · ${section.row_count??section.rows?.length??0} 列 rows`;host.append(title);}const table=document.createElement("table");table.className="result-table";const thead=document.createElement("thead"),trh=document.createElement("tr");(section.columns||[]).forEach(column=>{const th=document.createElement("th");th.textContent=column;trh.append(th);});thead.append(trh);table.append(thead);const tbody=document.createElement("tbody");(section.rows||[]).forEach(row=>{const tr=document.createElement("tr");(section.columns||[]).forEach(column=>{const td=document.createElement("td");const value=row[column];td.textContent=value==null?"—":typeof value==="number"?formatNumber(value):String(value);tr.append(td);});tbody.append(tr);});table.append(tbody);host.append(table);});
    const backend=result.backend||Array.from(new Set(sections.map(s=>s.backend).filter(Boolean))).join("+")||"—";summary.textContent=`${total} 列 rows · ${backend}${result.input_backend?` · input: ${result.input_backend}`:""}`;
    if(result.cache){const badge=document.createElement("span");badge.className="s4-result-note";badge.textContent=result.cache.hit?"快取命中 Cache Hit":result.cache.stored?"已快取 Cached":"未快取 Not Cached";summary.append(" ",badge);}
  }
  function formatNumber(value){if(!Number.isFinite(value))return String(value);if(Number.isInteger(value))return String(value);return value.toFixed(6).replace(/0+$/,"").replace(/\.$/,"");}
  function renderError(error){const host=document.querySelector("#result-content"),summary=document.querySelector("#result-summary");if(summary)summary.textContent="錯誤 Error";if(host){host.innerHTML="";const box=document.createElement("div");box.className="error-box";const strong=document.createElement("strong");strong.textContent="執行失敗 Analysis Failed";const detail=document.createElement("div");detail.textContent=error.message;box.append(strong,detail);host.append(box);}}

  function applyFilters(panel,filters=[]){const box=panel.querySelector(".s4-filter-box"),list=box.querySelector(".s4-filter-list");list.innerHTML="";filters.forEach(spec=>addFilter(box,spec));}
  function applyStages(panel,stages=[],selector=".s4-stage-list"){const list=panel.querySelector(selector);list.innerHTML="";stages.forEach(spec=>addStage(list,spec));}
  function applyPayload(payload) {
    const panel=document.getElementById(PANEL_IDS[payload.mode]);if(!panel)return false;
    applyFilters(panel,payload.filters||[]);
    if(payload.mode==="workflow"){applyStages(panel,payload.stages||[]);document.getElementById("s4-workflow-limit").value=payload.limit??500;}
    else applyStages(panel,payload.input_stages||[],".s4-input-stage-list");
    if(payload.mode==="clustering"){document.getElementById("s4-cluster-features").value=(payload.features||[]).join(",");document.getElementById("s4-cluster-ids").value=(payload.id_fields||[]).join(",");document.getElementById("s4-cluster-method").value=payload.method||"kmeans";document.getElementById("s4-cluster-k").value=payload.clusters??3;document.getElementById("s4-cluster-standardize").checked=payload.standardize!==false;document.getElementById("s4-cluster-seed").value=payload.seed??42;document.getElementById("s4-cluster-max").value=payload.max_input_rows??200000;document.getElementById("s4-cluster-assign").value=payload.assignment_limit??5000;}
    if(payload.mode==="regression"){document.getElementById("s4-reg-dependent").value=payload.dependent||"";document.getElementById("s4-reg-independent").value=(payload.independent||[]).join(",");document.getElementById("s4-reg-model").value=payload.model||"linear";document.getElementById("s4-reg-confidence").value=payload.confidence??.95;document.getElementById("s4-reg-standardize").checked=Boolean(payload.standardize_predictors);document.getElementById("s4-reg-max").value=payload.max_input_rows??200000;}
    if(payload.mode==="bootstrap"){document.getElementById("s4-boot-value").value=payload.value_field||"";document.getElementById("s4-boot-units").value=(payload.resample_unit_fields||[]).join(",");document.getElementById("s4-boot-stat").value=payload.statistic||"mean";document.getElementById("s4-boot-group").value=payload.group_field||"";document.getElementById("s4-boot-a").value=payload.group_a??"";document.getElementById("s4-boot-b").value=payload.group_b??"";document.getElementById("s4-boot-success").value=payload.success_value??1;document.getElementById("s4-boot-iterations").value=payload.iterations??2000;document.getElementById("s4-boot-confidence").value=payload.confidence??.95;document.getElementById("s4-boot-seed").value=payload.seed??42;document.getElementById("s4-boot-max").value=payload.max_input_rows??200000;}
    document.dispatchEvent(new CustomEvent("treepolo:analysis-options-changed"));
    return true;
  }

  function init(){injectStyles();injectPanels();document.addEventListener("treepolo:fields-updated",refreshFieldSelects);refreshFieldSelects();window.treepoloStage4Pages={applyPayload,buildPayload,renderResult,panelIdForMode:mode=>PANEL_IDS[mode]||null};}
  if(document.readyState==="loading")document.addEventListener("DOMContentLoaded",init,{once:true});else init();
})();
