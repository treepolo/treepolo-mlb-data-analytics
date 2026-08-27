(() => {
  "use strict";

  if (window.treepoloLegalFieldOptions) return;

  const state = { meta:null, queued:false };
  const NUMERIC_TYPES = /\b(?:INT|REAL|NUMERIC|DECIMAL|DOUBLE|FLOAT)\b/i;
  const $ = (s, root=document) => root.querySelector(s);
  const $$ = (s, root=document) => Array.from(root.querySelectorAll(s));
  const csv = value => String(value || "").split(",").map(x => x.trim().replace(/^[+-]/, "")).filter(Boolean);
  const norm = value => String(value || "").toLowerCase().replace(/\s+/g, " ").trim();

  function labelFromDom(name) {
    const source = $(`#basic-group option[value="${CSS.escape(name)}"]`);
    return source?.textContent || name;
  }

  function schemaMap() {
    const map = new Map();
    for (const field of state.meta?.fields || []) {
      map.set(field.name, { name:field.name, type:String(field.type || "TEXT").toUpperCase(), label:labelFromDom(field.name) });
    }
    return map;
  }

  function numeric(field) { return Boolean(field && NUMERIC_TYPES.test(field.type || "")); }
  function cloneMap(map) { return new Map(Array.from(map, ([k,v]) => [k, { ...v }])); }
  function addAlias(map, name, type="TEXT") {
    const alias=String(name || "").trim();
    if (!alias) return;
    map.set(alias, { name:alias, type, label:`前一步輸出 Prior-stage alias · ${alias}` });
  }

  function currentStageFields(control) {
    const stage = control.closest?.(".s4-stage");
    const list = stage?.parentElement;
    let fields = schemaMap();
    if (!stage || !list) return fields;
    for (const sibling of Array.from(list.children)) {
      if (sibling === stage) break;
      if (sibling.classList?.contains("s4-stage")) fields = applyStage(fields, sibling);
    }
    return fields;
  }

  function preparedFields(control) {
    const panel = control.closest?.(".panel");
    const prep = panel?.querySelector(".s4-input-stage-list");
    let fields = schemaMap();
    if (!prep || control.closest?.(".s4-stage")) return currentStageFields(control);
    for (const stage of Array.from(prep.children)) if (stage.classList?.contains("s4-stage")) fields = applyStage(fields, stage);
    return fields;
  }

  function metricType(row, fields) {
    const fn = row?.querySelector(".s4-metric-fn")?.value || "count";
    const source = row?.querySelector(".s4-metric-field")?.value?.trim() || "";
    if (fn === "count") return "INTEGER";
    if (["avg","sum","median","stddev_pop","stddev_samp"].includes(fn)) return "REAL";
    return fields.get(source)?.type || "REAL";
  }

  function applyStage(inputFields, stage) {
    const fields = cloneMap(inputFields);
    const kind = stage.querySelector(".s4-stage-kind")?.value || "";
    if (kind === "aggregate") {
      const out = new Map();
      for (const name of csv(stage.querySelector(".s4-groups")?.value)) if (fields.has(name)) out.set(name, fields.get(name));
      stage.querySelectorAll(".s4-metric-row").forEach(row => {
        const alias = row.querySelector(".s4-metric-alias")?.value?.trim();
        addAlias(out, alias, metricType(row, fields));
      });
      return out;
    }
    if (kind === "project") {
      const out = new Map();
      for (const name of csv(stage.querySelector(".s4-fields")?.value)) if (fields.has(name)) out.set(name, fields.get(name));
      return out;
    }
    if (kind === "derive") addAlias(fields, stage.querySelector(".s4-alias")?.value, "REAL");
    else if (kind === "rolling") {
      const fn=stage.querySelector(".s4-function")?.value || "avg";
      const source=stage.querySelector(".s4-field")?.value?.trim();
      addAlias(fields, stage.querySelector(".s4-alias")?.value, fn === "count" ? "INTEGER" : (fields.get(source)?.type || "REAL"));
    } else if (kind === "offset") {
      const source=stage.querySelector(".s4-field")?.value?.trim();
      addAlias(fields, stage.querySelector(".s4-alias")?.value, fields.get(source)?.type || "TEXT");
    } else if (kind === "trend") addAlias(fields, stage.querySelector(".s4-alias")?.value, "INTEGER");
    else if (kind === "rank") addAlias(fields, stage.querySelector(".s4-alias")?.value || "rank", "INTEGER");
    else if (kind === "arsenal_signature") addAlias(fields, stage.querySelector(".ta-custom-alias")?.value || "arsenal", "TEXT");
    else if (kind === "pitch_role_select") addAlias(fields, stage.querySelector(".ta-custom-alias")?.value || "selected_role_rank", "INTEGER");
    else if (kind === "pitch_role_annotate") addAlias(fields, stage.querySelector(".ta-custom-alias")?.value || "selected_pitch_type", "TEXT");
    else if (kind === "empirical_percentile") addAlias(fields, stage.querySelector(".ta-custom-alias")?.value || "percentile", "REAL");
    else if (kind === "event_pattern_cohorts") addAlias(fields, stage.querySelector(".ta-cohort-alias")?.value || "pattern_cohort", "TEXT");
    return fields;
  }

  function fieldScope(control) {
    if (control.closest?.(".s4-stage")) return currentStageFields(control);
    if (control.closest?.("#clustering-panel,#regression-panel,#bootstrap-panel")) return preparedFields(control);
    return schemaMap();
  }

  function role(control) {
    if (control.matches?.("#s4-cluster-features,#s4-reg-dependent,#s4-reg-independent,#s4-boot-value,#cc-selection-field,#cc-evaluation-field,#cc-features,.ta-percentile-field")) return "numeric";
    if (control.matches?.(".s4-left,.s4-right-field")) return "numeric";
    if (control.matches?.(".ta-pitch-field")) {
      const kind=control.closest(".s4-stage")?.querySelector(".s4-stage-kind")?.value;
      return kind === "pitch_role_select" || kind === "pitch_role_annotate" ? "pitch_type" : "text";
    }
    if (control.matches?.(".s4-metric-field")) {
      const fn=control.closest(".s4-metric-row")?.querySelector(".s4-metric-fn")?.value || "count";
      return fn === "count" ? "any" : "numeric";
    }
    if (control.matches?.(".ta-value-field")) {
      const fn=control.closest(".s4-stage")?.querySelector(".ta-role-fn")?.value || "avg";
      return fn === "count" ? "any" : "numeric";
    }
    if (control.matches?.("#role-value-field")) return $("#role-function")?.value === "count" ? "any" : "numeric";
    if (control.matches?.("#temporal-value")) return $("#temporal-function")?.value === "count" ? "any" : "numeric";
    if (control.matches?.("#cross-value")) return $("#cross-function")?.value === "count" ? "any" : "numeric";
    if (control.matches?.(".metric-field")) return control.closest(".metric-row")?.querySelector(".metric-function")?.value === "count" ? "any" : "numeric";
    if (control.matches?.("#percentile-value")) return "numeric";
    return "any";
  }

  function legalFields(control) {
    let fields = Array.from(fieldScope(control).values());
    const r=role(control);
    if (r === "numeric") fields = fields.filter(numeric);
    else if (r === "text") fields = fields.filter(f => !numeric(f));
    else if (r === "pitch_type") fields = fields.filter(f => (state.meta?.field_contracts?.pitch_type_fields || []).includes(f.name));
    return fields;
  }

  function optionNode(field) {
    const option=document.createElement("option");
    option.value=field.name; option.textContent=field.label || field.name; return option;
  }

  function refreshSelect(select) {
    if (!select || select.multiple && select.id === "basic-group") return;
    const legal=legalFields(select); if (!legal.length && !state.meta) return;
    const prior=select.multiple ? new Set(Array.from(select.selectedOptions).map(o=>o.value)) : new Set([select.value]);
    const allowEmpty=Array.from(select.options).some(o=>!o.value) || select.hasAttribute("data-allow-empty");
    select.innerHTML="";
    if (allowEmpty) { const empty=document.createElement("option");empty.value="";empty.textContent="不指定 None";select.append(empty); }
    legal.forEach(field => { const o=optionNode(field); o.selected=prior.has(field.name); select.append(o); });
    if (!select.multiple && prior.size && !Array.from(select.options).some(o=>prior.has(o.value))) select.value="";
    if (select.multiple) select.dispatchEvent(new CustomEvent("treepolo:checklist-refresh", { bubbles:false }));
  }

  function display(field) { return field.label?.includes(field.name) ? field.label : `${field.label || field.name} (${field.name})`; }
  function closeOwn(shell) { shell.querySelectorAll(":scope > .legal-field-popup").forEach(n=>n.remove()); }
  function setToken(input, value, multi, fromTyping=false) {
    if (!multi) input.value=value;
    else {
      const raw=String(input.value||"").split(",");
      const tail=raw.at(-1)?.trim() || "";
      const prefix=fromTyping ? ((tail.match(/^[+-]/)||[""])[0]) : "";
      let parts=raw.map(x=>x.trim()).filter(Boolean);
      if (fromTyping && tail) parts=parts.slice(0,-1);
      if (!new Set(parts.map(x=>x.replace(/^[+-]/,""))).has(value)) parts.push(prefix+value);
      input.value=parts.join(",");
    }
    input.dispatchEvent(new Event("input",{bubbles:true}));
    input.dispatchEvent(new Event("change",{bubbles:true}));
  }
  function token(input, multi) { const raw=String(input.value||""); return (multi ? (raw.split(",").pop()||"") : raw).trim().replace(/^[+-]/,""); }
  function openInputPopup(input, shell, multi, fromTyping=false) {
    closeOwn(shell);
    const popup=document.createElement("div");popup.className="xp-popup legal-field-popup";
    const search=document.createElement("input");search.type="text";search.className="xp-popup-search";search.placeholder="搜尋合法欄位 Search legal fields";search.value=fromTyping?token(input,multi):"";
    const list=document.createElement("div");list.className="xp-popup-list";popup.append(search,list);shell.append(popup);
    const render=()=>{
      const q=norm(search.value);const fields=legalFields(input).filter(f=>!q||norm(`${f.label} ${f.name}`).includes(q));list.innerHTML="";
      if(!fields.length){const e=document.createElement("div");e.className="xp-popup-empty";e.textContent="沒有合法欄位 No legal fields";list.append(e);return;}
      fields.forEach(field=>{const b=document.createElement("button");b.type="button";b.className="xp-popup-item";b.textContent=display(field);b.addEventListener("mousedown",e=>e.preventDefault());b.addEventListener("click",()=>{setToken(input,field.name,multi,fromTyping);if(multi)render();else popup.remove();});list.append(b);});
    };
    search.addEventListener("input",render);search.addEventListener("keydown",e=>{if(e.key==="Escape")popup.remove();if(e.key==="Enter"){const first=list.querySelector(".xp-popup-item");if(first){e.preventDefault();first.click();}}});render();search.focus();
  }

  const TEXT_RULES = [
    [".s4-groups",true],[".s4-metric-field",false],[".s4-metric-cond-field",false],[".s4-left",false],[".s4-right-field",false],
    [".s4-field",false],[".s4-value-field",false],[".s4-partition",true],[".s4-order",true],[".s4-fields",true],
    ["#s4-cluster-features",true],["#s4-cluster-ids",true],["#s4-cluster-partitions",true],["#s4-reg-dependent",false],["#s4-reg-independent",true],
    ["#s4-boot-value",false],["#s4-boot-units",true],["#s4-boot-group",false],["#cc-entities",true],["#cc-features",true],
    [".ta-entity-fields",true],[".ta-pitch-field",false],[".ta-value-field",false],[".ta-percentile-field",false],[".ta-percentile-partition",true],
    [".ta-event-field",false],[".ta-metric-cond-value-field",false],
  ];

  function decorateInput(input, multi) {
    if (!input || input.dataset.legalFieldControl === "1") return;
    input.dataset.legalFieldControl="1";
    input.dataset.xpFieldCombo="1"; // reserve this input so the older generic decorator cannot attach an all-fields popup.
    input.removeAttribute("list");
    const datalist=input.nextElementSibling;if(datalist?.tagName==="DATALIST")datalist.remove();
    let shell=input.closest(".xp-edit-shell");
    if(!shell){shell=document.createElement("span");shell.className="xp-edit-shell legal-field-shell";input.parentNode.insertBefore(shell,input);shell.append(input);}
    if(shell.querySelector(":scope > .legal-field-button")) return;
    const button=document.createElement("button");button.type="button";button.className="xp-combo-button legal-field-button";button.textContent="▼";button.title="顯示合法欄位 Show legal fields";shell.append(button);
    button.addEventListener("click",e=>{e.preventDefault();e.stopPropagation();const existing=shell.querySelector(":scope > .legal-field-popup");if(existing)existing.remove();else openInputPopup(input,shell,multi,false);});
    input.addEventListener("input",()=>{const t=token(input,multi);if(!t)return;let p=shell.querySelector(":scope > .legal-field-popup");if(!p){openInputPopup(input,shell,multi,true);p=shell.querySelector(":scope > .legal-field-popup");}const q=p?.querySelector(".xp-popup-search");if(q&&q.value!==t){q.value=t;q.dispatchEvent(new Event("input"));}});
    input.addEventListener("blur",()=>{const legal=new Set(legalFields(input).map(f=>f.name));const tokens=csv(input.value);input.classList.toggle("ta-invalid",tokens.some(name=>!legal.has(name)));});
  }

  function scan(root=document) {
    if(!state.meta)return;
    root.querySelectorAll?.('select[data-field-select],.s4-field-select,.cc-field,.cc-filter-field').forEach(refreshSelect);
    TEXT_RULES.forEach(([selector,multi])=>root.querySelectorAll?.(selector).forEach(input=>decorateInput(input,multi)));
  }

  function schedule(root=document) {
    if(state.queued)return;state.queued=true;setTimeout(()=>{state.queued=false;scan(root);},0);
  }

  async function init() {
    try { const response=await fetch('/api/meta',{cache:'no-store'}); if(response.ok) state.meta=await response.json(); } catch {}
    if(!state.meta)return;
    scan(document);
    document.addEventListener('treepolo:fields-updated',()=>schedule(document));
    document.addEventListener('change',e=>{
      if(e.target.matches?.('.s4-stage-kind,.s4-metric-fn,.s4-function,.ta-role-fn,#role-function,#temporal-function,#cross-function')) schedule(e.target.closest('.panel')||document);
    });
    new MutationObserver(ms=>{if(ms.some(m=>m.addedNodes.length))schedule(document);}).observe(document.body,{childList:true,subtree:true});
  }

  window.treepoloLegalFieldOptions = { legalFields, applyStage, schemaMap };
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init,{once:true});else init();
})();
