(() => {
  "use strict";

  if (window.treepoloFieldChecklists) return;
  window.treepoloFieldChecklists = true;

  // These controls are semantic sets of fields. They intentionally share one
  // checklist implementation. Ordered controls such as .s4-order stay editable
  // because field order and +/- direction are part of their meaning.
  const MULTI_FIELD_INPUT_SELECTORS = [
    ".s4-groups",
    ".s4-partition",
    ".s4-fields",
    "#s4-cluster-features",
    "#s4-cluster-ids",
    "#s4-cluster-partitions",
    "#s4-reg-independent",
    "#s4-boot-units",
    "#cc-entities",
    "#cc-features",
    ".ta-entity-fields",
    ".ta-percentile-partition",
  ];
  const INPUT_SELECTOR = MULTI_FIELD_INPUT_SELECTORS.join(",");
  const ALL_SELECTOR = `select[multiple][data-field-select],${INPUT_SELECTOR}`;
  let generatedId = 0;

  function normalize(value) {
    return String(value || "").toLowerCase().replace(/\s+/g, " ").trim();
  }

  function csv(value) {
    return String(value || "").split(",").map(item => item.trim()).filter(Boolean);
  }

  function injectStyles() {
    if (document.getElementById("field-checklist-search-styles")) return;
    const style = document.createElement("style");
    style.id = "field-checklist-search-styles";
    style.textContent = `
      .field-checklist{border:1px solid #7f9db9;background:#fff;max-height:180px;overflow:auto;min-width:0}
      .field-checklist-tools{position:sticky;top:0;z-index:4;background:#f4f7fb;border-bottom:1px solid #b6c1ce;padding:4px}
      .field-checklist-search{width:100%;box-sizing:border-box}
      .field-checklist-summary{display:flex;gap:4px;align-items:center;flex-wrap:wrap;margin-top:3px;min-height:20px;font-size:11px}
      .field-checklist-chip{padding:1px 5px;border:1px solid #8ea2ba;background:#fff;cursor:pointer;white-space:nowrap}
      .field-checklist-items{min-width:max-content;padding:2px 3px}
      .field-check-item{display:flex;align-items:center;gap:5px;min-height:20px;white-space:nowrap}
      .field-check-item.field-search-hit{outline:2px solid #d68b00;outline-offset:-2px;background:#fff4cf}
    `;
    document.head.append(style);
  }

  function isTextMulti(control) {
    return control?.tagName === "INPUT" && MULTI_FIELD_INPUT_SELECTORS.some(selector => control.matches(selector));
  }

  function locateOnlySearch(control) {
    return control.id === "basic-group" || control.dataset.searchMode === "locate";
  }

  function baseLabels() {
    return new Map(Array.from(document.querySelector("#basic-group")?.options || [])
      .filter(option => option.value)
      .map(option => [option.value, option.textContent || option.value]));
  }

  function legalValues(control) {
    // Every checklist goes through the same legality provider. A context where
    // all fields are legal simply receives the provider's full result.
    const provider = window.treepoloLegalFieldOptions?.available;
    if (typeof provider === "function") {
      try { return Array.from(new Set(provider(control) || [])); }
      catch { return []; }
    }
    // Bootstrap fallback only; treepolo:field-legality-ready refreshes this as
    // soon as the legality layer is available.
    if (control?.tagName === "SELECT") {
      return Array.from(control.options || []).map(option => option.value).filter(Boolean);
    }
    return [];
  }

  function optionLabel(control, value) {
    if (control?.tagName === "SELECT") {
      const option = Array.from(control.options || []).find(item => item.value === value);
      if (option?.textContent) return option.textContent;
    }
    return baseLabels().get(value) || `前一步輸出 Prior-stage alias · ${value}`;
  }

  function selectedValues(control) {
    if (control?.tagName === "SELECT") {
      return Array.from(control.selectedOptions || []).map(option => option.value).filter(Boolean);
    }
    return csv(control?.value);
  }

  function sameValues(a, b) {
    return a.length === b.length && a.every((value, index) => value === b[index]);
  }

  function writeValues(control, values, emit = true) {
    if (control?.tagName === "SELECT") {
      const wanted = new Set(values);
      Array.from(control.options || []).forEach(option => { option.selected = wanted.has(option.value); });
    } else if (control) {
      control.value = values.join(",");
    }
    if (!emit || !control) return;
    control.dispatchEvent(new Event("input", { bubbles:true }));
    control.dispatchEvent(new Event("change", { bubbles:true }));
  }

  function detachUnifiedShell(input) {
    if (!isTextMulti(input)) return;
    // Own this control before field-controls-unified can turn it into a single
    // editable combo. If it was already decorated, unwrap the original storage
    // input and discard that shell.
    input.dataset.unifiedFieldInput = "1";
    const shell = input.closest(".xp-field-input-shell,.xp-edit-shell");
    if (shell) {
      shell.parentNode.insertBefore(input, shell);
      shell.remove();
    }
    input.removeAttribute("list");
    const datalist = input.nextElementSibling;
    if (datalist?.tagName === "DATALIST") datalist.remove();
  }

  function hostFor(control) {
    if (!control.dataset.checklistKey) {
      control.dataset.checklistKey = control.id || `generated-${++generatedId}`;
    }
    const hostId = `field-checklist-${control.dataset.checklistKey}`;
    let host = document.getElementById(hostId);
    if (!host) {
      host = document.createElement("div");
      host.id = hostId;
      host.className = "field-checklist";
      host.setAttribute("role", "group");
      control.insertAdjacentElement("afterend", host);
    }
    return host;
  }

  function renderChecklist(control) {
    if (!control?.isConnected) return;
    if (isTextMulti(control)) detachUnifiedShell(control);

    control.hidden = true;
    control.style.display = "none";
    const host = hostFor(control);
    const locateOnly = locateOnlySearch(control);
    const previousQuery = host.querySelector(".field-checklist-search")?.value || "";
    const legal = legalValues(control);
    const allowed = new Set(legal);
    const before = selectedValues(control);
    const kept = before.filter(value => allowed.has(value));
    if (!sameValues(before, kept)) writeValues(control, kept, true);

    host.innerHTML = "";
    const tools = document.createElement("div");
    tools.className = "field-checklist-tools";
    const search = document.createElement("input");
    search.type = "search";
    search.className = "field-checklist-search";
    search.placeholder = locateOnly ? "搜尋並定位欄位 Search & locate field" : "搜尋欄位 Search fields";
    search.value = previousQuery;
    const summary = document.createElement("div");
    summary.className = "field-checklist-summary";
    const items = document.createElement("div");
    items.className = "field-checklist-items";
    tools.append(search, summary);
    host.append(tools, items);

    const selected = new Set(selectedValues(control));
    const rows = legal.map(value => {
      const label = document.createElement("label");
      label.className = "field-check-item";
      const textLabel = optionLabel(control, value);
      label.dataset.searchText = normalize(`${textLabel} ${value}`);

      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.checked = selected.has(value);
      checkbox.value = value;

      const text = document.createElement("span");
      text.textContent = textLabel;
      label.append(checkbox, text);
      items.append(label);
      return { value, label, checkbox, textLabel };
    });

    function flash(row) {
      rows.forEach(item => item.label.classList.remove("field-search-hit"));
      if (!row) return;
      row.label.classList.add("field-search-hit");
      row.label.scrollIntoView({ block:"nearest" });
      setTimeout(() => row.label.classList.remove("field-search-hit"), 1800);
    }

    function updateSummary() {
      summary.innerHTML = "";
      const current = selectedValues(control);
      const count = document.createElement("strong");
      count.textContent = `已選 ${current.length} Selected`;
      summary.append(count);
      current.forEach(value => {
        const row = rows.find(item => item.value === value);
        if (!row) return;
        const chip = document.createElement("button");
        chip.type = "button";
        chip.className = "field-checklist-chip";
        chip.textContent = value;
        chip.title = row.textLabel;
        chip.addEventListener("click", () => flash(row));
        summary.append(chip);
      });
    }

    rows.forEach(row => row.checkbox.addEventListener("change", () => {
      let values = selectedValues(control);
      if (row.checkbox.checked) {
        if (!values.includes(row.value)) values.push(row.value);
      } else {
        values = values.filter(value => value !== row.value);
      }
      writeValues(control, values, true);
      updateSummary();
    }));

    let locateIndex = -1;
    function applySearch(advance = false) {
      const query = normalize(search.value);
      if (locateOnly) {
        const matches = query ? rows.filter(row => row.label.dataset.searchText.includes(query)) : [];
        if (!matches.length) {
          locateIndex = -1;
          rows.forEach(row => row.label.classList.remove("field-search-hit"));
          return;
        }
        locateIndex = advance ? (locateIndex + 1) % matches.length : 0;
        flash(matches[locateIndex]);
        return;
      }
      rows.forEach(row => { row.label.hidden = Boolean(query) && !row.label.dataset.searchText.includes(query); });
    }

    search.addEventListener("input", () => { locateIndex = -1; applySearch(false); });
    search.addEventListener("keydown", event => {
      if (event.key === "Enter" && locateOnly) {
        event.preventDefault();
        applySearch(true);
      }
    });

    updateSummary();
    if (previousQuery) applySearch(false);
  }

  function refreshAll() {
    document.querySelectorAll(ALL_SELECTOR).forEach(renderChecklist);
  }

  function mutationContainsControl(mutation) {
    return Array.from(mutation.addedNodes || []).some(node => {
      if (node.nodeType !== 1) return false;
      return node.matches?.(ALL_SELECTOR) || Boolean(node.querySelector?.(ALL_SELECTOR));
    });
  }

  function init() {
    injectStyles();
    refreshAll();
    ["treepolo:fields-updated", "treepolo:field-legality-ready", "treepolo:analysis-options-changed"]
      .forEach(name => document.addEventListener(name, () => setTimeout(refreshAll, 0)));

    let queued = false;
    new MutationObserver(mutations => {
      if (queued || !mutations.some(mutationContainsControl)) return;
      queued = true;
      setTimeout(() => { queued = false; refreshAll(); }, 0);
    }).observe(document.body, { childList:true, subtree:true });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init, { once:true });
  else init();
})();
