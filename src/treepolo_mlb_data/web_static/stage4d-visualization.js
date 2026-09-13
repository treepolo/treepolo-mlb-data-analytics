(() => {
  "use strict";

  if (window.treepoloStage4D) return;

  const SVG_NS = "http://www.w3.org/2000/svg";
  const PALETTE = ["#2f6fad","#c3543f","#3f8a5b","#8a5ba5","#c18a2d","#477f8e","#9a5966","#65728a","#6c8f36","#a76d34"];
  const state = {
    catalog:null,
    source:null,
    currentClientResult:null,
    prepared:null,
    sectionIndex:0,
    loading:false,
    savedVisualizationId:null,
  };

  const $ = (selector, root=document) => root.querySelector(selector);
  const $$ = (selector, root=document) => Array.from(root.querySelectorAll(selector));

  async function api(path, options={}) {
    const response = await fetch(path, {headers:{"Content-Type":"application/json"}, ...options});
    let body = {};
    try { body = await response.json(); } catch {}
    if (!response.ok) throw new Error(body.error || `${response.status} ${response.statusText}`);
    return body;
  }

  function escapeHtml(value) {
    const div=document.createElement("div"); div.textContent=String(value ?? ""); return div.innerHTML;
  }
  function fmt(value) {
    if (value == null) return "—";
    if (typeof value === "number" && Number.isFinite(value)) {
      if (Number.isInteger(value)) return value.toLocaleString("en-US");
      return value.toFixed(4).replace(/0+$/,"").replace(/\.$/,"");
    }
    return String(value);
  }
  function modeLabel(mode) {
    return ({basic:"基本分析 Basic",sequence_pattern:"球序模式 Sequence",follow_event:"後續事件 Follow-up",arsenal:"球種武器庫 Arsenal",pitch_role:"球種角色 Pitch Role",temporal:"時間序列 Temporal",percentile:"個別百分位門檻 Individual Percentile Threshold",cross_level:"層級比較 Level Comparison",arsenal_change:"武器庫變化 Arsenal Change",workflow:"研究工作流 Research Workflow",clustering:"自動分群 Clustering",regression:"迴歸 Regression",bootstrap:"Bootstrap",cluster_compare:"多階段分群比較 Cluster Comparison"})[mode] || mode || "Analysis";
  }
  function formatTime(value) {
    try { return new Intl.DateTimeFormat("zh-TW",{dateStyle:"short",timeStyle:"short"}).format(new Date(value)); }
    catch { return value || "—"; }
  }
  function setStatus(message, error=false) {
    const host=$("#viz-status"); if(host){host.textContent=message;host.classList.toggle("error-box",Boolean(error));}
    const appStatus=$("#status-message"); if(appStatus) appStatus.textContent=message;
  }

  function injectStyles() {
    if ($("#stage4d-styles")) return;
    const style=document.createElement("style"); style.id="stage4d-styles";
    style.textContent=`
      body.output-panel-active #result-window{display:none!important}
      .stage4d-grid{display:grid;grid-template-columns:minmax(245px,310px) minmax(420px,1fr);gap:10px;align-items:start}
      .stage4d-controls{display:flex;flex-direction:column;gap:8px}.stage4d-controls fieldset{margin:0}.stage4d-controls label{display:flex;flex-direction:column;gap:2px;font-size:12px}
      .stage4d-map-grid,.stage4d-display-grid{display:grid;grid-template-columns:1fr 1fr;gap:6px 8px}.stage4d-span{grid-column:1/-1}
      .stage4d-canvas-pane{min-width:0}.stage4d-canvas-frame{border:1px solid #7f9db9;background:#fff;box-shadow:inset 0 0 0 1px #d6dfeb;overflow:auto;min-height:520px;padding:8px}
      #viz-canvas{display:block;margin:auto;max-width:none;background:#fff}.viz-toolbar{display:flex;flex-wrap:wrap;gap:5px;align-items:center;margin-bottom:7px}.viz-toolbar select{min-width:92px}
      .viz-source-meta{display:grid;grid-template-columns:120px 1fr;gap:3px 8px;font-size:11px;padding:7px;border:1px solid #b8c2cf;background:#f5f7fa;overflow-wrap:anywhere}
      .viz-source-meta b{color:#34495e}.viz-sampling-note{padding:5px 7px;border:1px solid #b7a15d;background:#fff8dc;font-size:11px}.viz-sampling-note.full{border-color:#8ca68b;background:#f2faef}
      .viz-library-table{width:100%;border-collapse:collapse;font-size:11px}.viz-library-table th,.viz-library-table td{border:1px solid #aeb7c4;padding:4px 6px;vertical-align:top}.viz-library-table th{background:#e8eef7;text-align:left}.viz-library-table button{margin-right:3px}
      .viz-empty{padding:9px;color:#596575}.viz-result-toolbar{display:flex;align-items:center;gap:5px;margin-left:auto}.viz-result-toolbar select{max-width:160px}
      .result-save-toolbar{flex-wrap:wrap}.result-save-toolbar .viz-result-toolbar{margin-left:auto}
      .stage4d-modal-row{display:flex;gap:7px;align-items:end}.stage4d-modal-row label{flex:1}
      .viz-checkbox{display:flex!important;flex-direction:row!important;align-items:center;gap:4px!important}.viz-mini{font-size:11px;color:#53606d}
      .viz-label{font:11px Arial,'Microsoft JhengHei',sans-serif;fill:#263849}.viz-title{font:600 16px Arial,'Microsoft JhengHei',sans-serif;fill:#172b3a}.viz-subtitle{font:11px Arial,'Microsoft JhengHei',sans-serif;fill:#617080}.viz-axis{stroke:#76889a;stroke-width:1}.viz-gridline{stroke:#e2e7ec;stroke-width:1}.viz-reference{stroke:#9b5b45;stroke-width:1;stroke-dasharray:5 4}.viz-legend{font:10px Arial,'Microsoft JhengHei',sans-serif;fill:#314354}
      @media(max-width:1000px){.stage4d-grid{grid-template-columns:1fr}.stage4d-canvas-frame{min-height:420px}}
    `;
    document.head.append(style);
  }

  function registerNavButton(button, panelId) {
    button.addEventListener("click",()=>window.treepoloPanels?.activate(panelId,{updateUrl:true,source:"navigation"}));
  }

  function restructureNavigation() {
    const nav=$(".navigation-pane"); if(!nav)return;
    $$(".task-group",nav).filter(group=>group.querySelector(".task-group-title")?.textContent.includes("工作區")).forEach(group=>group.remove());
    let output=$("#stage4d-output-nav");
    if(!output){
      output=document.createElement("div"); output.id="stage4d-output-nav"; output.className="task-group";
      output.innerHTML=`<div class="task-group-title">輸出 Output</div>
        <button class="nav-item" data-panel="visualization-panel">視覺化 Visualization</button>
        <button class="nav-item" data-panel="analysis-library-panel">分析庫 Analysis Library</button>
        <button class="nav-item" data-panel="analysis-history-panel">分析紀錄 Analysis History</button>`;
      const dataGroup=$$(".task-group",nav).find(group=>group.querySelector(".task-group-title")?.textContent.includes("資料 Data"));
      nav.insertBefore(output,dataGroup||null);
      if(dataGroup) nav.append(dataGroup);
      output.querySelectorAll("button").forEach(button=>registerNavButton(button,button.dataset.panel));
    }
  }

  function injectPanels() {
    const main=$(".main-pane"),result=$("#result-window"); if(!main||!result)return;
    const library=$("#analysis-library-panel");
    if(library){
      const heading=library.querySelector(".panel-heading"); if(heading) heading.textContent="分析庫 Analysis Library";
      const historyBox=$("#analysis-history-list")?.closest("fieldset");
      if(historyBox&&!$("#analysis-history-panel")){
        const history=document.createElement("div"); history.id="analysis-history-panel"; history.className="panel";
        history.innerHTML='<div class="panel-heading">分析紀錄 Analysis History</div><div class="panel-body"></div>';
        history.querySelector(".panel-body").append(historyBox);
        main.insertBefore(history,result);
        window.treepoloPanels?.register?.("analysis-history-panel","analysis-history");
      }
    }
    if(!$("#visualization-panel")){
      const panel=document.createElement("div");panel.id="visualization-panel";panel.className="panel";
      panel.innerHTML=`<div class="panel-heading">視覺化 Visualization</div><div class="panel-body">
        <div class="stage4d-grid">
          <div class="stage4d-controls">
            <fieldset><legend>資料來源 Data Source</legend>
              <label>來源類型 Source Type<select id="viz-source-kind"><option value="current">目前分析 Current Result</option><option value="recent">近期結果 Recent Results</option><option value="history">分析紀錄 Analysis History</option><option value="saved">分析庫 Saved Analyses</option><option value="visualization">已儲存視覺化 Saved Visualizations</option></select></label>
              <label>資料 Data<select id="viz-source-item"></select></label>
              <label>結果區段 Result Section<select id="viz-section"><option value="0">Section 1</option></select></label>
              <div class="button-row"><button id="viz-load">載入資料 Load Data</button><button id="viz-rerun" style="display:none">重新執行取得完整結果 Re-run Full Result</button></div>
            </fieldset>
            <fieldset><legend>視覺化類型 Presentation</legend>
              <label>預設 Preset<select id="viz-preset"><option value="">自訂 Custom</option></select></label>
              <label>類型 Type<select id="viz-type"><option value="line">折線圖 Line</option><option value="bar">長條圖 Bar</option><option value="scatter">散點圖 Scatter</option><option value="range">點與區間 Point / Range</option><option value="dumbbell">配對比較 Dumbbell</option><option value="difference">差值圖 Difference</option></select></label>
              <div class="stage4d-map-grid">
                <label>X 軸 / 類別 X<select id="viz-x"></select></label><label>Y 軸 Y<select id="viz-y"></select></label>
                <label>系列 Series<select id="viz-series"></select></label><label>標籤 Label<select id="viz-label"></select></label>
                <label>下界 / 基準 Lower / Baseline<select id="viz-lower"></select></label><label>上界 Upper<select id="viz-upper"></select></label>
              </div>
            </fieldset>
            <fieldset><legend>資料處理 Data Handling</legend>
              <label>繪圖資料 Sampling<select id="viz-sampling"><option value="full">完整資料 Full Data</option><option value="automatic" selected>自動抽樣 Automatic Sampling</option><option value="manual">手動抽樣 Manual Sampling</option></select></label>
              <div class="stage4d-map-grid"><label>抽樣方式 Method<select id="viz-sample-method"><option value="random">隨機 Random</option><option value="every_nth">每 N 間隔 Every Nth Row</option></select></label><label>抽樣筆數 Sample Rows<input id="viz-sample-size" type="number" min="1" max="50000" value="5000"></label><label>Seed<input id="viz-sample-seed" type="number" value="42"></label></div>
              <div id="viz-sampling-note" class="viz-sampling-note">尚未載入資料 Data not loaded</div>
            </fieldset>
            <fieldset><legend>顯示設定 Display</legend><div class="stage4d-display-grid">
              <label class="stage4d-span">標題 Title<input id="viz-title" type="text"></label><label class="stage4d-span">副標題 Subtitle<input id="viz-subtitle" type="text"></label>
              <label>寬度 Width<input id="viz-width" type="number" min="480" max="3000" value="1000"></label><label>高度 Height<input id="viz-height" type="number" min="320" max="2200" value="620"></label>
              <label>點大小 Point Size<input id="viz-point-size" type="number" min="1" max="20" step="0.5" value="4"></label><label>透明度 Opacity<input id="viz-opacity" type="number" min="0.05" max="1" step="0.05" value="0.75"></label>
              <label>X 最小 X Min<input id="viz-x-min" type="number" step="any"></label><label>X 最大 X Max<input id="viz-x-max" type="number" step="any"></label>
              <label>Y 最小 Y Min<input id="viz-y-min" type="number" step="any"></label><label>Y 最大 Y Max<input id="viz-y-max" type="number" step="any"></label>
              <label>參考 X Reference X<input id="viz-ref-x" type="number" step="any"></label><label>參考 Y Reference Y<input id="viz-ref-y" type="number" step="any"></label>
              <label>Bar 方向 Bar Orientation<select id="viz-bar-orientation"><option value="vertical">直向 Vertical</option><option value="horizontal">橫向 Horizontal</option></select></label><label class="viz-checkbox"><input id="viz-stacked" type="checkbox"> 堆疊 Stacked</label>
              <label class="viz-checkbox"><input id="viz-legend" type="checkbox" checked> 圖例 Legend</label><label class="viz-checkbox"><input id="viz-data-labels" type="checkbox"> 資料標籤 Data Labels</label>
              <label class="viz-checkbox"><input id="viz-show-n" type="checkbox" checked> 樣本數 Show N</label><label class="viz-checkbox"><input id="viz-equal-axes" type="checkbox"> 等比例座標 Equal Axes</label>
            </div></fieldset>
            <fieldset><legend>保存 Save</legend><div class="stage4d-map-grid">
              <label class="stage4d-span">名稱 Name<input id="viz-save-name" type="text" placeholder="例如 Scherzer FC/SL Auto K"></label><label class="stage4d-span">備註 Notes<textarea id="viz-save-notes" rows="2"></textarea></label>
              <label>保存方式 Save Mode<select id="viz-save-mode"><option value="live">Live — 連結分析</option><option value="frozen">Frozen — 凍結這次結果</option></select></label>
              <label>Preset 名稱<input id="viz-preset-name" type="text"></label>
            </div><div class="button-row"><button id="viz-save">儲存視覺化 Save Visualization</button><button id="viz-save-preset">儲存為預設 Save Preset</button></div></fieldset>
          </div>
          <div class="stage4d-canvas-pane">
            <div class="viz-toolbar"><button id="viz-render">更新預覽 Render</button><button id="viz-svg">SVG</button><button id="viz-png">PNG</button><button id="viz-copy">複製圖片 Copy Image</button><span class="toolbar-separator"></span><select id="viz-data-export"><option>csv</option><option>json</option><option>xlsx</option><option>parquet</option></select><button id="viz-export-data">匯出資料 Export Data</button><select id="viz-report-format"><option>html</option><option>pdf</option></select><button id="viz-report">匯出報告 Export Report</button></div>
            <div class="stage4d-canvas-frame"><svg id="viz-canvas" width="1000" height="620" viewBox="0 0 1000 620" role="img" aria-label="Visualization preview"></svg></div>
            <div id="viz-status" class="viz-mini">請選擇資料。 Select a result source.</div>
            <fieldset><legend>來源資訊 Provenance</legend><div id="viz-provenance" class="viz-source-meta"><b>狀態 Status</b><span>尚未載入 Not loaded</span></div></fieldset>
            <fieldset><legend>已儲存視覺化 Saved Visualizations</legend><div id="viz-saved-list" class="viz-empty">讀取中 Loading…</div></fieldset>
            <fieldset><legend>自訂預設 User Presets</legend><div id="viz-user-presets" class="viz-empty">讀取中 Loading…</div></fieldset>
          </div>
        </div></div>`;
      main.insertBefore(panel,result);
      window.treepoloPanels?.register?.("visualization-panel","visualization");
    }
  }

  function outputPanelChanged(event) {
    const id=event.detail?.panelId;
    document.body.classList.toggle("output-panel-active",["visualization-panel","analysis-library-panel","analysis-history-panel"].includes(id));
  }

  function currentAnalysisSource() {
    const current=window.treepoloLastAnalysis;
    if(!current?.payload) return null;
    return {kind:"analysis_payload",payload:current.payload};
  }

  function sourceLabel(item,kind) {
    if(kind==="recent") return `${formatTime(item.created_at)} · ${modeLabel(item.mode)} · ${fmt(item.row_count)} rows`;
    if(kind==="history") return `#${item.id} · ${formatTime(item.created_at)} · ${modeLabel(item.mode)} · ${item.status}`;
    if(kind==="saved") return `#${item.id} · ${item.name} · ${modeLabel(item.payload?.mode)}`;
    if(kind==="visualization") return `#${item.id} · ${item.name} · ${item.save_mode}`;
    return String(item?.name||item?.id||"");
  }

  function sourceForSelection() {
    const kind=$("#viz-source-kind")?.value||"current";
    if(kind==="current") return currentAnalysisSource();
    const id=$("#viz-source-item")?.value;
    if(!id)return null;
    if(kind==="recent")return{kind,id};
    return{kind,id:Number(id)};
  }

  function refreshSourceItems() {
    const kind=$("#viz-source-kind").value,select=$("#viz-source-item");select.innerHTML="";
    let items=[];
    if(kind==="current"){
      const option=document.createElement("option");option.value="current";option.textContent=window.treepoloLastAnalysis?.payload?`${modeLabel(window.treepoloLastAnalysis.payload.mode)} · current`:`尚無目前分析 No current result`;select.append(option);select.disabled=true;return;
    }
    select.disabled=false;
    if(kind==="recent")items=state.catalog?.recent||[];
    if(kind==="history")items=(state.catalog?.history||[]).filter(item=>item.status==="success");
    if(kind==="saved")items=state.catalog?.saved||[];
    if(kind==="visualization")items=state.catalog?.visualizations||[];
    items.forEach(item=>{const option=document.createElement("option");option.value=kind==="recent"?item.token:item.id;option.textContent=sourceLabel(item,kind);select.append(option);});
    if(!items.length){const option=document.createElement("option");option.value="";option.textContent="沒有可用資料 No available data";select.append(option);}
  }

  function fieldOptions(metadata, allowEmpty=true) {
    const options=allowEmpty?['<option value="">不指定 None</option>']:[];
    (metadata||[]).forEach(item=>options.push(`<option value="${escapeHtml(item.name)}">${escapeHtml(item.label||item.name)} (${escapeHtml(item.name)})</option>`));
    return options.join("");
  }

  function setSelect(id,value) {
    const el=$(id);if(!el)return;const wanted=value==null?"":String(value);if(Array.from(el.options).some(option=>option.value===wanted))el.value=wanted;
  }

  function autoMappings(metadata,type) {
    const numeric=(metadata||[]).filter(item=>item.is_numeric&&!item.is_identifier).map(item=>item.name);
    const temporal=(metadata||[]).filter(item=>item.is_temporal).map(item=>item.name);
    const category=(metadata||[]).filter(item=>item.role==="category"||item.is_identifier).map(item=>item.name);
    const estimates=(metadata||[]).filter(item=>["estimate","measure","percentage","difference","sample_size"].includes(item.role)).map(item=>item.name);
    const lower=(metadata||[]).find(item=>item.role==="interval_lower")?.name||"";
    const upper=(metadata||[]).find(item=>item.role==="interval_upper")?.name||"";
    return {
      x:type==="scatter"?(numeric[0]||""):(temporal[0]||category[0]||numeric[0]||""),
      y:estimates.find(name=>name!==numeric[0])||numeric[1]||numeric[0]||"",
      series:category.find(name=>!["pitcher","batter","game_pk","pitch_uid"].includes(name))||"",
      label:category[0]||"", lower, upper,
    };
  }

  function fillMappings(metadata, preserve=false) {
    ["#viz-x","#viz-y","#viz-series","#viz-label","#viz-lower","#viz-upper"].forEach(id=>{const el=$(id),old=preserve?el.value:"";el.innerHTML=fieldOptions(metadata,true);if(old&&Array.from(el.options).some(o=>o.value===old))el.value=old;});
    if(!preserve){const mapping=autoMappings(metadata,$("#viz-type").value);Object.entries(mapping).forEach(([key,value])=>setSelect(`#viz-${key}`,value));}
  }

  function populateSections(prepared) {
    const select=$("#viz-section");const old=String(prepared.section_index??state.sectionIndex??0);select.innerHTML="";
    (prepared.sections||[]).forEach(item=>{const option=document.createElement("option");option.value=String(item.index);option.textContent=`${item.title} · ${fmt(item.row_count)} rows`;select.append(option);});
    select.value=Array.from(select.options).some(o=>o.value===old)?old:String(prepared.section_index||0);state.sectionIndex=Number(select.value||0);
  }

  function displayProvenance(prepared) {
    const host=$("#viz-provenance");const p=prepared?.provenance||{};const rows=[
      ["來源 Source",p.source_kind||"—"],["模式 Mode",modeLabel(prepared?.analysis_payload?.mode||p.mode)],["區段 Section",p.section_title||prepared?.section?.title||`#${prepared?.section_index??0}`],
      ["符合資料 Rows",fmt(p.row_count)],["目前資料 Loaded",fmt(prepared?.section?.rows?.length||0)],["資料層級 Grain",typeof p.grain==="object"?JSON.stringify(p.grain):p.grain||"—"],["執行器 Backend",p.backend||"—"],["資料版本 Data Revision",p.data_revision||"—"],["重新執行 Rerun",p.rerun?"是 Yes":"否 No"]
    ];host.innerHTML=rows.map(([k,v])=>`<b>${escapeHtml(k)}</b><span>${escapeHtml(v)}</span>`).join("");
  }

  function updateSamplingNote() {
    const host=$("#viz-sampling-note"),s=state.prepared?.sampling;if(!host)return;
    if(!s){host.className="viz-sampling-note";host.textContent="尚未載入資料 Data not loaded";return;}
    host.className=`viz-sampling-note${s.sampled?"":" full"}`;
    if(s.sampled)host.textContent=`已抽樣 Sampled: ${fmt(s.returned_rows)} / ${fmt(s.source_rows)} rows · ${s.method} · seed ${s.seed}`;
    else if(s.preview_only)host.textContent=`目前是保存／前端預覽 Preview only: ${fmt(s.returned_rows)} / ${fmt(s.source_rows)} rows；可按重新執行取得完整結果。`;
    else host.textContent=`完整繪圖資料 Full data: ${fmt(s.returned_rows)} rows`;
  }

  function samplingSpec() {
    return {mode:$("#viz-sampling").value,method:$("#viz-sample-method").value,size:Number($("#viz-sample-size").value||5000),seed:Number($("#viz-sample-seed").value||42)};
  }

  function presentationSpec() {
    return {version:"stage4d-v1",type:$("#viz-type").value,preset:$("#viz-preset").value||null,mapping:{x:$("#viz-x").value||null,y:$("#viz-y").value||null,series:$("#viz-series").value||null,label:$("#viz-label").value||null,lower:$("#viz-lower").value||null,upper:$("#viz-upper").value||null},sampling:samplingSpec(),display:{title:$("#viz-title").value,subtitle:$("#viz-subtitle").value,width:Number($("#viz-width").value||1000),height:Number($("#viz-height").value||620),point_size:Number($("#viz-point-size").value||4),opacity:Number($("#viz-opacity").value||.75),x_min:$("#viz-x-min").value===""?null:Number($("#viz-x-min").value),x_max:$("#viz-x-max").value===""?null:Number($("#viz-x-max").value),y_min:$("#viz-y-min").value===""?null:Number($("#viz-y-min").value),y_max:$("#viz-y-max").value===""?null:Number($("#viz-y-max").value),reference_x:$("#viz-ref-x").value===""?null:Number($("#viz-ref-x").value),reference_y:$("#viz-ref-y").value===""?null:Number($("#viz-ref-y").value),bar_orientation:$("#viz-bar-orientation").value,stacked:$("#viz-stacked").checked,legend:$("#viz-legend").checked,data_labels:$("#viz-data-labels").checked,show_n:$("#viz-show-n").checked,equal_axes:$("#viz-equal-axes").checked}};
  }

  async function loadCatalog() {
    state.catalog=await api("/api/visualization/sources");refreshSourceItems();populatePresets();renderSavedLibraries();
  }

  async function loadData(allowRerun=false) {
    const source=state.source||sourceForSelection();if(!source){setStatus("沒有可用的分析結果。 No analysis result is available.",true);return;}
    state.source=source;state.loading=true;setStatus(allowRerun?"正在重新執行並取得完整結果 Re-running full analysis…":"正在載入視覺化資料 Loading visualization data…");
    try{
      const request={source,section:Number($("#viz-section").value||state.sectionIndex||0),sampling:samplingSpec(),allow_rerun:allowRerun};
      if(source.kind==="analysis_payload"&&state.currentClientResult)request.client_result=state.currentClientResult;
      const prepared=await api("/api/visualization/data",{method:"POST",body:JSON.stringify(request)});
      if(!prepared.result_available){state.prepared=null;$("#viz-rerun").style.display=prepared.requires_rerun?"":"none";setStatus("這筆分析結果目前未保存。可重新執行以取得資料。 Result unavailable; re-run is required.",true);return;}
      state.prepared=prepared;state.sectionIndex=prepared.section_index;populateSections(prepared);fillMappings(prepared.field_metadata,false);displayProvenance(prepared);updateSamplingNote();$("#viz-rerun").style.display=prepared.requires_rerun?"":"none";
      chooseRecommendedPreset(prepared.recommendations||[]);render();setStatus(prepared.requires_rerun?"已載入預覽；完整繪圖／匯出可重新執行。 Preview loaded; re-run for full data.":"視覺化資料已載入 Visualization data loaded");
    }catch(error){setStatus(error.message,true);}finally{state.loading=false;}
  }

  function populatePresets() {
    const select=$("#viz-preset"),old=select.value;select.innerHTML='<option value="">自訂 Custom</option>';
    (state.catalog?.presets?.built_in||[]).forEach(p=>{const o=document.createElement("option");o.value=`builtin:${p.id}`;o.textContent=`內建 Built-in · ${p.name}`;select.append(o);});
    (state.catalog?.presets?.user||[]).forEach(p=>{const o=document.createElement("option");o.value=`user:${p.id}`;o.textContent=`自訂 User · ${p.name}`;select.append(o);});
    if(Array.from(select.options).some(o=>o.value===old))select.value=old;
  }

  function presetByValue(value) {
    if(!value)return null;const [kind,id]=value.split(":");
    if(kind==="builtin")return(state.catalog?.presets?.built_in||[]).find(p=>p.id===id)||null;
    return(state.catalog?.presets?.user||[]).find(p=>String(p.id)===id)?.spec||null;
  }

  function compatiblePreset(preset) {
    if(!preset||!state.prepared)return false;const fields=new Set(state.prepared.section?.columns||[]);return(preset.required_fields||[]).every(field=>fields.has(field));
  }

  function chooseRecommendedPreset(ids) {
    if(!ids.length)return;const built=state.catalog?.presets?.built_in||[];const id=ids.find(candidate=>built.some(p=>p.id===candidate&&compatiblePreset(p)));if(id){$("#viz-preset").value=`builtin:${id}`;applyPreset();}
  }

  function applyPreset() {
    const preset=presetByValue($("#viz-preset").value);if(!preset)return;
    if(preset.required_fields&&!compatiblePreset(preset)){setStatus("目前結果缺少此預設需要的欄位。 Required fields are missing for this preset.",true);return;}
    if(preset.type)$("#viz-type").value=preset.type;
    const mapping=preset.mapping||{};Object.entries(mapping).forEach(([key,value])=>setSelect(`#viz-${key}`,value));
    const display=preset.display||{};
    if(display.equal_axes!=null)$("#viz-equal-axes").checked=Boolean(display.equal_axes);
    if(display.reference_x!=null)$("#viz-ref-x").value=display.reference_x;
    if(display.reference_y!=null)$("#viz-ref-y").value=display.reference_y;
    render();
  }

  function svgEl(name,attrs={},text=null) {const el=document.createElementNS(SVG_NS,name);Object.entries(attrs).forEach(([k,v])=>{if(v!=null)el.setAttribute(k,String(v));});if(text!=null)el.textContent=String(text);return el;}
  function numeric(value){return typeof value==="number"&&Number.isFinite(value)?value:(value!==""&&value!=null&&Number.isFinite(Number(value))?Number(value):null);}
  function domain(values,pinnedMin=null,pinnedMax=null) {let min=pinnedMin!=null?pinnedMin:Math.min(...values),max=pinnedMax!=null?pinnedMax:Math.max(...values);if(!Number.isFinite(min)||!Number.isFinite(max)){min=0;max=1;}if(min===max){const pad=Math.abs(min||1)*.08;min-=pad;max+=pad;}const natural=(max-min)*.06;return[pinnedMin!=null?min:min-natural,pinnedMax!=null?max:max+natural];}
  function scale([d0,d1],[r0,r1]){return value=>r0+(Number(value)-d0)/(d1-d0)*(r1-r0);}
  function unique(values){return Array.from(new Set(values.map(value=>String(value??"—"))));}
  function colorFor(value,categories){const index=Math.max(0,categories.indexOf(String(value??"—")));return PALETTE[index%PALETTE.length];}
  function xAccessor(rows,field) {
    if(!field)return{values:rows.map((_,i)=>i),labels:rows.map((_,i)=>String(i+1)),numeric:true};
    const raw=rows.map(row=>row[field]);
    const numericValues=raw.map(numeric);if(numericValues.every(value=>value!=null))return{values:numericValues,labels:raw.map(fmt),numeric:true};
    const dates=raw.map(value=>Date.parse(String(value)));if(dates.every(Number.isFinite))return{values:dates,labels:raw.map(String),numeric:true,date:true};
    const categories=unique(raw);return{values:raw.map(value=>categories.indexOf(String(value??"—"))),labels:categories,numeric:false,categories};
  }
  function axisTicks(svg,xScale,yScale,xDomain,yDomain,plot,labels) {
    const [left,top,right,bottom]=plot;for(let i=0;i<=5;i++){const ratio=i/5,y=bottom-ratio*(bottom-top),value=yDomain[0]+ratio*(yDomain[1]-yDomain[0]);svg.append(svgEl("line",{x1:left,y1:y,x2:right,y2:y,class:"viz-gridline"}),svgEl("text",{x:left-6,y:y+4,"text-anchor":"end",class:"viz-label"},fmt(value)));}
    svg.append(svgEl("line",{x1:left,y1:bottom,x2:right,y2:bottom,class:"viz-axis"}),svgEl("line",{x1:left,y1:top,x2:left,y2:bottom,class:"viz-axis"}));
    if(labels?.length){const step=Math.max(1,Math.ceil(labels.length/8));labels.forEach((label,index)=>{if(index%step)return;const x=left+(labels.length===1?.5:index/Math.max(1,labels.length-1))*(right-left);svg.append(svgEl("text",{x,y:bottom+18,"text-anchor":"middle",class:"viz-label"},String(label).slice(0,18)));});}
    else for(let i=0;i<=5;i++){const ratio=i/5,x=left+ratio*(right-left),value=xDomain[0]+ratio*(xDomain[1]-xDomain[0]);svg.append(svgEl("text",{x,y:bottom+18,"text-anchor":"middle",class:"viz-label"},fmt(value)));}
  }

  function addBaseballOverlay(svg,spec,xScale,yScale,plot) {
    const preset=String(spec.preset||"");if(!preset.includes("pitch_location"))return;
    const [left,top,right,bottom]=plot;
    const x1=xScale(-17/24),x2=xScale(17/24),topY=yScale(3.5),botY=yScale(1.5);
    if([x1,x2,topY,botY].every(Number.isFinite))svg.append(svgEl("rect",{x:Math.min(x1,x2),y:Math.min(topY,botY),width:Math.abs(x2-x1),height:Math.abs(botY-topY),fill:"none",stroke:"#56616f","stroke-width":2}));
    const plateY=yScale(.15),plateX=xScale(0),half=Math.abs(xScale(17/24)-plateX);if([plateY,plateX,half].every(Number.isFinite))svg.append(svgEl("polygon",{points:`${plateX-half},${plateY} ${plateX+half},${plateY} ${plateX+half*.55},${plateY+8} ${plateX},${plateY+13} ${plateX-half*.55},${plateY+8}`,fill:"#f8f8f8",stroke:"#56616f"}));
  }

  function render() {
    const svg=$("#viz-canvas"),prepared=state.prepared;if(!svg)return;svg.replaceChildren();
    const spec=presentationSpec(),display=spec.display,mapping=spec.mapping,rows=(prepared?.section?.rows||[]).filter(row=>row&&typeof row==="object");
    const width=Math.max(480,display.width||1000),height=Math.max(320,display.height||620);svg.setAttribute("width",width);svg.setAttribute("height",height);svg.setAttribute("viewBox",`0 0 ${width} ${height}`);
    svg.append(svgEl("rect",{x:0,y:0,width,height,fill:"#ffffff"}));
    const title=display.title||prepared?.section?.title||"Visualization";svg.append(svgEl("text",{x:22,y:28,class:"viz-title"},title));if(display.subtitle)svg.append(svgEl("text",{x:22,y:48,class:"viz-subtitle"},display.subtitle));
    if(display.show_n&&prepared){const s=prepared.sampling||{};svg.append(svgEl("text",{x:width-20,y:27,"text-anchor":"end",class:"viz-subtitle"},s.sampled?`Sampled n=${fmt(s.returned_rows)} / ${fmt(s.source_rows)}`:`n=${fmt(s.returned_rows??rows.length)}`));}
    if(!rows.length){svg.append(svgEl("text",{x:width/2,y:height/2,"text-anchor":"middle",class:"viz-label"},"沒有可繪製資料 No plottable data"));return;}
    const yField=mapping.y;if(!yField){svg.append(svgEl("text",{x:width/2,y:height/2,"text-anchor":"middle",class:"viz-label"},"請指定 Y 軸。 Select a Y field."));return;}
    const yValues=rows.map(row=>numeric(row[yField])).filter(value=>value!=null);if(!yValues.length){svg.append(svgEl("text",{x:width/2,y:height/2,"text-anchor":"middle",class:"viz-label"},"Y 欄位沒有數值資料。 Y field is not numeric."));return;}
    const margin={left:75,right:display.legend?150:30,top:70,bottom:62};const plot=[margin.left,margin.top,width-margin.right,height-margin.bottom];
    const xInfo=xAccessor(rows,mapping.x);let xDomain=domain(xInfo.values,display.x_min,display.x_max),yDomain=domain(yValues,display.y_min,display.y_max);
    if(display.equal_axes&&xInfo.numeric){const span=Math.max(xDomain[1]-xDomain[0],yDomain[1]-yDomain[0]),xc=(xDomain[0]+xDomain[1])/2,yc=(yDomain[0]+yDomain[1])/2;xDomain=[xc-span/2,xc+span/2];yDomain=[yc-span/2,yc+span/2];}
    const xScale=scale(xDomain,[plot[0],plot[2]]),yScale=scale(yDomain,[plot[3],plot[1]]);
    const xLabels=xInfo.numeric?null:(xInfo.categories||[]);axisTicks(svg,xScale,yScale,xDomain,yDomain,plot,xLabels);addBaseballOverlay(svg,spec,xScale,yScale,plot);
    if(display.reference_x!=null&&display.reference_x>=xDomain[0]&&display.reference_x<=xDomain[1]){const x=xScale(display.reference_x);svg.append(svgEl("line",{x1:x,y1:plot[1],x2:x,y2:plot[3],class:"viz-reference"}));}
    if(display.reference_y!=null&&display.reference_y>=yDomain[0]&&display.reference_y<=yDomain[1]){const y=yScale(display.reference_y);svg.append(svgEl("line",{x1:plot[0],y1:y,x2:plot[2],y2:y,class:"viz-reference"}));}
    const seriesField=mapping.series,categories=seriesField?unique(rows.map(row=>row[seriesField])):["All"];
    const type=spec.type;
    if(type==="scatter")renderScatter(svg,rows,spec,xInfo,xScale,yScale,categories);
    else if(type==="line")renderLine(svg,rows,spec,xInfo,xScale,yScale,categories);
    else if(type==="bar"||type==="difference")renderBars(svg,rows,spec,xInfo,xScale,yScale,yDomain,categories,plot);
    else if(type==="range")renderRange(svg,rows,spec,xInfo,xScale,yScale,categories);
    else if(type==="dumbbell")renderDumbbell(svg,rows,spec,xInfo,xScale,yScale,categories);
    if(display.legend&&seriesField)renderLegend(svg,categories,width-margin.right+18,margin.top);
    svg.append(svgEl("text",{x:(plot[0]+plot[2])/2,y:height-18,"text-anchor":"middle",class:"viz-label"},mapping.x||"Row"));
    svg.append(svgEl("text",{x:18,y:(plot[1]+plot[3])/2,transform:`rotate(-90 18 ${(plot[1]+plot[3])/2})`,"text-anchor":"middle",class:"viz-label"},yField));
  }

  function pointTitle(row,spec){const m=spec.mapping;return[m.label&&row[m.label],m.x&&`${m.x}: ${fmt(row[m.x])}`,m.y&&`${m.y}: ${fmt(row[m.y])}`,m.series&&`${m.series}: ${fmt(row[m.series])}`].filter(Boolean).join(" · ");}
  function renderScatter(svg,rows,spec,xInfo,xScale,yScale,categories){const m=spec.mapping,d=spec.display;rows.forEach((row,index)=>{const y=numeric(row[m.y]),x=xInfo.values[index];if(y==null||!Number.isFinite(x))return;const selected=row.selected===true||row.selected===1;const c=svgEl("circle",{cx:xScale(x),cy:yScale(y),r:selected?d.point_size*1.8:d.point_size,fill:colorFor(m.series?row[m.series]:"All",categories),opacity:d.opacity,stroke:selected?"#111":"none","stroke-width":selected?1.5:0});c.append(svgEl("title",{},pointTitle(row,spec)));svg.append(c);if(d.data_labels&&m.label)svg.append(svgEl("text",{x:xScale(x)+5,y:yScale(y)-4,class:"viz-label"},String(row[m.label]??"")));});}
  function renderLine(svg,rows,spec,xInfo,xScale,yScale,categories){const m=spec.mapping,d=spec.display;categories.forEach(category=>{const points=[];rows.forEach((row,index)=>{if(m.series&&String(row[m.series]??"—")!==category)return;const y=numeric(row[m.y]),x=xInfo.values[index];if(y==null||!Number.isFinite(x))return;points.push({x,y,row});});points.sort((a,b)=>a.x-b.x);if(points.length>1)svg.append(svgEl("polyline",{points:points.map(p=>`${xScale(p.x)},${yScale(p.y)}`).join(" "),fill:"none",stroke:colorFor(category,categories),"stroke-width":2,opacity:d.opacity}));points.forEach(p=>{const selected=p.row.selected===true||p.row.selected===1;const c=svgEl("circle",{cx:xScale(p.x),cy:yScale(p.y),r:selected?5:Math.max(2,d.point_size*.8),fill:colorFor(category,categories),stroke:selected?"#111":"none"});c.append(svgEl("title",{},pointTitle(p.row,spec)));svg.append(c);});});}
  function renderBars(svg,rows,spec,xInfo,xScale,yScale,yDomain,categories,plot){const m=spec.mapping,d=spec.display;const zero=yScale(Math.max(yDomain[0],Math.min(yDomain[1],0)));const n=Math.max(1,rows.length),baseWidth=(plot[2]-plot[0])/n;const grouped=Math.max(1,categories.length);rows.forEach((row,index)=>{const y=numeric(row[m.y]);if(y==null)return;const cat=m.series?String(row[m.series]??"—"):"All",seriesIndex=categories.indexOf(cat);let x=xScale(xInfo.values[index]),barWidth=Math.max(2,baseWidth*.72);if(m.series&&!d.stacked){barWidth=Math.max(2,baseWidth*.72/grouped);x+=((seriesIndex-(grouped-1)/2)*barWidth);}const top=yScale(y);if(d.bar_orientation==="horizontal")return;const rect=svgEl("rect",{x:x-barWidth/2,y:Math.min(zero,top),width:barWidth,height:Math.max(1,Math.abs(zero-top)),fill:colorFor(cat,categories),opacity:d.opacity});rect.append(svgEl("title",{},pointTitle(row,spec)));svg.append(rect);if(d.data_labels)svg.append(svgEl("text",{x,y:top-4,"text-anchor":"middle",class:"viz-label"},fmt(y)));});}
  function renderRange(svg,rows,spec,xInfo,xScale,yScale,categories){const m=spec.mapping,d=spec.display;rows.forEach((row,index)=>{const y=numeric(row[m.y]),lo=numeric(row[m.lower]),hi=numeric(row[m.upper]),x=xScale(xInfo.values[index]);if(y==null)return;const color=colorFor(m.series?row[m.series]:"All",categories);if(lo!=null&&hi!=null){svg.append(svgEl("line",{x1:x,y1:yScale(lo),x2:x,y2:yScale(hi),stroke:color,"stroke-width":2}),svgEl("line",{x1:x-4,y1:yScale(lo),x2:x+4,y2:yScale(lo),stroke:color}),svgEl("line",{x1:x-4,y1:yScale(hi),x2:x+4,y2:yScale(hi),stroke:color}));}svg.append(svgEl("circle",{cx:x,cy:yScale(y),r:Math.max(3,d.point_size),fill:color}));});}
  function renderDumbbell(svg,rows,spec,xInfo,xScale,yScale,categories){const m=spec.mapping,d=spec.display;rows.forEach((row,index)=>{const current=numeric(row[m.y]),base=numeric(row[m.lower]),x=xScale(xInfo.values[index]);if(current==null||base==null)return;const color=colorFor(m.series?row[m.series]:"All",categories);svg.append(svgEl("line",{x1:x,y1:yScale(base),x2:x,y2:yScale(current),stroke:"#82909d","stroke-width":2}),svgEl("circle",{cx:x,cy:yScale(base),r:Math.max(3,d.point_size),fill:"#82909d"}),svgEl("circle",{cx:x,cy:yScale(current),r:Math.max(3,d.point_size),fill:color}));});}
  function renderLegend(svg,categories,x,y){categories.slice(0,18).forEach((category,index)=>{const yy=y+index*17;svg.append(svgEl("rect",{x,y:yy-9,width:10,height:10,fill:colorFor(category,categories)}),svgEl("text",{x:x+15,y:yy,class:"viz-legend"},String(category).slice(0,24)));});}

  function updateCurrentToolbar() {
    let toolbar=$("#result-window .result-save-toolbar");if(!toolbar)return;
    let host=toolbar.querySelector(".viz-result-toolbar");if(!host){host=document.createElement("div");host.className="viz-result-toolbar";host.innerHTML='<select id="result-output-section"><option value="0">Section 1</option></select><select id="result-export-format"><option>csv</option><option>json</option><option>xlsx</option><option>parquet</option></select><button id="result-export" type="button">匯出 Export</button><button id="result-open-viz" type="button">送至視覺化 Open in Visualization</button>';toolbar.append(host);host.querySelector("#result-export").addEventListener("click",exportCurrentResult);host.querySelector("#result-open-viz").addEventListener("click",openCurrentInVisualization);}
    const result=window.treepoloLastAnalysis?.result,select=host.querySelector("#result-output-section");select.innerHTML="";const sections=result?.sections||[result].filter(Boolean);sections.forEach((section,index)=>{const o=document.createElement("option");o.value=String(index);o.textContent=section?.title||`Section ${index+1}`;select.append(o);});host.querySelectorAll("button,select").forEach(el=>el.disabled=!window.treepoloLastAnalysis?.payload);
  }

  async function postDownload(path,request) {
    const response=await fetch(path,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(request)});if(!response.ok){let body={};try{body=await response.json();}catch{}throw new Error(body.error||`${response.status} ${response.statusText}`);}const blob=await response.blob();const disposition=response.headers.get("Content-Disposition")||"";const match=disposition.match(/filename="([^"]+)"/);const filename=match?.[1]||"download";const url=URL.createObjectURL(blob);const a=document.createElement("a");a.href=url;a.download=filename;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
  }

  async function exportCurrentResult(){const current=window.treepoloLastAnalysis;if(!current?.payload)return;const format=$("#result-export-format").value,section=Number($("#result-output-section").value||0);setStatus(`正在匯出 ${format.toUpperCase()}…`);try{await postDownload("/api/export",{source:{kind:"analysis_payload",payload:current.payload},section,format,name:`${current.payload.mode||"analysis"}-result`});setStatus("匯出完成 Export complete");}catch(error){setStatus(error.message,true);}}
  function openCurrentInVisualization(){const current=window.treepoloLastAnalysis;if(!current?.payload)return;state.source={kind:"analysis_payload",payload:current.payload};state.currentClientResult=current.result||null;state.sectionIndex=Number($("#result-output-section")?.value||0);$("#viz-source-kind").value="current";refreshSourceItems();window.treepoloPanels?.activate?.("visualization-panel",{updateUrl:true,source:"open-in-visualization"});$("#viz-section").value=String(state.sectionIndex);loadData(false);}

  function applySpec(spec) {
    if(!spec)return;$("#viz-type").value=spec.type||"scatter";if(spec.preset&&Array.from($("#viz-preset").options).some(o=>o.value===spec.preset))$("#viz-preset").value=spec.preset;
    Object.entries(spec.mapping||{}).forEach(([key,value])=>setSelect(`#viz-${key}`,value));const d=spec.display||{};
    const values={"#viz-title":d.title,"#viz-subtitle":d.subtitle,"#viz-width":d.width,"#viz-height":d.height,"#viz-point-size":d.point_size,"#viz-opacity":d.opacity,"#viz-x-min":d.x_min,"#viz-x-max":d.x_max,"#viz-y-min":d.y_min,"#viz-y-max":d.y_max,"#viz-ref-x":d.reference_x,"#viz-ref-y":d.reference_y,"#viz-bar-orientation":d.bar_orientation};Object.entries(values).forEach(([id,value])=>{if(value!=null)$(id).value=value;});
    [["#viz-stacked",d.stacked],["#viz-legend",d.legend],["#viz-data-labels",d.data_labels],["#viz-show-n",d.show_n],["#viz-equal-axes",d.equal_axes]].forEach(([id,value])=>{if(value!=null)$(id).checked=Boolean(value);});
    const s=spec.sampling||{};if(s.mode)$("#viz-sampling").value=s.mode;if(s.method)$("#viz-sample-method").value=s.method;if(s.size)$("#viz-sample-size").value=s.size;if(s.seed!=null)$("#viz-sample-seed").value=s.seed;
  }

  async function saveVisualization(){if(!state.source||!state.prepared){setStatus("請先載入資料。 Load data first.",true);return;}const name=$("#viz-save-name").value.trim();if(!name){setStatus("請輸入視覺化名稱。 Visualization name is required.",true);return;}const request={name,notes:$("#viz-save-notes").value,save_mode:$("#viz-save-mode").value,source:state.source,section:Number($("#viz-section").value||0),spec:presentationSpec()};try{const path=state.savedVisualizationId?`/api/visualizations/${state.savedVisualizationId}`:"/api/visualizations";const body=await api(path,{method:"POST",body:JSON.stringify(request)});state.savedVisualizationId=body.item?.id||null;setStatus("視覺化已儲存 Visualization saved");await loadCatalog();}catch(error){setStatus(error.message,true);}}
  async function savePreset(){const name=$("#viz-preset-name").value.trim();if(!name){setStatus("請輸入 Preset 名稱。 Preset name is required.",true);return;}try{await api("/api/visualization-presets",{method:"POST",body:JSON.stringify({name,spec:presentationSpec()})});setStatus("Preset 已儲存 Preset saved");await loadCatalog();}catch(error){setStatus(error.message,true);}}

  function renderSavedLibraries(){const saved=$("#viz-saved-list"),presets=$("#viz-user-presets");if(!saved||!presets)return;const vis=state.catalog?.visualizations||[];if(!vis.length)saved.innerHTML='<div class="viz-empty">尚未儲存視覺化。 No saved visualizations.</div>';else{const table=document.createElement("table");table.className="viz-library-table";table.innerHTML='<thead><tr><th>Name</th><th>Mode</th><th>Updated</th><th>Actions</th></tr></thead><tbody></tbody>';vis.forEach(item=>{const tr=document.createElement("tr");tr.innerHTML=`<td>${escapeHtml(item.name)}</td><td>${escapeHtml(item.save_mode)}</td><td>${escapeHtml(formatTime(item.updated_at))}</td><td></td>`;const td=tr.lastElementChild;const load=document.createElement("button");load.textContent="載入 Load";load.onclick=()=>loadSavedVisualization(item.id);const del=document.createElement("button");del.textContent="刪除 Delete";del.onclick=()=>deleteVisualization(item.id);td.append(load,del);table.tBodies[0].append(tr);});saved.replaceChildren(table);}
    const user=state.catalog?.presets?.user||[];if(!user.length)presets.innerHTML='<div class="viz-empty">尚無自訂 Preset。 No user presets.</div>';else{const table=document.createElement("table");table.className="viz-library-table";table.innerHTML='<thead><tr><th>Name</th><th>Type</th><th>Actions</th></tr></thead><tbody></tbody>';user.forEach(item=>{const tr=document.createElement("tr");tr.innerHTML=`<td>${escapeHtml(item.name)}</td><td>${escapeHtml(item.spec?.type||"—")}</td><td></td>`;const use=document.createElement("button");use.textContent="套用 Apply";use.onclick=()=>{$("#viz-preset").value=`user:${item.id}`;applyPreset();};const del=document.createElement("button");del.textContent="刪除 Delete";del.onclick=async()=>{await api(`/api/visualization-presets/${item.id}`,{method:"DELETE"});await loadCatalog();};tr.lastElementChild.append(use,del);table.tBodies[0].append(tr);});presets.replaceChildren(table);}}
  async function loadSavedVisualization(id){try{const body=await api(`/api/visualizations/${id}`);const item=body.item;if(!item)return;state.savedVisualizationId=item.id;state.source={kind:"visualization",id:item.id};$("#viz-save-name").value=item.name||"";$("#viz-save-notes").value=item.notes||"";$("#viz-save-mode").value=item.save_mode||"live";applySpec(item.spec);$("#viz-source-kind").value="visualization";refreshSourceItems();$("#viz-source-item").value=String(item.id);state.sectionIndex=item.section_index||0;await loadData(false);setStatus("已載入儲存的視覺化 Saved visualization loaded");}catch(error){setStatus(error.message,true);}}
  async function deleteVisualization(id){try{await api(`/api/visualizations/${id}`,{method:"DELETE"});if(state.savedVisualizationId===id)state.savedVisualizationId=null;await loadCatalog();}catch(error){setStatus(error.message,true);}}

  function serializeSvg(){const svg=$("#viz-canvas");const clone=svg.cloneNode(true);clone.setAttribute("xmlns",SVG_NS);return new XMLSerializer().serializeToString(clone);}
  function downloadBlob(blob,filename){const url=URL.createObjectURL(blob),a=document.createElement("a");a.href=url;a.download=filename;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);}
  function exportSvg(){downloadBlob(new Blob([serializeSvg()],{type:"image/svg+xml;charset=utf-8"}),"visualization.svg");}
  async function svgToPngBlob(){const svg=serializeSvg(),blob=new Blob([svg],{type:"image/svg+xml"}),url=URL.createObjectURL(blob);try{const image=new Image();await new Promise((resolve,reject)=>{image.onload=resolve;image.onerror=reject;image.src=url;});const canvas=document.createElement("canvas");canvas.width=Number($("#viz-width").value||1000);canvas.height=Number($("#viz-height").value||620);const ctx=canvas.getContext("2d");ctx.fillStyle="#fff";ctx.fillRect(0,0,canvas.width,canvas.height);ctx.drawImage(image,0,0,canvas.width,canvas.height);return await new Promise(resolve=>canvas.toBlob(resolve,"image/png"));}finally{URL.revokeObjectURL(url);}}
  async function exportPng(){const blob=await svgToPngBlob();if(blob)downloadBlob(blob,"visualization.png");}
  async function copyImage(){try{const blob=await svgToPngBlob();if(!blob||!navigator.clipboard||!window.ClipboardItem)throw new Error("Clipboard image API unavailable");await navigator.clipboard.write([new ClipboardItem({"image/png":blob})]);setStatus("圖片已複製 Image copied");}catch(error){setStatus(error.message,true);}}

  async function exportVisualizationData(){if(!state.source)return;try{await postDownload("/api/export",{source:state.source,section:Number($("#viz-section").value||0),format:$("#viz-data-export").value,name:$("#viz-save-name").value||"visualization-data"});setStatus("資料匯出完成 Data export complete");}catch(error){setStatus(error.message,true);}}
  async function exportReport(){if(!state.source)return;try{await postDownload("/api/report",{source:state.source,section:Number($("#viz-section").value||0),format:$("#viz-report-format").value,name:$("#viz-save-name").value||$("#viz-title").value||"analysis-report",spec:presentationSpec(),chart_svg:serializeSvg()});setStatus("報告匯出完成 Report export complete");}catch(error){setStatus(error.message,true);}}

  function bind() {
    $("#viz-source-kind").addEventListener("change",()=>{state.source=null;state.currentClientResult=null;refreshSourceItems();});
    $("#viz-load").addEventListener("click",()=>{state.source=sourceForSelection();state.currentClientResult=$("#viz-source-kind").value==="current"?window.treepoloLastAnalysis?.result:null;state.sectionIndex=Number($("#viz-section").value||0);state.savedVisualizationId=null;loadData(false);});
    $("#viz-rerun").addEventListener("click",()=>loadData(true));
    $("#viz-section").addEventListener("change",()=>{state.sectionIndex=Number($("#viz-section").value||0);loadData(false);});
    $("#viz-preset").addEventListener("change",applyPreset);$("#viz-type").addEventListener("change",()=>{if(state.prepared)fillMappings(state.prepared.field_metadata,true);render();});
    ["#viz-x","#viz-y","#viz-series","#viz-label","#viz-lower","#viz-upper","#viz-title","#viz-subtitle","#viz-width","#viz-height","#viz-point-size","#viz-opacity","#viz-x-min","#viz-x-max","#viz-y-min","#viz-y-max","#viz-ref-x","#viz-ref-y","#viz-bar-orientation","#viz-stacked","#viz-legend","#viz-data-labels","#viz-show-n","#viz-equal-axes"].forEach(id=>$(id).addEventListener("change",render));
    ["#viz-sampling","#viz-sample-method","#viz-sample-size","#viz-sample-seed"].forEach(id=>$(id).addEventListener("change",()=>state.prepared&&loadData(false)));
    $("#viz-render").addEventListener("click",render);$("#viz-svg").addEventListener("click",exportSvg);$("#viz-png").addEventListener("click",()=>exportPng().catch(error=>setStatus(error.message,true)));$("#viz-copy").addEventListener("click",copyImage);
    $("#viz-export-data").addEventListener("click",exportVisualizationData);$("#viz-report").addEventListener("click",exportReport);$("#viz-save").addEventListener("click",saveVisualization);$("#viz-save-preset").addEventListener("click",savePreset);
    document.addEventListener("treepolo:panel-activated",outputPanelChanged);document.addEventListener("treepolo:analysis-state-changed",()=>{updateCurrentToolbar();if($("#viz-source-kind")?.value==="current")refreshSourceItems();});
  }

  async function init() {
    injectStyles();injectPanels();restructureNavigation();bind();updateCurrentToolbar();
    try{await loadCatalog();setStatus("Stage 4D Visualization 就緒 Ready");}catch(error){setStatus(error.message,true);}
    const active=window.treepoloPanels?.activePanelId?.();document.body.classList.toggle("output-panel-active",["visualization-panel","analysis-library-panel","analysis-history-panel"].includes(active));
  }

  window.treepoloStage4D={openCurrent:openCurrentInVisualization,render,presentationSpec,loadCatalog};
  if(document.readyState==="loading")document.addEventListener("DOMContentLoaded",init,{once:true});else init();
})();