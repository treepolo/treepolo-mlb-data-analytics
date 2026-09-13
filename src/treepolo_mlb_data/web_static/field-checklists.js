(() => {
  "use strict";

  if (window.treepoloFieldChecklists) return;
  window.treepoloFieldChecklists = true;

  const DYNAMIC_MULTI_SELECTORS = [
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
  const CONTROL_SELECTOR = ['input[data-multi-field]', ...DYNAMIC_MULTI_SELECTORS].join(",");
  const PIPELINE_SHAPE_SELECTORS = [
    ".s4-stage-kind", ".s4-groups", ".s4-fields", ".s4-field",
    ".s4-metric-field", ".s4-metric-alias", ".s4-alias",
    ".s4-metric-cond-field", ".ta-role-kind", ".ta-role-fn",
    ".ta-custom-alias", ".ta-cohort-alias",
  ].join(",");

  let generatedId = 0;
  let refreshQueued = false;

  function model() {
    return window.treepoloMultiField;
  }

  function isControl(control) {
    return Boolean(control?.tagName === "INPUT" && control.matches(CONTROL_SELECTOR));
  }

  function claimControl(control) {
    if (!isControl(control)) return false;
    control.dataset.multiField = "1";
    control.dataset.unifiedFieldInput = "1";
    control.dataset.unifiedMulti = "1";
    control.dataset.fieldChecklistOwned = "1";
    control.removeAttribute("list");
    return true;
  }

  function injectStyles() {
    if (document.getElementById("field-checklist-styles")) return;
    const style = document.createElement("style");
    style.id = "field-checklist-styles";
    style.textContent = `
      input[data-multi-field]{width:100%;box-sizing:border-box}
      input[data-multi-field].ta-invalid{outline:2px solid #b12828;background:#fff3f3}
      .field-checklist{width:100%;max-width:100%;min-width:0;border:1px solid #7f9db9;background:#fff;max-height:190px;overflow:auto}
      .field-checklist-tools{position:sticky;top:0;z-index:4;background:#f4f7fb;border-bottom:1px solid #b6c1ce;padding:4px}
      .field-checklist-summary{display:flex;gap:4px;align-items:center;flex-wrap:wrap;min-height:20px;font-size:11px}
      .field-checklist-chip{padding:1px 5px;border:1px solid #8ea2ba;background:#fff;cursor:pointer;white-space:nowrap}
      .field-checklist-items{min-width:max-content}
      .field-checklist .field-check-item{display:flex;flex-direction:row;align-items:center;gap:6px;min-height:22px;padding:2px 5px;font-weight:400;cursor:pointer;user-select:none;white-space:nowrap}
      .field-checklist .field-check-item.field-search-hit{outline:2px solid #d68b00;outline-offset:-2px;background:#fff4cf}
    `;
    document.head.append(style);
  }

  function activeForRender(control) {
    const panel = control.closest?.(".panel");
    return !panel || panel.classList.contains("active-panel");
  }

  function descriptors(control) {
    const provider = window.treepoloLegalFieldOptions;
    if (typeof provider?.descriptors === "function") {
      try { return provider.descriptors(control) || []; }
      catch { return []; }
    }
    if (typeof provider?.available === "function") {
      try {
        return (provider.available(control) || []).map(value => ({
          value,
          label: window.treepoloFieldCatalog?.label?.(value) || value,
        }));
      } catch { return []; }
    }
    return [];
  }

  function selectedValues(control) {
    return model()?.values?.(control) || String(control?.value || "").split(",").map(item => item.trim()).filter(Boolean);
  }

  function legalSelection(control) {
    const raw = selectedValues(control);
    const legal = new Set(descriptors(control).map(item => item.value));
    return {
      raw,
      accepted: raw.filter(value => legal.has(value)),
      rejected: raw.filter(value => !legal.has(value)),
    };
  }

  function writeValues(control, values) {
    if (model()?.write) model().write(control, values, { emit:true });
    else {
      control.value = values.join(",");
      control.dispatchEvent(new Event("change", { bubbles:true }));
    }
  }

  function sanitizeControl(control) {
    if (!isControl(control) || control.dataset.fieldChecklistSanitizing === "1") return true;
    const selection = legalSelection(control);
    control.classList.toggle("ta-invalid", selection.rejected.length > 0);
    if (!selection.rejected.length) return true;
    control.dataset.fieldChecklistSanitizing = "1";
    try {
      writeValues(control, selection.accepted);
    } finally {
      delete control.dataset.fieldChecklistSanitizing;
    }
    control.classList.remove("ta-invalid");
    return false;
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

  function renderSignature(items) {
    return items.map(item => `${item.value}\u0001${item.label}`).join("\u0003");
  }

  function syncState(state) {
    const selection = legalSelection(state.control);
    const selected = selection.accepted;
    const current = new Set(selected);
    state.control.classList.toggle("ta-invalid", selection.rejected.length > 0);
    state.rows.forEach(row => { row.checkbox.checked = current.has(row.value); });

    const selectionSignature = selected.join("\u0001");
    if (state.selectionSignature === selectionSignature) return;
    state.selectionSignature = selectionSignature;

    const fragment = document.createDocumentFragment();
    const count = document.createElement("strong");
    count.textContent = `已選 ${selected.length} Selected`;
    fragment.append(count);
    selected.forEach(value => {
      const row = state.rowByValue.get(value);
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = "field-checklist-chip";
      chip.textContent = value;
      chip.title = row?.textLabel || value;
      if (row) {
        chip.addEventListener("click", event => {
          event.preventDefault();
          event.stopPropagation();
          state.flash(row);
        });
      }
      fragment.append(chip);
    });
    state.summary.replaceChildren(fragment);
  }

  function buildChecklist(control, host, items, signature) {
    host.replaceChildren();

    const tools = document.createElement("div");
    tools.className = "field-checklist-tools";
    const summary = document.createElement("div");
    summary.className = "field-checklist-summary";
    const list = document.createElement("div");
    list.className = "field-checklist-items";
    tools.append(summary);
    host.append(tools, list);

    const rows = [];
    const rowByValue = new Map();
    const selected = new Set(legalSelection(control).accepted);

    items.forEach(item => {
      const row = document.createElement("div");
      row.className = "field-check-item";
      const textLabel = item.label || item.value;

      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.checked = selected.has(item.value);
      checkbox.value = item.value;
      const text = document.createElement("span");
      text.textContent = textLabel;
      row.append(checkbox, text);
      list.append(row);

      const entry = { value:item.value, label:row, checkbox, textLabel };
      rows.push(entry);
      rowByValue.set(item.value, entry);

      checkbox.addEventListener("click", event => event.stopPropagation());
      checkbox.addEventListener("change", () => {
        let values = legalSelection(control).accepted;
        if (checkbox.checked) {
          if (!values.includes(item.value)) values.push(item.value);
        } else {
          values = values.filter(value => value !== item.value);
        }
        writeValues(control, values);
      });
      row.addEventListener("click", event => {
        event.preventDefault();
        event.stopPropagation();
        checkbox.checked = !checkbox.checked;
        checkbox.dispatchEvent(new Event("change", { bubbles:true }));
      });
    });

    const state = {
      control, host, signature, rows, rowByValue, summary,
      selectionSignature:null,
      flash:null,
    };

    state.flash = row => {
      rows.forEach(entry => entry.label.classList.remove("field-search-hit"));
      if (!row) return;
      row.label.classList.add("field-search-hit");
      row.label.scrollIntoView({ block:"nearest", inline:"nearest" });
      setTimeout(() => row.label.classList.remove("field-search-hit"), 1800);
    };

    host._treepoloChecklistState = state;
    syncState(state);
  }

  function renderChecklist(control) {
    if (!claimControl(control) || !control.isConnected || !activeForRender(control)) return;
    const items = descriptors(control);
    const host = hostFor(control);
    const signature = renderSignature(items);
    const state = host._treepoloChecklistState;
    if (state?.control === control && state.signature === signature) {
      syncState(state);
      return;
    }
    buildChecklist(control, host, items, signature);
  }

  function refreshRoot(root) {
    if (!root) return;
    if (isControl(root)) renderChecklist(root);
    root.querySelectorAll?.(CONTROL_SELECTOR).forEach(renderChecklist);
  }

  function refreshActivePanel() {
    const panel = document.querySelector(".main-pane > .panel.active-panel");
    if (panel) refreshRoot(panel);
    else refreshRoot(document);
  }

  function scheduleRefresh() {
    if (refreshQueued) return;
    refreshQueued = true;
    queueMicrotask(() => {
      refreshQueued = false;
      refreshActivePanel();
    });
  }

  function handleAddedControls(mutations) {
    const controls = new Set();
    mutations.forEach(mutation => {
      Array.from(mutation.addedNodes || []).forEach(node => {
        if (node.nodeType !== 1) return;
        if (isControl(node)) controls.add(node);
        node.querySelectorAll?.(CONTROL_SELECTOR).forEach(control => controls.add(control));
      });
    });
    controls.forEach(control => {
      claimControl(control);
      if (activeForRender(control)) renderChecklist(control);
    });
  }

  function syncChangedControl(control) {
    if (!isControl(control)) return;
    claimControl(control);
    const host = control.dataset.checklistKey ? document.getElementById(`field-checklist-${control.dataset.checklistKey}`) : null;
    if (host?._treepoloChecklistState) syncState(host._treepoloChecklistState);
  }

  function init() {
    injectStyles();
    document.querySelectorAll(CONTROL_SELECTOR).forEach(claimControl);
    refreshActivePanel();

    ["treepolo:fields-updated", "treepolo:field-legality-ready", "treepolo:analysis-options-changed"]
      .forEach(name => document.addEventListener(name, scheduleRefresh));

    document.addEventListener("treepolo:panel-activated", event => {
      const panel = document.getElementById(event.detail?.panelId || "");
      if (panel) queueMicrotask(() => refreshRoot(panel));
    });

    document.addEventListener("input", event => {
      syncChangedControl(event.target);
      if (event.target?.matches?.(PIPELINE_SHAPE_SELECTORS)) scheduleRefresh();
    });
    document.addEventListener("change", event => {
      if (isControl(event.target)) sanitizeControl(event.target);
      syncChangedControl(event.target);
      if (event.target?.matches?.(PIPELINE_SHAPE_SELECTORS)) scheduleRefresh();
    });
    document.addEventListener("focusout", event => {
      if (isControl(event.target)) sanitizeControl(event.target);
    });
    document.addEventListener("keydown", event => {
      if (event.key !== "Enter" || !isControl(event.target)) return;
      const wasValid = sanitizeControl(event.target);
      if (!wasValid) event.preventDefault();
      syncChangedControl(event.target);
    });

    new MutationObserver(handleAddedControls).observe(document.body, { childList:true, subtree:true });
  }

  window.treepoloFieldChecklistsApi = {
    owns: isControl,
    refresh: scheduleRefresh,
    refreshRoot,
    sanitize: sanitizeControl,
  };

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init, { once:true });
  else init();
})();