(() => {
  "use strict";

  if (window.treepoloFieldOptionLegality) return;
  window.treepoloFieldOptionLegality = true;

  const schemaTypes = new Map();
  const NUMERIC_SQL = /\b(INT|REAL|DOUBLE|FLOAT|DECIMAL|NUMERIC)\b/i;
  const NUMERIC_AGGS = new Set(["avg", "sum", "median", "stddev_pop", "stddev_samp"]);
  const ORDERABLE_TEXT_FIELDS = new Set(["game_date"]);
  const MULTI_INPUT_SELECTORS = new Set([
    ".s4-groups", ".s4-partition", ".s4-order", ".s4-fields",
    "#s4-cluster-features", "#s4-cluster-ids", "#s4-cluster-partitions",
    "#s4-reg-independent", "#s4-boot-units", "#cc-entities", "#cc-features",
    ".ta-entity-fields", ".ta-percentile-partition",
  ]);

  const norm = value => String(value ?? "").toLowerCase().replace(/\s+/g, " ").trim();
  const csv = value => String(value || "").split(",").map(item => item.trim()).filter(Boolean);

  function baseDescriptors() {
    const source = document.querySelector("#basic-group");
    if (!source) return [];
    return Array.from(source.options).filter(option => option.value).map(option => ({
      value: option.value,
      label: option.textContent || option.value,
      type: schemaTypes.get(option.value) || "UNKNOWN",
    }));
  }

  function descriptorMap(items) {
    return new Map(items.map(item => [item.value, { ...item }]));
  }

  function aliasDescriptor(value, type = "UNKNOWN") {
    return { value, label: `前一步輸出 Prior-stage alias · ${value}`, type };
  }

  function family(item) {
    if (!item) return "unknown";
    if (NUMERIC_SQL.test(item.type || "")) return "numeric";
    if (ORDERABLE_TEXT_FIELDS.has(item.value)) return "temporal";
    if (/BOOL/i.test(item.type || "")) return "boolean";
    return "text";
  }

  function isNumeric(item) { return family(item) === "numeric"; }
  function isTrendCompatible(item) { return ["numeric", "temporal"].includes(family(item)); }

  function typeFor(map, name) {
    return map.get(name)?.type || schemaTypes.get(name) || "UNKNOWN";
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

  function applyStageShape(map, stage) {
    const kind = stage.querySelector(".s4-stage-kind")?.value || "";
    if (kind === "aggregate") {
      const out = new Map();
      csv(stage.querySelector(".s4-groups")?.value).forEach(name => {
        if (map.has(name)) out.set(name, map.get(name));
      });
      stage.querySelectorAll(".s4-metric-row").forEach(row => {
        const alias = metricAlias(row);
        if (alias) out.set(alias, aliasDescriptor(alias, metricType(row, map)));
      });
      return out;
    }
    if (kind === "derive") {
      const alias = stage.querySelector(".s4-alias")?.value?.trim();
      if (alias) map.set(alias, aliasDescriptor(alias, "REAL"));
      return map;
    }
    if (kind === "rolling") {
      const fn = stage.querySelector(".s4-function")?.value || "avg";
      const field = stage.querySelector(".s4-field")?.value?.trim() || "";
      const alias = stage.querySelector(".s4-alias")?.value?.trim() || `rolling_${fn}_${field || "rows"}`;
      if (alias) {
        const type = fn === "count" ? "INTEGER" : (NUMERIC_AGGS.has(fn) || fn === "sum" || fn === "avg" ? "REAL" : typeFor(map, field));
        map.set(alias, aliasDescriptor(alias, type));
      }
      return map;
    }
    if (kind === "offset") {
      const field = stage.querySelector(".s4-field")?.value?.trim() || "";
      const direction = stage.querySelector(".s4-direction")?.value || "lag";
      const alias = stage.querySelector(".s4-alias")?.value?.trim() || `${direction}_${field}`;
      if (alias) map.set(alias, aliasDescriptor(alias, typeFor(map, field)));
      return map;
    }
    if (kind === "trend") {
      const field = stage.querySelector(".s4-field")?.value?.trim() || "";
      const direction = stage.querySelector(".s4-direction")?.value || "up";
      const alias = stage.querySelector(".s4-alias")?.value?.trim() || `consecutive_${direction}_${field}`;
      if (alias) map.set(alias, aliasDescriptor(alias, "INTEGER"));
      return map;
    }
    if (kind === "rank") {
      const alias = stage.querySelector(".s4-alias")?.value?.trim() || "rank";
      if (alias) map.set(alias, aliasDescriptor(alias, "INTEGER"));
      return map;
    }
    if (kind === "project") {
      const out = new Map();
      csv(stage.querySelector(".s4-fields")?.value).forEach(name => {
        if (map.has(name)) out.set(name, map.get(name));
      });
      return out;
    }
    if (kind === "arsenal_signature") {
      const alias = stage.querySelector(".ta-custom-alias")?.value?.trim() || "arsenal";
      map.set(alias, aliasDescriptor(alias, "TEXT"));
      return map;
    }
    if (kind === "pitch_role_select") {
      const alias = stage.querySelector(".ta-custom-alias")?.value?.trim() || "selected_role_rank";
      map.set(alias, aliasDescriptor(alias, "INTEGER"));
      return map;
    }
    if (kind === "pitch_role_annotate") {
      const alias = stage.querySelector(".ta-custom-alias")?.value?.trim() || "selected_pitch_type";
      map.set(alias, aliasDescriptor(alias, "TEXT"));
      return map;
    }
    if (kind === "empirical_percentile") {
      const alias = stage.querySelector(".ta-custom-alias")?.value?.trim() || "percentile";
      map.set(alias, aliasDescriptor(alias, "REAL"));
      return map;
    }
    if (kind === "event_pattern_cohorts") {
      const alias = stage.querySelector(".ta-cohort-alias")?.value?.trim() || "pattern_cohort";
      map.set(alias, aliasDescriptor(alias, "TEXT"));
      return map;
    }
    return map;
  }

  function mapBeforeStage(stage) {
    let map = descriptorMap(baseDescriptors());
    const list = stage?.parentElement;
    if (!stage || !list) return map;
    for (const sibling of Array.from(list.children)) {
      if (sibling === stage) break;
      if (sibling.classList?.contains("s4-stage")) map = applyStageShape(map, sibling);
    }
    return map;
  }

  function mapAfterPreparation(control) {
    let map = descriptorMap(baseDescriptors());
    const panel = control.closest("#clustering-panel,#regression-panel,#bootstrap-panel");
    const list = panel?.querySelector(".s4-input-stage-list");
    if (!list) return map;
    Array.from(list.children).forEach(stage => {
      if (stage.classList?.contains("s4-stage")) map = applyStageShape(map, stage);
    });
    return map;
  }

  function availableMap(control) {
    const stage = control.closest(".s4-stage");
    if (stage) return mapBeforeStage(stage);
    if (control.closest("#clustering-panel,#regression-panel,#bootstrap-panel") &&
        !control.closest(".s4-filter-row")) return mapAfterPreparation(control);
    return descriptorMap(baseDescriptors());
  }

  function compatibleWith(map, sourceName) {
    const source = map.get(sourceName);
    if (!source) return [];
    const sourceFamily = family(source);
    if (sourceFamily === "unknown") return Array.from(map.values());
    return Array.from(map.values()).filter(item => family(item) === sourceFamily ||
      (sourceFamily === "numeric" && family(item) === "numeric"));
  }

  function filteredDescriptors(control) {
    const map = availableMap(control);
    const all = Array.from(map.values());
    const numeric = () => all.filter(isNumeric);

    if (control.matches(".s4-metric-field")) {
      const fn = control.closest(".s4-metric-row")?.querySelector(".s4-metric-fn")?.value || "count";
      return NUMERIC_AGGS.has(fn) ? numeric() : all;
    }
    if (control.matches(".s4-left,.s4-right-field")) return numeric();
    if (control.matches(".s4-field")) {
      const stage = control.closest(".s4-stage");
      const kind = stage?.querySelector(".s4-stage-kind")?.value || "";
      if (kind === "derive") return numeric();
      if (kind === "rolling") {
        const fn = stage.querySelector(".s4-function")?.value || "avg";
        return ["avg", "sum"].includes(fn) ? numeric() : all;
      }
      if (kind === "trend") return all.filter(isTrendCompatible);
      return all;
    }
    if (control.matches(".s4-value-field")) {
      const source = control.closest(".s4-stage")?.querySelector(".s4-field")?.value?.trim() || "";
      return source ? compatibleWith(map, source) : all;
    }
    if (control.matches(".ta-metric-cond-value-field")) {
      const source = control.closest(".s4-metric-row")?.querySelector(".s4-metric-cond-field")?.value?.trim() || "";
      return source ? compatibleWith(map, source) : all;
    }
    if (control.matches(".ta-pitch-field")) {
      const kind = control.closest(".s4-stage")?.querySelector(".s4-stage-kind")?.value || "";
      const names = kind === "arsenal_signature" ? new Set(["pitch_type", "pitch_name"]) : new Set(["pitch_type"]);
      return all.filter(item => names.has(item.value));
    }
    if (control.matches(".ta-value-field,.ta-percentile-field")) return numeric();
    if (control.matches("#s4-cluster-features,#s4-reg-dependent,#s4-reg-independent,#cc-features")) return numeric();
    if (control.matches("#s4-boot-value")) {
      return document.querySelector("#s4-boot-stat")?.value === "proportion" ? all : numeric();
    }
    if (control.matches("#cc-selection-field,#cc-evaluation-field,.cc-field")) return numeric();
    if (control.matches(".metric-field")) {
      const fn = control.closest(".metric-row")?.querySelector(".metric-function")?.value || "count";
      return NUMERIC_AGGS.has(fn) ? numeric() : all;
    }
    if (control.matches("#role-value-field,#percentile-value")) return numeric();
    if (control.matches("#temporal-value")) {
      const fn = document.querySelector("#temporal-function")?.value || "avg";
      return NUMERIC_AGGS.has(fn) || ["avg", "sum"].includes(fn) ? numeric() : all;
    }
    if (control.matches("#cross-value")) {
      const fn = document.querySelector("#cross-function")?.value || "avg";
      return NUMERIC_AGGS.has(fn) || ["avg", "sum"].includes(fn) ? numeric() : all;
    }
    return all;
  }

  function shouldFilterNativeSelect(select) {
    return select.matches(".metric-field,#role-value-field,#percentile-value,#temporal-value,#cross-value,.cc-field");
  }

  function rebuildNativeSelect(select) {
    if (!shouldFilterNativeSelect(select)) return;
    const descriptors = filteredDescriptors(select);
    const allowed = descriptors.map(item => item.value);
    const previous = select.value;
    const allowEmpty = select.hasAttribute("data-allow-empty") || Array.from(select.options).some(option => !option.value);
    const signature = `${allowEmpty ? "1" : "0"}|${allowed.join("|")}`;
    if (select.dataset.legalSignature === signature) return;
    select.dataset.legalSignature = signature;
    select.innerHTML = "";
    if (allowEmpty) {
      const option = document.createElement("option");
      option.value = "";
      option.textContent = select.matches(".metric-field") ? "不指定 None" : "請選擇 Select Field";
      select.append(option);
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

  function isMultiInput(input) {
    return Array.from(MULTI_INPUT_SELECTORS).some(selector => input.matches(selector));
  }

  function fieldToken(input, multi) {
    const raw = String(input.value || "");
    if (!multi) return raw.trim();
    return (raw.split(",").pop() || "").trim().replace(/^[+-]/, "");
  }

  function setFieldToken(input, value, multi, replaceTail) {
    if (!multi) {
      input.value = value;
    } else {
      const rawParts = String(input.value || "").split(",");
      const rawTail = rawParts.at(-1)?.trim() || "";
      const prefix = replaceTail ? ((rawTail.match(/^[+-]/) || [""])[0]) : "";
      let parts = rawParts.map(item => item.trim()).filter(Boolean);
      if (replaceTail && rawTail) parts = parts.slice(0, -1);
      const existing = new Set(parts.map(item => item.replace(/^[+-]/, "")));
      if (!existing.has(value)) parts.push(`${prefix}${value}`);
      input.value = parts.join(",");
    }
    input.dispatchEvent(new Event("input", { bubbles:true }));
    input.dispatchEvent(new Event("change", { bubbles:true }));
  }

  function renderLegalEditPopup(popup) {
    const shell = popup.closest(".xp-edit-shell");
    const input = shell?.querySelector(":scope > input");
    const search = popup.querySelector(".xp-popup-search");
    const list = popup.querySelector(".xp-popup-list");
    if (!input || !search || !list) return;
    const descriptors = filteredDescriptors(input);
    const query = norm(search.value);
    const multi = isMultiInput(input);
    const selected = new Set(String(input.value || "").split(",").map(item => item.trim().replace(/^[+-]/, "")).filter(Boolean));
    const filtered = descriptors.filter(item => !query || norm(`${item.label} ${item.value}`).includes(query));
    list.innerHTML = "";
    if (!filtered.length) {
      const empty = document.createElement("div");
      empty.className = "xp-popup-empty";
      empty.textContent = "沒有合法項目 No legal matches";
      list.append(empty);
      return;
    }
    filtered.forEach(item => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = `xp-popup-item${selected.has(item.value) ? " xp-selected" : ""}`;
      button.textContent = item.label.includes(item.value) ? item.label : `${item.label} (${item.value})`;
      button.addEventListener("mousedown", event => event.preventDefault());
      button.addEventListener("click", () => {
        const replaceTail = Boolean(search.value && norm(search.value) === norm(fieldToken(input, multi)));
        setFieldToken(input, item.value, multi, replaceTail);
        if (!multi) popup.remove();
        else if (popup.isConnected) renderLegalEditPopup(popup);
      });
      list.append(button);
    });
  }

  function ownEditPopup(popup) {
    if (!popup?.closest(".xp-edit-shell") || popup.dataset.legalOwned === "1") return;
    popup.dataset.legalOwned = "1";
    const search = popup.querySelector(".xp-popup-search");
    search?.addEventListener("input", () => setTimeout(() => {
      if (popup.isConnected) renderLegalEditPopup(popup);
    }, 0));
    renderLegalEditPopup(popup);
  }

  function scanNativeSelects() {
    document.querySelectorAll("select").forEach(rebuildNativeSelect);
  }

  function refresh() {
    scanNativeSelects();
    document.querySelectorAll(".xp-edit-shell > .xp-popup").forEach(ownEditPopup);
  }

  async function loadMeta() {
    try {
      const response = await fetch("/api/meta", { cache:"no-store" });
      if (!response.ok) return;
      const meta = await response.json();
      (meta.fields || []).forEach(field => schemaTypes.set(field.name, String(field.type || "UNKNOWN").toUpperCase()));
      refresh();
    } catch {
      // Field availability still works without type narrowing; retry on fields-updated.
    }
  }

  function init() {
    loadMeta();
    refresh();
    document.addEventListener("change", event => {
      if (event.target.matches(".metric-function,#role-metric-kind,#temporal-function,#cross-function,.s4-metric-fn,.s4-function,#s4-boot-stat,.s4-stage-kind") ||
          event.target.matches(".s4-groups,.s4-metric-field,.s4-metric-alias,.s4-alias,.s4-fields,.ta-custom-alias,.ta-cohort-alias,.s4-field,.s4-metric-cond-field")) {
        setTimeout(refresh, 0);
      }
    });
    document.addEventListener("input", event => {
      if (event.target.matches(".s4-groups,.s4-metric-field,.s4-metric-alias,.s4-alias,.s4-fields,.ta-custom-alias,.ta-cohort-alias,.s4-field,.s4-metric-cond-field")) {
        setTimeout(refresh, 0);
      }
    });
    document.addEventListener("treepolo:fields-updated", () => {
      setTimeout(() => { loadMeta(); refresh(); }, 0);
    });
    let queued = false;
    new MutationObserver(mutations => {
      if (queued || !mutations.some(mutation => mutation.addedNodes.length || mutation.removedNodes.length)) return;
      queued = true;
      setTimeout(() => { queued = false; refresh(); }, 0);
    }).observe(document.body, { childList:true, subtree:true });
  }

  window.treepoloLegalFieldOptions = {
    available: control => filteredDescriptors(control).map(item => item.value),
    refresh,
  };

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init, { once:true });
  else init();
})();
