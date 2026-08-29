(() => {
  "use strict";

  if (window.treepoloFieldOptionLegalityV3) return;
  window.treepoloFieldOptionLegalityV3 = true;

  const schemaFields = new Map();
  const NUMERIC_AGGS = new Set(["avg", "sum", "median", "stddev_pop", "stddev_samp"]);
  const MULTI_INPUT_SELECTORS = [
    ".s4-groups", ".s4-partition", ".s4-order", ".s4-fields",
    "#s4-cluster-features", "#s4-cluster-ids", "#s4-cluster-partitions",
    "#s4-reg-independent", "#s4-boot-units", "#cc-entities", "#cc-features",
    ".ta-entity-fields", ".ta-percentile-partition",
  ];

  const norm = value => String(value ?? "").toLowerCase().replace(/\s+/g, " ").trim();
  const csv = value => String(value || "").split(",").map(item => item.trim()).filter(Boolean);
  const clone = item => ({ ...item, capabilities:new Set(item.capabilities || []) });

  function catalogDescriptors() {
    return (window.treepoloFieldCatalog?.fields?.() || []).map(field => ({
      value:field.name,
      label:window.treepoloFieldCatalog?.label?.(field.name) || field.name,
      type:String(field.type || "UNKNOWN").toUpperCase(),
      capabilities:new Set(field.capabilities || ["reference","filter","group","order","id"]),
    }));
  }

  function baseDescriptors() {
    if (schemaFields.size) return Array.from(schemaFields.values()).map(clone);
    return catalogDescriptors();
  }

  const asMap = items => new Map(items.map(item => [item.value, clone(item)]));
  function alias(value, type="UNKNOWN", capabilities=[]) {
    const caps = new Set(["reference","filter","group","order","id", ...capabilities]);
    if (["INTEGER","REAL","DOUBLE","FLOAT","DECIMAL","NUMERIC"].some(token => String(type).toUpperCase().includes(token))) caps.add("numeric");
    return { value, label:`前一步輸出 Prior-stage alias · ${value}`, type, capabilities:caps };
  }
  function typeFor(map, name) { return map.get(name)?.type || schemaFields.get(name)?.type || "UNKNOWN"; }
  function capsFor(map, name) { return new Set(map.get(name)?.capabilities || schemaFields.get(name)?.capabilities || []); }

  function metricAlias(row) {
    const fn=row.querySelector(".s4-metric-fn")?.value || "count";
    const field=row.querySelector(".s4-metric-field")?.value?.trim() || "";
    return row.querySelector(".s4-metric-alias")?.value?.trim() || (field ? `${fn}_${field}` : "row_count");
  }
  function metricDescriptor(row,map) {
    const fn=row.querySelector(".s4-metric-fn")?.value || "count";
    const field=row.querySelector(".s4-metric-field")?.value?.trim() || "";
    const name=metricAlias(row);
    if (fn === "count") return alias(name,"INTEGER",["numeric","trend_orderable"]);
    if (NUMERIC_AGGS.has(fn)) return alias(name,"REAL",["numeric","trend_orderable"]);
    const source=map.get(field);
    return source ? { ...clone(source), value:name, label:`前一步輸出 Prior-stage alias · ${name}` } : alias(name);
  }

  function applyStage(map,stage) {
    const kind=stage.querySelector(".s4-stage-kind")?.value || "";
    if (kind === "aggregate") {
      const out=new Map();
      csv(stage.querySelector(".s4-groups")?.value).forEach(name=>{if(map.has(name))out.set(name,clone(map.get(name)));});
      stage.querySelectorAll(".s4-metric-row").forEach(row=>{const d=metricDescriptor(row,map);if(d.value)out.set(d.value,d);});
      return out;
    }
    if (kind === "project") {
      const out=new Map();
      csv(stage.querySelector(".s4-fields")?.value).forEach(name=>{if(map.has(name))out.set(name,clone(map.get(name)));});
      return out;
    }
    if (kind === "derive") {
      const name=stage.querySelector(".s4-alias")?.value?.trim();
      if(name)map.set(name,alias(name,"REAL",["numeric","trend_orderable"]));
    } else if (kind === "rolling") {
      const fn=stage.querySelector(".s4-function")?.value || "avg";
      const sourceName=stage.querySelector(".s4-field")?.value?.trim() || "";
      const name=stage.querySelector(".s4-alias")?.value?.trim() || `rolling_${fn}_${sourceName || "rows"}`;
      if(fn === "count")map.set(name,alias(name,"INTEGER",["numeric","trend_orderable"]));
      else if(["avg","sum"].includes(fn))map.set(name,alias(name,"REAL",["numeric","trend_orderable"]));
      else if(map.has(sourceName))map.set(name,{...clone(map.get(sourceName)),value:name,label:`前一步輸出 Prior-stage alias · ${name}`});
    } else if (kind === "offset") {
      const sourceName=stage.querySelector(".s4-field")?.value?.trim() || "";
      const direction=stage.querySelector(".s4-direction")?.value || "lag";
      const name=stage.querySelector(".s4-alias")?.value?.trim() || `${direction}_${sourceName}`;
      const source=map.get(sourceName);if(name&&source)map.set(name,{...clone(source),value:name,label:`前一步輸出 Prior-stage alias · ${name}`});
    } else if (kind === "trend") {
      const sourceName=stage.querySelector(".s4-field")?.value?.trim() || "";
      const direction=stage.querySelector(".s4-direction")?.value || "up";
      const name=stage.querySelector(".s4-alias")?.value?.trim() || `consecutive_${direction}_${sourceName}`;
      if(name)map.set(name,alias(name,"INTEGER",["numeric","trend_orderable"]));
    } else if (kind === "rank") {
      const name=stage.querySelector(".s4-alias")?.value?.trim() || "rank";
      if(name)map.set(name,alias(name,"INTEGER",["numeric","trend_orderable"]));
    } else if (kind === "arsenal_signature") {
      const name=stage.querySelector(".ta-custom-alias")?.value?.trim() || "arsenal";
      if(name)map.set(name,alias(name,"TEXT"));
    } else if (kind === "pitch_role_select") {
      const name=stage.querySelector(".ta-custom-alias")?.value?.trim() || "selected_role_rank";
      if(name)map.set(name,alias(name,"INTEGER",["numeric","trend_orderable"]));
    } else if (kind === "pitch_role_annotate") {
      const name=stage.querySelector(".ta-custom-alias")?.value?.trim() || "selected_pitch_type";
      const pitch=Array.from(map.values()).find(item=>item.capabilities.has("canonical_pitch_type"));
      if(name)map.set(name,alias(name,pitch?.type || "TEXT",["pitch_classification","canonical_pitch_type"]));
    } else if (kind === "empirical_percentile") {
      const name=stage.querySelector(".ta-custom-alias")?.value?.trim() || "percentile";
      if(name)map.set(name,alias(name,"REAL",["numeric","trend_orderable"]));
    } else if (kind === "event_pattern_cohorts") {
      const name=stage.querySelector(".ta-cohort-alias")?.value?.trim() || "pattern_cohort";
      if(name)map.set(name,alias(name,"TEXT"));
    }
    return map;
  }

  function beforeStage(stage) {
    let map=asMap(baseDescriptors());
    if(!stage?.parentElement)return map;
    for(const sibling of Array.from(stage.parentElement.children)){
      if(sibling===stage)break;
      if(sibling.classList?.contains("s4-stage"))map=applyStage(map,sibling);
    }
    return map;
  }
  function afterPreparation(control) {
    let map=asMap(baseDescriptors());
    const panel=control.closest("#clustering-panel,#regression-panel,#bootstrap-panel");
    panel?.querySelectorAll(".s4-input-stage-list > .s4-stage").forEach(stage=>{map=applyStage(map,stage);});
    return map;
  }
  function availableMap(control) {
    const stage=control.closest(".s4-stage");
    if(stage)return beforeStage(stage);
    if(control.closest("#clustering-panel,#regression-panel,#bootstrap-panel")&&!control.closest(".s4-filter-row"))return afterPreparation(control);
    return asMap(baseDescriptors());
  }

  function withCapability(items,capability) {
    if(!schemaFields.size)return items;
    return items.filter(item=>item.capabilities.has(capability));
  }
  function compatible(map,sourceName) {
    const source=map.get(sourceName);if(!source)return Array.from(map.values());
    const sourceNumeric=source.capabilities.has("numeric");
    const sourceTemporal=source.capabilities.has("temporal");
    return Array.from(map.values()).filter(item=>{
      if(!schemaFields.size)return true;
      if(sourceNumeric)return item.capabilities.has("numeric");
      if(sourceTemporal)return item.capabilities.has("temporal");
      return !item.capabilities.has("numeric")&&!item.capabilities.has("temporal");
    });
  }

  function legalDescriptors(control) {
    const map=availableMap(control);const all=Array.from(map.values());
    const numeric=()=>withCapability(all,"numeric");
    if(control.matches(".cc-filter-field,.s4-filter-field,.condition-field"))return all;
    if(control.matches(".s4-metric-field")){
      const fn=control.closest(".s4-metric-row")?.querySelector(".s4-metric-fn")?.value || "count";
      return NUMERIC_AGGS.has(fn)?numeric():all;
    }
    if(control.matches(".s4-left,.s4-right-field"))return numeric();
    if(control.matches(".s4-field")){
      const stage=control.closest(".s4-stage");const kind=stage?.querySelector(".s4-stage-kind")?.value || "";
      if(kind==="rolling")return ["avg","sum"].includes(stage.querySelector(".s4-function")?.value || "avg")?numeric():all;
      if(kind==="trend")return withCapability(all,"trend_orderable");
      return all;
    }
    if(control.matches(".s4-value-field")){
      const source=control.closest(".s4-stage")?.querySelector(".s4-field")?.value?.trim() || "";return source?compatible(map,source):all;
    }
    if(control.matches(".ta-metric-cond-value-field")){
      const source=control.closest(".s4-metric-row")?.querySelector(".s4-metric-cond-field")?.value?.trim() || "";return source?compatible(map,source):all;
    }
    if(control.matches(".ta-pitch-field")){
      const kind=control.closest(".s4-stage")?.querySelector(".s4-stage-kind")?.value || "";
      return withCapability(all,kind==="arsenal_signature"?"pitch_classification":"canonical_pitch_type");
    }
    if(control.matches(".ta-value-field")){
      const stage=control.closest(".s4-stage");const metricKind=stage?.querySelector(".ta-role-kind")?.value || "usage_rate";const fn=stage?.querySelector(".ta-role-fn")?.value || "avg";
      return metricKind==="field_metric"&&fn==="count"?all:numeric();
    }
    if(control.matches(".ta-percentile-field,#s4-cluster-features,#s4-reg-dependent,#s4-reg-independent,#cc-features,#cc-selection-field,#cc-evaluation-field,#percentile-value"))return numeric();
    if(control.matches("#s4-boot-value"))return document.querySelector("#s4-boot-stat")?.value==="proportion"?all:numeric();
    if(control.matches(".metric-field")){
      const fn=control.closest(".metric-row")?.querySelector(".metric-function")?.value || "count";return NUMERIC_AGGS.has(fn)?numeric():all;
    }
    if(control.matches("#role-value-field")){
      const kind=document.querySelector("#role-metric-kind")?.value || "usage_rate";const fn=document.querySelector("#role-function")?.value || "avg";return kind==="field_metric"&&fn==="count"?all:numeric();
    }
    if(control.matches("#temporal-value"))return NUMERIC_AGGS.has(document.querySelector("#temporal-function")?.value || "avg")?numeric():all;
    if(control.matches("#cross-value"))return NUMERIC_AGGS.has(document.querySelector("#cross-function")?.value || "avg")?numeric():all;
    return all;
  }

  function filteredNative(select) {
    return select.matches(".metric-field,#role-value-field,#percentile-value,#temporal-value,#cross-value,#cc-selection-field,#cc-evaluation-field");
  }
  function rebuildSelect(select) {
    if(!filteredNative(select))return;
    const descriptors=legalDescriptors(select);const allowed=descriptors.map(item=>item.value);const previous=select.value;
    const allowEmpty=select.hasAttribute("data-allow-empty")||Array.from(select.options).some(option=>!option.value);
    const actual=Array.from(select.options).filter(option=>option.value).map(option=>option.value);
    if(actual.length===allowed.length&&actual.every((value,index)=>value===allowed[index]))return;
    select.innerHTML="";
    if(allowEmpty){const empty=document.createElement("option");empty.value="";empty.textContent=select.matches(".metric-field")?"不指定 None":"請選擇 Select Field";select.append(empty);}
    descriptors.forEach(item=>{const option=document.createElement("option");option.value=item.value;option.textContent=item.label;select.append(option);});
    if(allowed.includes(previous))select.value=previous;else if(!allowEmpty&&allowed.length)select.value=allowed[0];else select.value="";
  }

  function multiInput(input){return MULTI_INPUT_SELECTORS.some(selector=>input.matches(selector));}
  function tailToken(input,multi){if(!multi)return String(input.value||"").trim();return(String(input.value||"").split(",").pop()||"").trim().replace(/^[+-]/,"");}
  function setToken(input,value,multi,replaceTail){
    if(!multi)input.value=value;else{const raw=String(input.value||"").split(",");const tail=raw.at(-1)?.trim()||"";const prefix=replaceTail?((tail.match(/^[+-]/)||[""])[0]):"";let parts=raw.map(item=>item.trim()).filter(Boolean);if(replaceTail&&tail)parts=parts.slice(0,-1);if(!new Set(parts.map(item=>item.replace(/^[+-]/,""))).has(value))parts.push(`${prefix}${value}`);input.value=parts.join(",");}
    input.dispatchEvent(new Event("input",{bubbles:true}));input.dispatchEvent(new Event("change",{bubbles:true}));
  }
  function renderOwnedPopup(popup){
    const shell=popup.closest(".xp-edit-shell");const input=shell?.querySelector(":scope > input");const search=popup.querySelector(".xp-popup-search");const list=popup.querySelector(".xp-popup-list");if(!input||!search||!list)return;
    const multi=multiInput(input),query=norm(search.value),selected=new Set(String(input.value||"").split(",").map(item=>item.trim().replace(/^[+-]/,"")).filter(Boolean));
    const options=legalDescriptors(input).filter(item=>!query||norm(`${item.label} ${item.value}`).includes(query));list.innerHTML="";
    if(!options.length){const empty=document.createElement("div");empty.className="xp-popup-empty";empty.textContent="沒有合法項目 No legal matches";list.append(empty);return;}
    options.forEach(item=>{const button=document.createElement("button");button.type="button";button.className=`xp-popup-item${selected.has(item.value)?" xp-selected":""}`;button.textContent=item.label.includes(item.value)?item.label:`${item.label} (${item.value})`;button.addEventListener("mousedown",event=>event.preventDefault());button.addEventListener("click",()=>{const replaceTail=Boolean(search.value&&norm(search.value)===norm(tailToken(input,multi)));setToken(input,item.value,multi,replaceTail);if(!multi)popup.remove();else if(popup.isConnected)renderOwnedPopup(popup);});list.append(button);});
  }
  function ownPopup(popup){if(!popup?.closest(".xp-edit-shell")||popup.dataset.legalOwnedV3==="1")return;popup.dataset.legalOwnedV3="1";popup.querySelector(".xp-popup-search")?.addEventListener("input",()=>setTimeout(()=>popup.isConnected&&renderOwnedPopup(popup),0));renderOwnedPopup(popup);}
  function refresh(){document.querySelectorAll("select").forEach(rebuildSelect);document.querySelectorAll(".xp-edit-shell > .xp-popup").forEach(ownPopup);}

  async function loadMeta(){
    try{
      const response=await fetch("/api/meta",{cache:"no-store"});if(!response.ok)return;
      const meta=await response.json();schemaFields.clear();
      (meta.fields||[]).forEach(field=>schemaFields.set(field.name,{
        value:field.name,
        label:window.treepoloFieldCatalog?.label?.(field.name) || field.name,
        type:String(field.type||"UNKNOWN").toUpperCase(),
        capabilities:new Set(field.capabilities||[]),
      }));
      refresh();
    }
    catch{/* Availability still follows the pipeline; capability narrowing waits for metadata. */}
  }
  function relevantChange(target){return target?.matches?.("[data-multi-field],.metric-function,#role-metric-kind,#role-function,#temporal-function,#cross-function,.s4-metric-fn,.s4-function,#s4-boot-stat,.s4-stage-kind,.ta-role-kind,.ta-role-fn,.s4-groups,.s4-metric-field,.s4-metric-alias,.s4-alias,.s4-fields,.s4-field,.s4-metric-cond-field,.ta-custom-alias,.ta-cohort-alias");}
  function init(){
    refresh();loadMeta();document.addEventListener("change",event=>{if(relevantChange(event.target))setTimeout(refresh,0);});document.addEventListener("input",event=>{if(relevantChange(event.target))setTimeout(refresh,0);});document.addEventListener("treepolo:fields-updated",()=>setTimeout(()=>{refresh();loadMeta();},0));
    let queued=false;new MutationObserver(mutations=>{if(queued||!mutations.some(mutation=>mutation.addedNodes.length||mutation.removedNodes.length))return;queued=true;setTimeout(()=>{queued=false;refresh();},0);}).observe(document.body,{childList:true,subtree:true});
  }
  window.treepoloLegalFieldOptions={
    available:control=>legalDescriptors(control).map(item=>item.value),
    descriptors:control=>legalDescriptors(control).map(item=>({value:item.value,label:item.label||item.value})),
    refresh,
  };
  if(document.readyState==="loading")document.addEventListener("DOMContentLoaded",init,{once:true});else init();
})();
