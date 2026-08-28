(() => {
  "use strict";

  let lastPayload = null;
  let lastResult = null;
  const nativeFetch = window.fetch.bind(window);
  const MODE_PANELS = {
    basic:"basic-panel", sequence_pattern:"sequence-panel", follow_event:"follow-panel",
    arsenal:"arsenal-panel", pitch_role:"role-panel", temporal:"temporal-panel",
    percentile:"percentile-panel", cross_level:"cross-panel", arsenal_change:"arsenal-change-panel",
    workflow:"workflow-panel", clustering:"clustering-panel", regression:"regression-panel",
    bootstrap:"bootstrap-panel", cluster_compare:"cluster-compare-panel",
  };
  const STAGE4_MODES = new Set(["workflow","clustering","regression","bootstrap","cluster_compare"]);
  const METRIC_NAMES = {
    avg:["Average","平均值"], sum:["Sum","總和"], min:["Minimum","最小值"], max:["Maximum","最大值"],
    median:["Median","中位數"], stddev_pop:["Population standard deviation","母體標準差"],
    stddev_samp:["Sample standard deviation","樣本標準差"],
  };

  async function api(path, options={}) {
    const response = await window.fetch(path, { headers:{"Content-Type":"application/json"}, ...options });
    let body = {}; try { body = await response.json(); } catch {}
    if (!response.ok) throw new Error(body.error || `${response.status} ${response.statusText}`);
    return body;
  }

  function clusteringPartitionFields() {
    const input = document.getElementById("s4-cluster-partitions");
    return String(input?.value || "").split(",").map(v=>v.trim()).filter(Boolean);
  }

  window.fetch = async function treepoloStage4Fetch(input, init={}) {
    const url = typeof input === "string" ? input : input?.url || "";
    const method = String(init?.method || "GET").toUpperCase();
    let requestInit = init;
    let parsed = null;
    if (url.includes("/api/analyze") && method === "POST" && typeof init.body === "string") {
      try {
        parsed = JSON.parse(init.body);
        if (parsed?.mode === "clustering" && !Array.isArray(parsed.partition_fields)) {
          parsed.partition_fields = clusteringPartitionFields();
          requestInit = { ...init, body:JSON.stringify(parsed) };
        }
        lastPayload = parsed;
      } catch { lastPayload = null; }
    }
    const response = await nativeFetch(input, requestInit);
    if (url.includes("/api/analyze") && method === "POST" && response.ok) {
      try {
        lastResult = await response.clone().json();
        window.treepoloLastAnalysis = { payload:lastPayload, result:lastResult };
        setTimeout(()=>{ decorateCache(lastResult); refreshLibrary().catch(()=>{}); }, 20);
      } catch { lastResult = null; }
    }
    return response;
  };

  function injectStyles() {
    if (document.getElementById("stage4-workspace-styles")) return;
    const style = document.createElement("style"); style.id="stage4-workspace-styles";
    style.textContent=`
      .analysis-library-toolbar{display:grid;grid-template-columns:minmax(180px,1fr) minmax(240px,2fr) auto;gap:8px;align-items:end;margin-bottom:12px}
      .analysis-library-toolbar label{display:flex;flex-direction:column;gap:4px}.analysis-library-table{width:100%;border-collapse:collapse;font-size:12px}
      .analysis-library-table th,.analysis-library-table td{border:1px solid #aeb7c4;padding:5px 7px;vertical-align:top}.analysis-library-table th{background:#e8eef7;text-align:left}
      .analysis-library-actions{white-space:nowrap}.analysis-library-actions button{margin-right:4px}.library-empty{padding:10px;color:#5b6572}.library-section{margin-top:12px}
      .cache-badge{display:inline-block;margin-left:8px;padding:1px 6px;border:1px solid #7893b5;background:#eef5ff;font-size:11px;font-weight:600}
      .metric-row.metric-invalid .metric-field{outline:2px solid #b12828;background:#fff3f3}`;
    document.head.append(style);
  }

  function syncMetricRow(row) {
    if (!row) return;
    const fn=row.querySelector(".metric-function")?.value||"count";
    const field=row.querySelector(".metric-field"); if(!field)return;
    const empty=Array.from(field.options).find(o=>o.value==="");
    const emptyLabel=fn==="count"?"不指定 None":"請選擇欄位 Select Field";
    if(empty && empty.textContent!==emptyLabel) empty.textContent=emptyLabel;
    field.required=fn!=="count"; row.classList.toggle("metric-invalid",fn!=="count"&&!field.value);
  }
  function installMetricValidation() {
    const host=document.querySelector("#basic-metrics"); if(!host)return;
    const sync=()=>host.querySelectorAll(".metric-row").forEach(syncMetricRow); sync();
    new MutationObserver(sync).observe(host,{childList:true});
    host.addEventListener("change",e=>syncMetricRow(e.target.closest?.(".metric-row")));
    document.addEventListener("click",e=>{
      if(!e.target.closest?.('[data-run="basic"]'))return;
      const invalid=Array.from(host.querySelectorAll(".metric-row")).find(row=>{
        syncMetricRow(row); return row.querySelector(".metric-function")?.value!=="count"&&!row.querySelector(".metric-field")?.value;
      });
      if(!invalid)return;
      e.preventDefault();e.stopImmediatePropagation();
      const fn=invalid.querySelector(".metric-function").value; const [en,zh]=METRIC_NAMES[fn]||[fn,"此指標"];
      const message=`${en} requires a metric field / ${zh}必須指定計算欄位`;
      const summary=document.querySelector("#result-summary"); if(summary)summary.textContent="錯誤 Error";
      const hostResult=document.querySelector("#result-content"); if(hostResult)hostResult.innerHTML=`<div class="error-box"><strong>執行失敗 Analysis Failed</strong><div></div></div>`;
      const detail=hostResult?.querySelector(".error-box div"); if(detail)detail.textContent=message;
      const status=document.querySelector("#status-message"); if(status)status.textContent=message;
    },true);
  }

  function decorateCache(result) {
    if(!result?.cache)return; const summary=document.querySelector("#result-summary"); if(!summary)return;
    summary.querySelector(".cache-badge")?.remove(); const badge=document.createElement("span");badge.className="cache-badge";
    badge.textContent=result.cache.hit?"快取命中 Cache Hit":result.cache.stored?"已寫入快取 Cached":"結果過大未快取 Not Cached"; summary.append(" ",badge);
  }
  function switchPanel(id){document.querySelectorAll(".nav-item").forEach(x=>x.classList.toggle("active",x.dataset.panel===id));document.querySelectorAll(".panel").forEach(x=>x.classList.toggle("active-panel",x.id===id));}
  function modeLabel(mode){return({basic:"基本分析 Basic",sequence_pattern:"球序模式 Sequence",follow_event:"後續事件 Follow-up",arsenal:"球種武器庫 Arsenal",pitch_role:"球種角色 Pitch Role",temporal:"時間序列 Temporal",percentile:"個人門檻 Threshold",cross_level:"層級比較 Level Comparison",arsenal_change:"武器庫變化 Arsenal Change",workflow:"研究工作流 Workflow",clustering:"自動分群 Clustering",regression:"迴歸 Regression",bootstrap:"Bootstrap",cluster_compare:"多階段分群比較 Cluster Comparison"})[mode]||mode;}
  function formatTime(value){try{return new Intl.DateTimeFormat("zh-TW",{dateStyle:"short",timeStyle:"medium"}).format(new Date(value));}catch{return value||"—";}}
  function actionButton(text,handler){const b=document.createElement("button");b.type="button";b.textContent=text;b.addEventListener("click",()=>Promise.resolve(handler()).catch(showLibraryStatus));return b;}
  function showLibraryStatus(error){const status=document.querySelector("#status-message");if(status)status.textContent=error?.message||String(error);}

  function injectLibrary(){
    if(document.getElementById("analysis-library-panel"))return;
    const nav=document.querySelector(".navigation-pane"),main=document.querySelector(".main-pane"),result=document.querySelector("#result-window");if(!nav||!main||!result)return;
    const group=document.createElement("div");group.className="task-group";group.innerHTML='<div class="task-group-title">工作區 Workspace</div><button class="nav-item" data-panel="analysis-library-panel">分析紀錄 Analysis Library</button>';nav.append(group);
    group.querySelector("button").addEventListener("click",()=>{switchPanel("analysis-library-panel");refreshLibrary().catch(showLibraryStatus);});
    const panel=document.createElement("div");panel.id="analysis-library-panel";panel.className="panel";panel.innerHTML=`<div class="panel-heading">分析紀錄 Analysis Library</div><div class="panel-body">
      <fieldset><legend>儲存目前分析 Save Current Analysis</legend><div class="analysis-library-toolbar"><label>名稱 Name<input id="analysis-save-name" type="text"></label><label>備註 Notes<input id="analysis-save-notes" type="text"></label><button id="analysis-save-current" type="button">儲存 Save</button></div><p class="hint">保存完整分析設定；相同資料版本的快取結果仍存在時可直接回看。 Saves the full analysis specification and restores a cached result when available.</p></fieldset>
      <fieldset class="library-section"><legend>已儲存分析 Saved Analyses</legend><div id="saved-analysis-list" class="library-empty">讀取中 Loading…</div></fieldset>
      <fieldset class="library-section"><legend>最近分析 History</legend><div id="analysis-history-list" class="library-empty">讀取中 Loading…</div></fieldset></div>`;
    main.insertBefore(panel,result);panel.querySelector("#analysis-save-current").addEventListener("click",()=>saveCurrent().catch(showLibraryStatus));
  }
  async function saveCurrent(){if(!lastPayload)throw new Error("目前沒有可儲存的分析。 Run or load an analysis first.");const name=document.querySelector("#analysis-save-name")?.value?.trim()||"";if(!name)throw new Error("請輸入分析名稱。 Analysis name is required.");await api("/api/analysis/saved",{method:"POST",body:JSON.stringify({name,notes:document.querySelector("#analysis-save-notes")?.value||"",analysis_payload:lastPayload,cache_key:lastResult?.cache?.key||null,data_revision:lastResult?.cache?.data_revision||null})});document.querySelector("#analysis-save-name").value="";document.querySelector("#analysis-save-notes").value="";await refreshLibrary();}

  function renderSaved(items){
    const host=document.querySelector("#saved-analysis-list");if(!host)return;host.innerHTML="";if(!items.length){host.textContent="尚未儲存分析。 No saved analyses.";return;}
    const table=document.createElement("table");table.className="analysis-library-table";table.innerHTML="<thead><tr><th>名稱 Name</th><th>模式 Mode</th><th>更新 Updated</th><th>備註 Notes</th><th>操作 Actions</th></tr></thead>";const body=document.createElement("tbody");
    items.forEach(item=>{const tr=document.createElement("tr");[item.name,modeLabel(item.payload?.mode),formatTime(item.updated_at),item.notes||"—"].forEach(v=>{const td=document.createElement("td");td.textContent=v;tr.append(td);});const td=document.createElement("td");td.className="analysis-library-actions";td.append(actionButton("載入 Load",()=>loadSaved(item.id)),actionButton("刪除 Delete",()=>deleteSaved(item.id)));tr.append(td);body.append(tr);});table.append(body);host.append(table);
  }
  function renderHistory(items){
    const host=document.querySelector("#analysis-history-list");if(!host)return;host.innerHTML="";if(!items.length){host.textContent="尚無分析紀錄。 No analysis history.";return;}
    const table=document.createElement("table");table.className="analysis-library-table";table.innerHTML="<thead><tr><th>時間 Time</th><th>模式 Mode</th><th>結果 Rows</th><th>執行器 Backend</th><th>狀態 Status</th><th>操作 Actions</th></tr></thead>";const body=document.createElement("tbody");
    items.forEach(item=>{const tr=document.createElement("tr");[formatTime(item.created_at),modeLabel(item.mode),item.row_count??"—",item.backend||"—",item.status].forEach(v=>{const td=document.createElement("td");td.textContent=v;tr.append(td);});const td=document.createElement("td");td.append(actionButton("載入 Load",()=>loadHistory(item.id)));tr.append(td);body.append(tr);});table.append(body);host.append(table);
  }
  async function refreshLibrary(){if(!document.getElementById("analysis-library-panel"))return;const [saved,history]=await Promise.all([api("/api/analysis/saved"),api("/api/analysis/history?limit=100")]);renderSaved(saved.saved||[]);renderHistory(history.history||[]);}
  async function deleteSaved(id){await api(`/api/analysis/saved/${id}`,{method:"DELETE"});await refreshLibrary();}
  async function loadSaved(id){const body=await api(`/api/analysis/saved/${id}`);await loadItem(body.item,{kind:"saved",id});}
  async function loadHistory(id){const body=await api(`/api/analysis/history/${id}`);await loadItem(body.item,{kind:"history",id});}

  function setSelect(id,value){const el=document.getElementById(id);if(!el)return;if(el.multiple){const wanted=new Set(Array.isArray(value)?value:[value]);Array.from(el.options).forEach(o=>o.selected=wanted.has(o.value));}else el.value=value==null?"":String(value);el.dispatchEvent(new Event("change",{bubbles:true}));}
  function setInput(id,value,checked=false){const el=document.getElementById(id);if(!el)return;if(checked)el.checked=Boolean(value);else el.value=value==null?"":String(value);el.dispatchEvent(new Event("change",{bubbles:true}));}
  function setFilters(name,filters=[]){const host=document.querySelector(`[data-filter-box="${name}"]`),list=host?.querySelector(".filter-list"),add=host?.querySelector(".add-filter");if(!list||!add)return;list.innerHTML="";(filters||[]).forEach(spec=>{add.click();const row=list.lastElementChild;if(!row)return;row.querySelector(".condition-field").value=spec.field||"";row.querySelector(".condition-op").value=spec.op||"eq";row.querySelector(".condition-value").value=Array.isArray(spec.value)?spec.value.join(","):(spec.value??"");});}
  function setSingle(id,spec={}){const row=document.querySelector(`#${id} .condition-row`);if(!row)return;row.querySelector(".condition-field").value=spec.field||"";row.querySelector(".condition-op").value=spec.op||"eq";row.querySelector(".condition-value").value=Array.isArray(spec.value)?spec.value.join(","):(spec.value??"");}
  function setMetrics(metrics=[]){const host=document.querySelector("#basic-metrics"),add=document.querySelector("[data-add-metric]");if(!host||!add)return;host.innerHTML="";(metrics.length?metrics:[{function:"count",field:"",distinct:false}]).forEach(spec=>{add.click();const row=host.lastElementChild;row.querySelector(".metric-function").value=spec.function||"count";row.querySelector(".metric-field").value=spec.field||"";row.querySelector(".metric-distinct").checked=Boolean(spec.distinct);syncMetricRow(row);});}
  function setSort(mode,sorts=[]){const box=document.querySelector(`[data-result-ordering="${mode}"]`),list=box?.querySelector(`[data-result-sort-list="${mode}"]`),add=box?.querySelector(".add-result-sort");if(!list||!add)return;list.innerHTML="";(sorts||[]).forEach(spec=>{add.click();const row=list.lastElementChild;row.querySelector(".result-sort-field").value=spec.field||"";row.querySelector(".result-sort-direction").value=spec.descending?"desc":"asc";});}

  function applyLegacyPayload(p){
    const m=p.mode;
    if(m==="basic"){setFilters("basic",p.filters);setSelect("basic-group",p.group_by||[]);setMetrics(p.metrics||[]);setInput("basic-limit",p.limit??200);}
    else if(m==="sequence_pattern"){setFilters("sequence",p.filters);setSingle("sequence-event",p.event);setInput("sequence-occurrence",p.occurrence??1);setInput("sequence-exact",p.exact_count??"");setSelect("sequence-arrangement",p.arrangement||"any");setInput("sequence-last",p.require_last_event,true);}
    else if(m==="follow_event"){setFilters("follow",p.filters);setSingle("follow-anchor",p.anchor);setSingle("follow-target",p.target);setSingle("follow-between",(p.between||[])[0]||{});setInput("follow-gap",p.max_gap??3);}
    else if(m==="arsenal"){setFilters("arsenal",p.filters);setSelect("arsenal-entities",p.entity_fields||[]);setInput("arsenal-min-usage",p.min_usage??.05);setSelect("arsenal-tie",p.tie_method||"dense_rank");}
    else if(m==="pitch_role"){setFilters("role",p.filters);setSelect("role-entities",p.entity_fields||[]);setSelect("role-metric-kind",p.metric_kind||"usage_rate");setSelect("role-value-field",p.value_field||"release_speed");setSelect("role-function",p.function||"avg");setInput("role-rank",p.rank??1);setSelect("role-direction",p.descending===false?"asc":"desc");setInput("role-exclude",(p.exclude_pitch_types||[]).join(", "));setSelect("role-tie",p.tie_method||"dense_rank");}
    else if(m==="temporal"){setFilters("temporal",p.filters);setSelect("temporal-entities",p.entity_fields||[]);setSelect("temporal-period",p.period_field||"game_pk");setSelect("temporal-value",p.value_field||"release_speed");setSelect("temporal-function",p.function||"avg");setSelect("temporal-direction",p.direction||"previous");setInput("temporal-offset",p.offset??1);}
    else if(m==="percentile"){setFilters("percentile",p.filters);setSelect("percentile-entities",p.entity_fields||[]);setSelect("percentile-value",p.value_field||"release_speed");setInput("percentile-threshold",Number(p.threshold??.8)*100);setSelect("percentile-side",p.side||"high");}
    else if(m==="cross_level"){setFilters("cross",p.filters);setSelect("cross-unit",p.unit_fields||[]);setSelect("cross-baseline",p.baseline_fields||[]);setSelect("cross-value",p.value_field||"release_speed");setSelect("cross-function",p.function||"avg");}
    else if(m==="arsenal_change"){setFilters("arsenal-change",p.filters);setSelect("change-entities",p.entity_fields||[]);setInput("change-min-usage",p.min_usage??.05);setInput("change-a-start",p.period_a?.start||"");setInput("change-a-end",p.period_a?.end||"");setInput("change-b-start",p.period_b?.start||"");setInput("change-b-end",p.period_b?.end||"");}
    setSort(m,p.result_sort||[]);document.dispatchEvent(new CustomEvent("treepolo:analysis-options-changed"));
  }

  function applyPayload(payload){
    if(STAGE4_MODES.has(payload.mode)){
      if(payload.mode==="clustering" && document.getElementById("s4-cluster-partitions")) setInput("s4-cluster-partitions",(payload.partition_fields||[]).join(","));
      const applied=window.treepoloStage4Pages?.applyPayload?.(payload)||window.treepoloClusterComparePage?.applyPayload?.(payload);
      if(!applied)throw new Error(`Stage 4 analysis page is unavailable: ${payload.mode}`);
      return;
    }
    applyLegacyPayload(payload);
  }

  function renderStoredResult(result){const host=document.querySelector("#result-content"),summary=document.querySelector("#result-summary");if(!host||!summary)return;host.innerHTML="";const render=section=>{const wrap=document.createElement("div");if(section.title){const h=document.createElement("div");h.className="result-section-title";h.textContent=section.title;wrap.append(h);}const table=document.createElement("table");table.className="result-table";const head=document.createElement("thead"),hr=document.createElement("tr");(section.columns||[]).forEach(c=>{const th=document.createElement("th");th.textContent=c;hr.append(th);});head.append(hr);table.append(head);const body=document.createElement("tbody");(section.rows||[]).forEach(row=>{const tr=document.createElement("tr");(section.columns||[]).forEach(c=>{const td=document.createElement("td");td.textContent=row[c]==null?"—":String(row[c]);tr.append(td);});body.append(tr);});table.append(body);wrap.append(table);return wrap;};if(result.sections)result.sections.forEach(s=>host.append(render(s)));else host.append(render(result));const count=result.row_count??(result.sections||[]).reduce((sum,s)=>sum+Number(s.row_count||0),0);summary.textContent=`${count} 列 rows · 已載入保存結果 Loaded Stored Result`;decorateCache(result);}
  async function loadItem(item,source={}){if(!item?.payload)return;lastPayload=item.payload;lastResult=item.result||null;window.treepoloLastAnalysis={payload:lastPayload,result:lastResult,history_id:source.kind==="history"?(source.id||item.id||null):null,cache_key:item.cache_key||null,data_revision:item.data_revision||null,loaded_source_kind:source.kind||null,loaded_source_id:source.id||item.id||null};if(item.result_available&&item.result)renderStoredResult(item.result);applyPayload(item.payload);const id=MODE_PANELS[item.payload.mode];if(id&&document.getElementById(id))switchPanel(id);document.dispatchEvent(new CustomEvent("treepolo:analysis-current-source-updated",{detail:{kind:source.kind||null,id:source.id||item.id||null,history_id:source.kind==="history"?(source.id||item.id||null):null,cache_key:item.cache_key||null,data_revision:item.data_revision||null}}));}

  function injectClusteringPartitionControl(){
    const panel=document.getElementById("clustering-panel");if(!panel||document.getElementById("s4-cluster-partitions"))return;
    const grid=panel.querySelector("fieldset:last-of-type .s4-grid");if(!grid)return;
    const label=document.createElement("label");label.innerHTML='各自分群 Partition By<input id="s4-cluster-partitions" type="text" placeholder="例如 pitcher">';grid.insertBefore(label,grid.firstChild);
    const hint=panel.querySelector("fieldset:last-of-type .hint");if(hint)hint.textContent="Partition By 會為每個個體獨立建模；留空才是把所有資料一起分群。安全門檻不是抽樣，超過門檻會拒絕執行。";
  }

  function loadClusterComparePage(){if(document.querySelector('script[data-cluster-compare-loader]'))return;const s=document.createElement("script");s.src="/cluster-comparison-page.js";s.dataset.clusterCompareLoader="1";document.body.append(s);}

  function init(){injectStyles();installMetricValidation();injectLibrary();injectClusteringPartitionControl();loadClusterComparePage();refreshLibrary().catch(showLibraryStatus);}
  if(document.readyState==="loading")document.addEventListener("DOMContentLoaded",init,{once:true});else init();
})();