(() => {
  "use strict";

  if (window.treepoloFieldLegality) return;
  window.treepoloFieldLegality = true;

  const FIELD_INPUT_RULES = [
    [".s4-groups", true], [".s4-metric-field", false], [".s4-metric-cond-field", false],
    [".s4-left", false], [".s4-right-field", false], [".s4-field", false],
    [".s4-value-field", false], [".s4-partition", true], [".s4-order", true],
    [".s4-fields", true], ["#s4-cluster-features", true], ["#s4-cluster-ids", true],
    ["#s4-cluster-partitions", true], ["#s4-reg-dependent", false],
    ["#s4-reg-independent", true], ["#s4-boot-value", false], ["#s4-boot-units", true],
    ["#s4-boot-group", false], ["#cc-entities", true], ["#cc-features", true],
    [".ta-entity-fields", true], [".ta-pitch-field", false], [".ta-value-field", false],
    [".ta-percentile-field", false], [".ta-percentile-partition", true],
    [".ta-event-field", false], [".ta-metric-cond-value-field", false],
  ];
  const FIELD_INPUT_SELECTOR = FIELD_INPUT_RULES.map(([selector]) => selector).join(",");
  const NATIVE_FIELD_SELECTOR = [
    'select[data-field-select]:not([multiple])', '.s4-filter-field', '.s4-field-select',
    '.cc-field', '.cc-filter-field', '.result-sort-field',
  ].join(",");
  const NUMERIC_TYPES = new Set(["INTEGER", "REAL", "DOUBLE", "FLOAT", "DECIMAL", "NUMERIC", "BIGINT", "SMALLINT"]);
  const fieldTypes = new Map();
  let metaPromise = null;
  let scheduled = false;

  const norm = value => String(value ?? "").toLowerCase().replace(/\s+/g, " ").trim();
  const tokens = value => String(value || "").split(",").map(item => item.trim().replace(/^[+-]/, "")).filter(Boolean);

  function loadMeta() {
    if (metaPromise) return metaPromise;
    metaPromise = fetch("/api/meta", { cache: "no-store" })
      .then(response => response.ok ? response.json() : null)
      .then(meta => {
        fieldTypes.clear();
        (meta?.fields || []).forEach(field => {
          fieldTypes.set(String(field.name), String(field.type || "TEXT").toUpperCase());
        });
        return meta;
      })
      .catch(() => null);
    return metaPromise;
  }

  function labelMap() {
    const source = document.querySelector("#basic-group");
    const labels = new Map();
    if (source) {
      Array.from(source.options).filter(option => option.value).forEach(option => {
        labels.set(option.value, option.textContent || option.value);
      });
    }
    return labels;
  }

  function baseCatalog() {
    const labels = labelMap();
    const catalog = new Map();
    if (fieldTypes.size) {
      for (const [name, type] of fieldTypes) {
        catalog.set(name, { value: name, label: labels.get(name) || name, type });
      }
    } else {
      for (const [name, label] of labels) {
        catalog.set(name, { value: name, label, type: "UNKNOWN" });
      }
    }
    return catalog;
  }

  function typeOf(name, catalog) {
    return catalog?.get(name)?.type || fieldTypes.get(name) || "UNKNOWN";
  }

  function aggregateType(fn, sourceType = "UNKNOWN") {
    if (fn === "count") return "INTEGER";
    if (["avg", "sum", "median", "stddev_pop", "stddev_samp"].includes(fn)) return "REAL";
    if (["min", "max"].includes(fn)) return sourceType;
    return "UNKNOWN";
  }

  function aggregateNeedsNumeric(fn) {
    return ["avg", "sum", "median", "stddev_pop", "stddev_samp"].includes(fn);
  }

  function addAlias(catalog, alias, type) {
    if (!alias) return;
    catalog.set(alias, { value: alias, label: `前一步輸出 Prior-stage alias · ${alias}`, type: type || "UNKNOWN" });
  }

  function processStage(catalog, stage) {
    const kind = stage.querySelector(".s4-stage-kind")?.value || "";
    if (kind === "aggregate") {
      const next = new Map();
      tokens(stage.querySelector(".s4-groups")?.value).forEach(name => {
        if (catalog.has(name)) next.set(name, catalog.get(name));
      });
      stage.querySelectorAll(".s4-metric-row").forEach(row => {
        const fn = row.querySelector(".s4-metric-fn")?.value || "count";
        const field = row.querySelector(".s4-metric-field")?.value?.trim() || "";
        const alias = row.querySelector(".s4-metric-alias")?.value?.trim() || (field ? `${fn}_${field}` : "row_count");
        addAlias(next, alias, aggregateType(fn, typeOf(field, catalog)));
      });
      return next;
    }
    if (kind === "project") {
      const next = new Map();
      tokens(stage.querySelector(".s4-fields")?.value).forEach(name => {
        if (catalog.has(name)) next.set(name, catalog.get(name));
      });
      return next;
    }
    if (kind === "derive") addAlias(catalog, stage.querySelector(".s4-alias")?.value?.trim(), "REAL");
    if (kind === "rolling") {
      const fn = stage.querySelector(".s4-function")?.value || "avg";
      const field = stage.querySelector(".s4-field")?.value?.trim() || "";
      addAlias(catalog, stage.querySelector(".s4-alias")?.value?.trim(), aggregateType(fn, typeOf(field, catalog)));
    }
    if (kind === "offset") {
      const field = stage.querySelector(".s4-field")?.value?.trim() || "";
      addAlias(catalog, stage.querySelector(".s4-alias")?.value?.trim(), typeOf(field, catalog));
    }
    if (kind === "trend") addAlias(catalog, stage.querySelector(".s4-alias")?.value?.trim(), "INTEGER");
    if (kind === "rank") addAlias(catalog, stage.querySelector(".s4-alias")?.value?.trim() || "rank", "INTEGER");
    if (kind === "arsenal_signature") addAlias(catalog, stage.querySelector(".ta-custom-alias")?.value?.trim() || "arsenal", "TEXT");
    if (kind === "pitch_role_select") addAlias(catalog, stage.querySelector(".ta-custom-alias")?.value?.trim() || "selected_role_rank", "INTEGER");
    if (kind === "pitch_role_annotate") {
      const pitchField = stage.querySelector(".ta-pitch-field")?.value?.trim() || "";
      addAlias(catalog, stage.querySelector(".ta-custom-alias")?.value?.trim() || "selected_pitch_type", typeOf(pitchField, catalog));
    }
    if (kind === "empirical_percentile") addAlias(catalog, stage.querySelector(".ta-custom-alias")?.value?.trim() || "percentile", "REAL");
    if (kind === "event_pattern_cohorts") addAlias(catalog, stage.querySelector(".ta-cohort-alias")?.value?.trim() || "pattern_cohort", "TEXT");
    return catalog;
  }

  function pipelineCatalog(control) {
    let catalog = baseCatalog();
    const stage = control.closest?.(".s4-stage");
    const list = stage?.parentElement;
    if (!stage || !list) return catalog;
    for (const sibling of Array.from(list.children)) {
      if (sibling === stage) break;
      if (sibling.classList?.contains("s4-stage")) catalog = processStage(catalog, sibling);
    }
    return catalog;
  }

  function requirementFor(control) {
    if (!control) return { kind: "any" };
    if (control.matches?.(".result-sort-field")) return { kind: "existing" };
    if (control.matches?.("#s4-cluster-features,#cc-features,#s4-reg-dependent,#s4-reg-independent,#cc-selection-field,#cc-evaluation-field,.s4-left,.s4-right-field")) return { kind: "numeric" };
    if (control.matches?.("#s4-boot-value")) return { kind: document.querySelector("#s4-boot-stat")?.value === "proportion" ? "any" : "numeric" };
    if (control.matches?.(".metric-field")) return { kind: aggregateNeedsNumeric(control.closest(".metric-row")?.querySelector(".metric-function")?.value || "count") ? "numeric" : "any" };
    if (control.matches?.("#role-value-field")) return { kind: aggregateNeedsNumeric(document.querySelector("#role-function")?.value || "avg") ? "numeric" : "any" };
    if (control.matches?.("#temporal-value")) return { kind: aggregateNeedsNumeric(document.querySelector("#temporal-function")?.value || "avg") ? "numeric" : "any" };
    if (control.matches?.("#cross-value")) return { kind: aggregateNeedsNumeric(document.querySelector("#cross-function")?.value || "avg") ? "numeric" : "any" };
    if (control.matches?.(".s4-metric-field")) return { kind: aggregateNeedsNumeric(control.closest(".s4-metric-row")?.querySelector(".s4-metric-fn")?.value || "count") ? "numeric" : "any" };
    if (control.matches?.(".ta-value-field")) return { kind: aggregateNeedsNumeric(control.closest(".s4-stage")?.querySelector(".ta-role-fn")?.value || "avg") ? "numeric" : "any" };
    if (control.matches?.(".s4-field")) {
      const stage = control.closest(".s4-stage");
      if (stage?.querySelector(".s4-stage-kind")?.value === "rolling") {
        return { kind: aggregateNeedsNumeric(stage.querySelector(".s4-function")?.value || "avg") ? "numeric" : "any" };
      }
    }
    if (control.matches?.(".s4-value-field")) return { kind: "compatible", with: control.closest(".s4-stage")?.querySelector(".s4-field") };
    if (control.matches?.(".ta-metric-cond-value-field")) return { kind: "compatible", with: control.closest(".s4-metric-row")?.querySelector(".s4-metric-cond-field") };
    if (control.matches?.(".ta-pitch-field")) {
      const stageKind = control.closest(".s4-stage")?.querySelector(".s4-stage-kind")?.value;
      if (["pitch_role_select", "pitch_role_annotate"].includes(stageKind)) return { kind: "fixed", value: control.defaultValue || "pitch_type" };
    }
    return { kind: "any" };
  }

  function sameFamily(a, b) {
    if (a === "UNKNOWN" || b === "UNKNOWN") return true;
    return NUMERIC_TYPES.has(a) === NUMERIC_TYPES.has(b);
  }

  function legalOptions(control, catalog = null) {
    if (!control) return [];
    const requirement = requirementFor(control);
    if (requirement.kind === "existing") {
      return Array.from(control.options || []).filter(option => option.value).map(option => ({ value: option.value, label: option.textContent || option.value, type: "UNKNOWN" }));
    }
    const source = catalog || pipelineCatalog(control);
    const compareType = requirement.kind === "compatible" && requirement.with ? typeOf(requirement.with.value?.trim() || "", source) : "UNKNOWN";
    return Array.from(source.values()).filter(option => {
      if (requirement.kind === "fixed") return option.value === requirement.value;
      if (requirement.kind === "numeric") return !fieldTypes.size || option.type === "UNKNOWN" || NUMERIC_TYPES.has(option.type);
      if (requirement.kind === "compatible") return !fieldTypes.size || sameFamily(option.type, compareType);
      return true;
    });
  }

  function refreshNativeSelect(select) {
    if (!select || select.multiple || select.matches(".result-sort-field")) return;
    const current = select.value;
    const old = Array.from(select.options);
    const empty = old.find(option => !option.value);
    const desired = (empty ? [["", empty.textContent || "不指定 None"]] : []).concat(
      legalOptions(select, baseCatalog()).map(option => [option.value, option.label])
    );
    const same = old.length === desired.length && old.every((option, index) => option.value === desired[index][0] && (option.textContent || "") === desired[index][1]);
    if (same) return;
    select.innerHTML = "";
    desired.forEach(([value, label]) => {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = label;
      select.append(option);
    });
    if (Array.from(select.options).some(option => option.value === current)) select.value = current;
    else if (empty) select.value = "";
  }

  function ruleForInput(input) {
    return FIELD_INPUT_RULES.find(([selector]) => input.matches(selector)) || null;
  }

  function currentToken(input, multi) {
    const raw = String(input.value || "");
    return multi ? (raw.split(",").pop() || "").trim().replace(/^[+-]/, "") : raw.trim();
  }

  function chooseToken(input, value, multi) {
    if (!multi) {
      input.value = value;
    } else {
      const parts = String(input.value || "").split(",").map(item => item.trim()).filter(Boolean);
      const tail = parts.at(-1) || "";
      const prefix = (tail.match(/^[+-]/) || [""])[0];
      if (tail) parts.pop();
      if (!new Set(parts.map(item => item.replace(/^[+-]/, ""))).has(value)) parts.push(`${prefix}${value}`);
      input.value = parts.join(",");
    }
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function legalizeEditPopup(popup) {
    if (!popup || popup.dataset.legalized === "1") return;
    const shell = popup.closest(".xp-edit-shell");
    const input = shell?.querySelector(`input${FIELD_INPUT_SELECTOR ? "" : ""}`);
    if (!shell || !input || !input.matches(FIELD_INPUT_SELECTOR)) return;
    const rule = ruleForInput(input);
    if (!rule) return;
    popup.dataset.legalized = "1";
    const multi = Boolean(rule[1]);
    const originalSearch = popup.querySelector(".xp-popup-search");
    const search = originalSearch ? originalSearch.cloneNode(true) : document.createElement("input");
    search.type = "text";
    search.className = "xp-popup-search";
    search.placeholder = "搜尋合法欄位 Search legal fields";
    if (originalSearch) originalSearch.replaceWith(search); else popup.prepend(search);
    let list = popup.querySelector(".xp-popup-list");
    if (!list) {
      list = document.createElement("div");
      list.className = "xp-popup-list";
      popup.append(list);
    }
    const render = () => {
      const query = norm(search.value);
      const selected = new Set(tokens(input.value));
      const options = legalOptions(input).filter(option => !query || norm(`${option.label} ${option.value}`).includes(query));
      list.innerHTML = "";
      if (!options.length) {
        const empty = document.createElement("div");
        empty.className = "xp-popup-empty";
        empty.textContent = "沒有合法符合項目 No legal matches";
        list.append(empty);
        return;
      }
      options.forEach(option => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = `xp-popup-item${selected.has(option.value) ? " xp-selected" : ""}`;
        button.textContent = option.label.includes(option.value) ? option.label : `${option.label} (${option.value})`;
        button.addEventListener("mousedown", event => event.preventDefault());
        button.addEventListener("click", () => {
          chooseToken(input, option.value, multi);
          if (multi) render(); else popup.remove();
        });
        list.append(button);
      });
    };
    search.addEventListener("input", render);
    search.addEventListener("keydown", event => {
      if (event.key === "Escape") popup.remove();
      if (event.key === "Enter") {
        const first = list.querySelector(".xp-popup-item");
        if (first) { event.preventDefault(); first.click(); }
      }
    });
    render();
  }

  function refreshAll() {
    document.querySelectorAll(NATIVE_FIELD_SELECTOR).forEach(refreshNativeSelect);
    document.querySelectorAll(".xp-edit-shell > .xp-popup").forEach(legalizeEditPopup);
  }

  function scheduleRefresh() {
    if (scheduled) return;
    scheduled = true;
    setTimeout(() => { scheduled = false; refreshAll(); }, 0);
  }

  function init() {
    refreshAll();
    loadMeta().then(refreshAll);
    document.addEventListener("treepolo:fields-updated", scheduleRefresh);
    document.addEventListener("change", event => {
      if (event.target.matches(".metric-function,#role-function,#temporal-function,#cross-function,.s4-metric-fn,.s4-function,.ta-role-fn,#s4-boot-stat,.s4-field,.s4-metric-cond-field,.s4-stage-kind")) scheduleRefresh();
    });
    new MutationObserver(mutations => {
      if (!mutations.some(mutation => mutation.addedNodes.length)) return;
      scheduleRefresh();
    }).observe(document.body, { childList: true, subtree: true });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init, { once: true });
  else init();
})();
