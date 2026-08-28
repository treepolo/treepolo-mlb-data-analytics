(() => {
  "use strict";

  if (window.treepoloFieldChecklists) return;
  window.treepoloFieldChecklists = true;

  // Unordered sets of fields belong to this component. Ordered selectors such
  // as .s4-order stay in the editable-combo layer because order/+/- are data.
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
  const PIPELINE_SHAPE_SELECTORS = [
    ".s4-stage-kind", ".s4-groups", ".s4-fields", ".s4-field",
    ".s4-metric-field", ".s4-metric-alias", ".s4-alias",
    ".s4-metric-cond-field", ".ta-role-kind", ".ta-role-fn",
    ".ta-custom-alias", ".ta-cohort-alias",
  ].join(",");
  const MUTATION_IGNORE_SELECTOR = ".field-checklist,#result-content,#analysis-library-panel,.ta-table-pager";

  let generatedId = 0;
  let refreshQueued = false;

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
      ${ALL_SELECTOR}{display:none!important}
      .field-checklist{width:100%;max-width:100%;min-width:0}
      .field-checklist-tools{position:sticky;top:0;z-index:4;background:#f4f7fb;border-bottom:1px solid #b6c1ce;padding:4px}
      .field-checklist-search{width:100%;box-sizing:border-box}
      .field-checklist-summary{display:flex;gap:4px;align-items:center;flex-wrap:wrap;margin-top:3px;min-height:20px;font-size:11px}
      .field-checklist-chip{padding:1px 5px;border:1px solid #8ea2ba;background:#fff;cursor:pointer;white-space:nowrap}
      .field-checklist-items{min-width:max-content}
      /* Stage-4 forms style every descendant <label> as a vertical form field.
         Checklist rows are divs and this scoped rule guarantees the exact same
         row geometry in Basic Analysis and dynamically generated stages. */
      .field-checklist .field-check-item{display:flex;flex-direction:row;align-items:center;gap:6px;min-height:22px;padding:2px 5px;font-weight:400;cursor:pointer;user-select:none;white-space:nowrap}
      .field-checklist .field-check-item.field-search-hit{outline:2px solid #d68b00;outline-offset:-2px;background:#fff4cf}
    `;
    document.head.append(style);
  }

  function isTextMulti(control) {
    return control?.tagName === "INPUT" && MULTI_FIELD_INPUT_SELECTORS.some(selector => control.matches(selector));
  }

  function claimControl(control) {
    if (!isTextMulti(control)) return;
    // This prevents the later editable-combo scanner from owning the same
    // semantic control. The checklist remains the single UI owner.
    control.dataset.unifiedFieldInput = "1";
    control.dataset.unifiedMulti = "1";
    control.dataset.fieldChecklistOwned = "1";
    control.removeAttribute("list");
  }

  function claimControls(root = document) {
    if (root.matches?.(INPUT_SELECTOR)) claimControl(root);
    root.querySelectorAll?.(INPUT_SELECTOR).forEach(claimControl);
  }

  function detachLegacyShell(input) {
    if (!isTextMulti(input)) return;
    claimControl(input);
    const shell = input.closest(".xp-field-input-shell,.xp-edit-shell");
    if (!shell) return;
    shell.parentNode.insertBefore(input, shell);
    shell.remove();
  }

  function activeForRender(control) {
    const panel = control.closest?.(".panel");
    return !panel || panel.classList.contains("active-panel");
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
    const provider = window.treepoloLegalFieldOptions?.available;
    if (typeof provider === "function") {
      try { return Array.from(new Set(provider(control) || [])); }
      catch { return []; }
    }
    // Static selects already contain their legal bootstrap choices. Dynamic
    // pipeline inputs wait until the legality provider exists so saved values
    // are never erased during startup.
    if (control?.tagName === "SELECT") {
      return Array.from(control.options || []).map(option => option.value).filter(Boolean);
    }
    return null;
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
    // A checklist selection is a committed value change, not text editing.
    // Emitting both input and change doubled every downstream legality refresh.
    control.dispatchEvent(new Event("change", { bubbles:true }));
  }

  function hostFor(control) {
    if (!control.dataset.checklistKey) control.dataset.checklistKey = control.id || `generated-${++generatedId}`;
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

  function renderSignature(control, legal, locateOnly) {
    const labels = legal.map(value => `${value}\u0001${optionLabel(control, value)}`);
    return `${locateOnly ? "locate" : "filter"}\u0002${labels.join("\u0003")}`;
  }

  function syncState(state) {
    const selected = selectedValues(state.control);
    const current = new Set(selected);
    state.rows.forEach(row => { row.checkbox.checked = current.has(row.value); });

    // Rebuilding the summary on every refresh created child-list mutations even
    // when nothing changed. Those mutations woke multiple document-wide legacy
    // observers and produced a long tail of post-load work. Keep it idempotent.
    const selectionSignature = selected.join("\u0001");
    if (state.selectionSignature === selectionSignature) return;
    state.selectionSignature = selectionSignature;

    const fragment = document.createDocumentFragment();
    const count = document.createElement("strong");
    count.textContent = `已選 ${current.size} Selected`;
    fragment.append(count);
    selected.forEach(value => {
      const row = state.rowByValue.get(value);
      if (!row) return;
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = "field-checklist-chip";
      chip.textContent = value;
      chip.title = row.textLabel;
      chip.addEventListener("click", event => {
        event.preventDefault();
        event.stopPropagation();
        state.flash(row);
      });
      fragment.append(chip);
    });
    state.summary.replaceChildren(fragment);
  }

  function buildChecklist(control, host, legal, locateOnly, signature) {
    const previousQuery = host.querySelector(".field-checklist-search")?.value || "";
    host.replaceChildren();

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

    const rows = [];
    const rowByValue = new Map();
    const selected = new Set(selectedValues(control));

    legal.forEach(value => {
      // div is deliberate. Dynamic Stage-4 controls live inside an outer label;
      // nesting another label allowed ancestor form CSS to corrupt row layout.
      const row = document.createElement("div");
      row.className = "field-check-item";
      const textLabel = optionLabel(control, value);
      row.dataset.searchText = normalize(`${textLabel} ${value}`);

      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.checked = selected.has(value);
      checkbox.value = value;
      const text = document.createElement("span");
      text.textContent = textLabel;
      row.append(checkbox, text);
      items.append(row);

      const item = { value, label:row, checkbox, textLabel };
      rows.push(item);
      rowByValue.set(value, item);

      checkbox.addEventListener("click", event => event.stopPropagation());
      checkbox.addEventListener("change", () => {
        let values = selectedValues(control);
        if (checkbox.checked) {
          if (!values.includes(value)) values.push(value);
        } else {
          values = values.filter(itemValue => itemValue !== value);
        }
        writeValues(control, values, true);
        syncState(state);
      });
      row.addEventListener("click", event => {
        event.preventDefault();
        event.stopPropagation();
        checkbox.checked = !checkbox.checked;
        checkbox.dispatchEvent(new Event("change", { bubbles:true }));
      });
    });

    const state = {
      control, host, signature, rows, rowByValue, summary, search, locateOnly,
      selectionSignature: null,
      flash: null,
    };

    state.flash = row => {
      rows.forEach(item => item.label.classList.remove("field-search-hit"));
      if (!row) return;
      row.label.classList.add("field-search-hit");
      row.label.scrollIntoView({ block:"nearest", inline:"nearest" });
      setTimeout(() => row.label.classList.remove("field-search-hit"), 1800);
    };

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
        state.flash(matches[locateIndex]);
        return;
      }
      rows.forEach(row => { row.label.hidden = Boolean(query) && !row.label.dataset.searchText.includes(query); });
    }

    search.addEventListener("click", event => event.stopPropagation());
    search.addEventListener("input", event => {
      event.stopPropagation();
      locateIndex = -1;
      applySearch(false);
    });
    search.addEventListener("keydown", event => {
      if (event.key === "Enter" && locateOnly) {
        event.preventDefault();
        event.stopPropagation();
        applySearch(true);
      }
    });

    host._treepoloChecklistState = state;
    host.scrollLeft = 0;
    syncState(state);
    if (previousQuery) applySearch(false);
    return state;
  }

  function renderChecklist(control) {
    if (!control?.isConnected || !activeForRender(control)) return;
    claimControl(control);
    if (isTextMulti(control)) detachLegacyShell(control);

    const legal = legalValues(control);
    if (legal === null) return;

    control.hidden = true;
    control.style.display = "none";
    const host = hostFor(control);
    const allowed = new Set(legal);
    const before = selectedValues(control);
    const kept = before.filter(value => allowed.has(value));
    if (!sameValues(before, kept)) writeValues(control, kept, true);

    const locateOnly = locateOnlySearch(control);
    const signature = renderSignature(control, legal, locateOnly);
    const state = host._treepoloChecklistState;
    if (state?.control === control && state.signature === signature) {
      syncState(state);
      return;
    }
    buildChecklist(control, host, legal, locateOnly, signature);
  }

  function refreshAll() {
    document.querySelectorAll(ALL_SELECTOR).forEach(control => {
      if (activeForRender(control)) renderChecklist(control);
    });
  }

  function scheduleRefresh() {
    if (refreshQueued) return;
    refreshQueued = true;
    queueMicrotask(() => {
      refreshQueued = false;
      refreshAll();
    });
  }

  function addedControls(mutation) {
    const controls = [];
    Array.from(mutation.addedNodes || []).forEach(node => {
      if (node.nodeType !== 1) return;
      if (node.closest?.(MUTATION_IGNORE_SELECTOR)) return;

      if (node.matches?.(INPUT_SELECTOR)) controls.push(node);
      node.querySelectorAll?.(INPUT_SELECTOR).forEach(control => controls.push(control));

      if (node.matches?.('select[multiple][data-field-select]')) controls.push(node);
      node.querySelectorAll?.('select[multiple][data-field-select]').forEach(control => controls.push(control));
    });
    return controls;
  }

  function handleAddedControls(mutations) {
    const controls = new Set();
    mutations.forEach(mutation => addedControls(mutation).forEach(control => controls.add(control)));
    controls.forEach(control => {
      claimControl(control);
      if (activeForRender(control)) renderChecklist(control);
    });
  }

  function init() {
    injectStyles();
    // Claim all dynamic unordered-multi inputs before field-controls-unified.js
    // scans the document. One semantic control therefore has exactly one owner.
    claimControls(document);
    refreshAll();

    ["treepolo:fields-updated", "treepolo:field-legality-ready", "treepolo:analysis-options-changed"]
      .forEach(name => document.addEventListener(name, scheduleRefresh));

    document.addEventListener("click", event => {
      if (event.target?.closest?.(".nav-item")) scheduleRefresh();
    });
    document.addEventListener("change", event => {
      if (event.target?.matches?.(PIPELINE_SHAPE_SELECTORS)) scheduleRefresh();
    });
    document.addEventListener("input", event => {
      if (event.target?.matches?.(PIPELINE_SHAPE_SELECTORS)) scheduleRefresh();
    });

    // The observer only claims/renders newly inserted checklist controls. It no
    // longer converts arbitrary DOM mutations into a document-wide refresh.
    new MutationObserver(handleAddedControls).observe(document.body, { childList:true, subtree:true });
  }

  window.treepoloFieldChecklistsApi = {
    owns(control) {
      return Boolean(control?.matches?.(ALL_SELECTOR));
    },
    refresh: scheduleRefresh,
  };

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init, { once:true });
  else init();
})();