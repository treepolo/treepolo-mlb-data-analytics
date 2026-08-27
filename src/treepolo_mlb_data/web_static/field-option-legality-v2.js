(() => {
  "use strict";

  if (window.treepoloFieldOptionLegalityV2) return;
  window.treepoloFieldOptionLegalityV2 = true;

  const schemaTypes = new Map();
  const NUMERIC_SQL = /\b(INT|REAL|DOUBLE|FLOAT|DECIMAL|NUMERIC)\b/i;
  const NUMERIC_AGGS = new Set(["avg", "sum", "median", "stddev_pop", "stddev_samp"]);
  const ORDERABLE_TEXT_FIELDS = new Set(["game_date"]);
  const MULTI_INPUT_SELECTORS = [
    ".s4-groups", ".s4-partition", ".s4-order", ".s4-fields",
    "#s4-cluster-features", "#s4-cluster-ids", "#s4-cluster-partitions",
    "#s4-reg-independent", "#s4-boot-units", "#cc-entities", "#cc-features",
    ".ta-entity-fields", ".ta-percentile-partition",
  ];

  const norm = value => String(value ?? "").toLowerCase().replace(/\s+/g, " ").trim();
  const csv = value => String(value || "").split(",").map(item => item.trim()).filter(Boolean);

  function baseDescriptors() {
    const source = document.querySelector("#basic-group");
    return source ? Array.from(source.options).filter(option => option.value).map(option => ({
      value: option.value,
      label: option.textContent || option.value,
      type: schemaTypes.get(option.value) || "UNKNOWN",
    })) : [];
  }

  const asMap = items => new Map(items.map(item => [item.value, { ...item }]));
  const alias = (value, type = "UNKNOWN") => ({ value, label:`前一步輸出 Prior-stage alias · ${value}`, type });

  function family(item) {
    if (!item || !item.type || item.type === "UNKNOWN") return "unknown";
    if (NUMERIC_SQL.test(item.type)) return "numeric";
    if (ORDERABLE_TEXT_FIELDS.has(item.value)) return "temporal";
    if (/BOOL/i.test(item.type)) return "boolean";
    return "text";
  }

  function typeFor(map, name) { return map.get(name)?.type || schemaTypes.get(name) || "UNKNOWN"; }
  function numericOnly(items) {
    if (!schemaTypes.size) return items;
    return items.filter(item => family(item) === "numeric" || family(item) === "unknown");
  }
  function trendCompatible(items) {
    if (!schemaTypes.size) return items;
    return items.filter(item => ["numeric", "temporal", "unknown"].includes(family(item)));
  }

  function metricAlias(row) {
    const fn = row.querySelector(".s4-metric-fn")?.value || "count";
    const field = row.querySelector(".s4-metric-field")?.value?.trim() || "";
    return row.querySelector(".s4-metric-alias")?.value?.trim() || (field ? `${fn}_${field}` : "row_count");
  }

  function metricType(row, map) {
    const fn = row.querySelector(".s4-metric-fn")?.value || "count";
    const field = row.querySelector(".s4-metric-field")?.value?.trim() || "";
    if (fn === "count") return "INTEGER";
    if (NUMERIC_AGGS.has(fn)) return "REAL";
    return typeFor(map, field);
  }

  function applyStage(map, stage) {
    const kind = stage.querySelector(".s4-stage-kind")?.value || "";
    if (kind === "aggregate") {
      const out = new Map();
      csv(stage.querySelector(".s4-groups")?.value).forEach(name => { if (map.has(name)) out.set(name, map.get(name)); });
      stage.querySelectorAll(".s4-metric-row").forEach(row => {
        const name = metricAlias(row);
        if (name) out.set(name, alias(name, metricType(row, map)));
      });
      return out;
    }
    if (kind === "derive") {
      const name = stage.querySelector(".s4-alias")?.value?.trim();
      if (name) map.set(name, alias(name, "REAL"));
    } else if (kind === "rolling") {
      const fn = stage.querySelector(".s4-function")?.value || "avg";
      const field = stage.querySelector(".s4-field")?.value?.trim() || "";
      const name = stage.querySelector(".s4-alias")?.value?.trim() || `rolling_${fn}_${field || "rows"}`;
      const type = fn === "count" ? "INTEGER" : (["avg", "sum"].includes(fn) ? "REAL" : typeFor(map, field));
      if (name) map.set(name, alias(name, type));
    } else if (kind === "offset") {
      const field = stage.querySelector(".s4-field")?.value?.trim() || "";
      const direction = stage.querySelector(".s4-direction")?.value || "lag";
      const name = stage.querySelector(".s4-alias")?.value?.trim() || `${direction}_${field}`;
      if (name) map.set(name, alias(name, typeFor(map, field)));
    } else if (kind === "trend") {
      const field = stage.querySelector(".s4-field")?.value?.trim() || "";
      const direction = stage.querySelector(".s4-direction")?.value || "up";
      const name = stage.querySelector(".s4-alias")?.value?.trim() || `consecutive_${direction}_${field}`;
      if (name) map.set(name, alias(name, "INTEGER"));
    } else if (kind === "rank") {
      const name = stage.querySelector(".s4-alias")?.value?.trim() || "rank";
      if (name) map.set(name, alias(name, "INTEGER"));
    } else if (kind === "project") {
      const out = new Map();
      csv(stage.querySelector(".s4-fields")?.value).forEach(name => { if (map.has(name)) out.set(name, map.get(name)); });
      return out;
    } else if (kind === "arsenal_signature") {
      const name = stage.querySelector(".ta-custom-alias")?.value?.trim() || "arsenal";
      map.set(name, alias(name, "TEXT"));
    } else if (kind === "pitch_role_select") {
      const name = stage.querySelector(".ta-custom-alias")?.value?.trim() || "selected_role_rank";
      map.set(name, alias(name, "INTEGER"));
    } else if (kind === "pitch_role_annotate") {
      const name = stage.querySelector(".ta-custom-alias")?.value?.trim() || "selected_pitch_type";
      map.set(name, alias(name, "TEXT"));
    } else if (kind === "empirical_percentile") {
      const name = stage.querySelector(".ta-custom-alias")?.value?.trim() || "percentile";
      map.set(name, alias(name, "REAL"));
    } else if (kind === "event_pattern_cohorts") {
      const name = stage.querySelector(".ta-cohort-alias")?.value?.trim() || "pattern_cohort";
      map.set(name, alias(name, "TEXT"));
    }
    return map;
  }

  function beforeStage(stage) {
    let map = asMap(baseDescriptors());
    if (!stage?.parentElement) return map;
    for (const sibling of Array.from(stage.parentElement.children)) {
      if (sibling === stage) break;
      if (sibling.classList?.contains("s4-stage")) map = applyStage(map, sibling);
    }
    return map;
  }

  function afterPreparation(control) {
    let map = asMap(baseDescriptors());
    const panel = control.closest("#clustering-panel,#regression-panel,#bootstrap-panel");
    panel?.querySelectorAll(".s4-input-stage-list > .s4-stage").forEach(stage => { map = applyStage(map, stage); });
    return map;
  }

  function availableMap(control) {
    const stage = control.closest(".s4-stage");
    if (stage) return beforeStage(stage);
    if (control.closest("#clustering-panel,#regression-panel,#bootstrap-panel") && !control.closest(".s4-filter-row")) return afterPreparation(control);
    return asMap(baseDescriptors());
  }

  function compatible(map, sourceName) {
    const source = map.get(sourceName);
    if (!source) return Array.from(map.values());
    const sourceFamily = family(source);
    if (sourceFamily === "unknown") return Array.from(map.values());
    return Array.from(map.values()).filter(item => {
      const candidate = family(item);
      return candidate === "unknown" || candidate === sourceFamily || (sourceFamily === "numeric" && candidate === "numeric");
    });
  }

  function legalDescriptors(control) {
    const map = availableMap(control);
    const all = Array.from(map.values());
    const numeric = () => numericOnly(all);

    if (control.matches(".s4-metric-field")) {
      const fn = control.closest(".s4-metric-row")?.querySelector(".s4-metric-fn")?.value || "count";
      return NUMERIC_AGGS.has(fn) ? numeric() : all;
    }
    if (control.matches(".s4-left,.s4-right-field")) return numeric();
    if (control.matches(".s4-field")) {
      const stage = control.closest(".s4-stage");
      const kind = stage?.querySelector(".s4-stage-kind")?.value || "";
      if (kind === "rolling") {
        const fn = stage.querySelector(".s4-function")?.value || "avg";
        return ["avg", "sum"].includes(fn) ? numeric() : all;
      }
      if (kind === "trend") return trendCompatible(all);
      return all;
    }
    if (control.matches(".s4-value-field")) {
      const source = control.closest(".s4-stage")?.querySelector(".s4-field")?.value?.trim() || "";
      return source ? compatible(map, source) : all;
    }
    if (control.matches(".ta-metric-cond-value-field")) {
      const source = control.closest(".s4-metric-row")?.querySelector(".s4-metric-cond-field")?.value?.trim() || "";
      return source ? compatible(map, source) : all;
    }
    if (control.matches(".ta-pitch-field")) {
      const kind = control.closest(".s4-stage")?.querySelector(".s4-stage-kind")?.value || "";
      const names = kind === "arsenal_signature" ? new Set(["pitch_type", "pitch_name"]) : new Set(["pitch_type"]);
      return all.filter(item => names.has(item.value));
    }
    if (control.matches(".ta-value-field")) {
      const stage = control.closest(".s4-stage");
      const metricKind = stage?.querySelector(".ta-role-kind")?.value || "usage_rate";
      const fn = stage?.querySelector(".ta-role-fn")?.value || "avg";
      return metricKind === "field_metric" && fn === "count" ? all : numeric();
    }
    if (control.matches(".ta-percentile-field")) return numeric();
    if (control.matches("#s4-cluster-features,#s4-reg-dependent,#s4-reg-independent,#cc-features")) return numeric();
    if (control.matches("#s4-boot-value")) return document.querySelector("#s4-boot-stat")?.value === "proportion" ? all : numeric();
    if (control.matches("#cc-selection-field,#cc-evaluation-field,.cc-field")) return numeric();
    if (control.matches(".metric-field")) {
      const fn = control.closest(".metric-row")?.querySelector(".metric-function")?.value || "count";
      return NUMERIC_AGGS.has(fn) ? numeric() : all;
    }
    if (control.matches("#role-value-field")) {
      const kind = document.querySelector("#role-metric-kind")?.value || "usage_rate";
      const fn = document.querySelector("#role-function")?.value || "avg";
      return kind === "field_metric" && fn === "count" ? all : numeric();
    }
    if (control.matches("#percentile-value")) return numeric();
    if (control.matches("#temporal-value")) {
      const fn = document.querySelector("#temporal-function")?.value || "avg";
      return NUMERIC_AGGS.has(fn) ? numeric() : all;
    }
    if (control.matches("#cross-value")) {
      const fn = document.querySelector("#cross-function")?.value || "avg";
      return NUMERIC_AGGS.has(fn) ? numeric() : all;
    }
    return all;
  }

  function filteredNative(select) {
    return select.matches(".metric-field,#role-value-field,#percentile-value,#temporal-value,#cross-value,.cc-field");
  }

  function rebuildSelect(select) {
    if (!filteredNative(select)) return;
    const descriptors = legalDescriptors(select);
    const allowed = descriptors.map(item => item.value);
    const previous = select.value;
    const allowEmpty = select.hasAttribute("data-allow-empty") || Array.from(select.options).some(option => !option.value);
    const actual = Array.from(select.options).filter(option => option.value).map(option => option.value);
    if (actual.length === allowed.length && actual.every((value, index) => value === allowed[index])) return;

    select.innerHTML = "";
    if (allowEmpty) {
      const empty = document.createElement("option");
      empty.value = "";
      empty.textContent = select.matches(".metric-field") ? "不指定 None" : "請選擇 Select Field";
      select.append(empty);
    }
    descriptors.forEach(item => {
      const option = document.createElement("option");
      option.value = item.value;
      option.textContent = item.label;
      select.append(option);
    });
    if (allowed.includes(previous)) select.value = previous;
    else if (!allowEmpty && allowed.length) select.value = allowed[0];
    else select.value = "";
  }

  function multiInput(input) { return MULTI_INPUT_SELECTORS.some(selector => input.matches(selector)); }
  function tailToken(input, multi) {
    if (!multi) return String(input.value || "").trim();
    return (String(input.value || "").split(",").pop() || "").trim().replace(/^[+-]/, "");
  }

  function setToken(input, value, multi, replaceTail) {
    if (!multi) input.value = value;
    else {
      const raw = String(input.value || "").split(",");
      const tail = raw.at(-1)?.trim() || "";
      const prefix = replaceTail ? ((tail.match(/^[+-]/) || [""])[0]) : "";
      let parts = raw.map(item => item.trim()).filter(Boolean);
      if (replaceTail && tail) parts = parts.slice(0, -1);
      if (!new Set(parts.map(item => item.replace(/^[+-]/, ""))).has(value)) parts.push(`${prefix}${value}`);
      input.value = parts.join(",");
    }
    input.dispatchEvent(new Event("input", { bubbles:true }));
    input.dispatchEvent(new Event("change", { bubbles:true }));
  }

  function renderOwnedPopup(popup) {
    const shell = popup.closest(".xp-edit-shell");
    const input = shell?.querySelector(":scope > input");
    const search = popup.querySelector(".xp-popup-search");
    const list = popup.querySelector(".xp-popup-list");
    if (!input || !search || !list) return;
    const multi = multiInput(input);
    const query = norm(search.value);
    const selected = new Set(String(input.value || "").split(",").map(item => item.trim().replace(/^[+-]/, "")).filter(Boolean));
    const options = legalDescriptors(input).filter(item => !query || norm(`${item.label} ${item.value}`).includes(query));
    list.innerHTML = "";
    if (!options.length) {
      const empty = document.createElement("div");
      empty.className = "xp-popup-empty";
      empty.textContent = "沒有合法項目 No legal matches";
      list.append(empty);
      return;
    }
    options.forEach(item => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = `xp-popup-item${selected.has(item.value) ? " xp-selected" : ""}`;
      button.textContent = item.label.includes(item.value) ? item.label : `${item.label} (${item.value})`;
      button.addEventListener("mousedown", event => event.preventDefault());
      button.addEventListener("click", () => {
        const replaceTail = Boolean(search.value && norm(search.value) === norm(tailToken(input, multi)));
        setToken(input, item.value, multi, replaceTail);
        if (!multi) popup.remove();
        else if (popup.isConnected) renderOwnedPopup(popup);
      });
      list.append(button);
    });
  }

  function ownPopup(popup) {
    if (!popup?.closest(".xp-edit-shell") || popup.dataset.legalOwned === "1") return;
    popup.dataset.legalOwned = "1";
    popup.querySelector(".xp-popup-search")?.addEventListener("input", () => setTimeout(() => popup.isConnected && renderOwnedPopup(popup), 0));
    renderOwnedPopup(popup);
  }

  function refresh() {
    document.querySelectorAll("select").forEach(rebuildSelect);
    document.querySelectorAll(".xp-edit-shell > .xp-popup").forEach(ownPopup);
  }

  async function loadMeta() {
    try {
      const response = await fetch("/api/meta", { cache:"no-store" });
      if (!response.ok) return;
      const meta = await response.json();
      schemaTypes.clear();
      (meta.fields || []).forEach(field => schemaTypes.set(field.name, String(field.type || "UNKNOWN").toUpperCase()));
      refresh();
    } catch {
      // Without metadata, availability is still enforced; type-specific lists stay permissive.
    }
  }

  function relevantChange(target) {
    return target?.matches?.(
      ".metric-function,#role-metric-kind,#role-function,#temporal-function,#cross-function," +
      ".s4-metric-fn,.s4-function,#s4-boot-stat,.s4-stage-kind,.ta-role-kind,.ta-role-fn," +
      ".s4-groups,.s4-metric-field,.s4-metric-alias,.s4-alias,.s4-fields,.s4-field,.s4-metric-cond-field," +
      ".ta-custom-alias,.ta-cohort-alias"
    );
  }

  function init() {
    refresh();
    loadMeta();
    document.addEventListener("change", event => { if (relevantChange(event.target)) setTimeout(refresh, 0); });
    document.addEventListener("input", event => { if (relevantChange(event.target)) setTimeout(refresh, 0); });
    document.addEventListener("treepolo:fields-updated", () => setTimeout(() => { refresh(); loadMeta(); }, 0));

    let queued = false;
    new MutationObserver(mutations => {
      if (queued || !mutations.some(mutation => mutation.addedNodes.length || mutation.removedNodes.length)) return;
      queued = true;
      setTimeout(() => { queued = false; refresh(); }, 0);
    }).observe(document.body, { childList:true, subtree:true });
  }

  window.treepoloLegalFieldOptions = {
    available: control => legalDescriptors(control).map(item => item.value),
    refresh,
  };

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init, { once:true });
  else init();
})();
