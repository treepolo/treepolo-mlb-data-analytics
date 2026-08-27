(() => {
  "use strict";

  function normalize(value) {
    return String(value || "").toLowerCase().replace(/\s+/g, " ").trim();
  }

  function injectStyles() {
    if (document.getElementById("field-checklist-search-styles")) return;
    const style = document.createElement("style");
    style.id = "field-checklist-search-styles";
    style.textContent = `
      .field-checklist-tools{position:sticky;top:0;z-index:4;background:#f4f7fb;border-bottom:1px solid #b6c1ce;padding:4px}
      .field-checklist-search{width:100%;box-sizing:border-box}
      .field-checklist-summary{display:flex;gap:4px;align-items:center;flex-wrap:wrap;margin-top:3px;min-height:20px;font-size:11px}
      .field-checklist-chip{padding:1px 5px;border:1px solid #8ea2ba;background:#fff;cursor:pointer}
      .field-checklist-items{min-width:max-content}
      .field-check-item.field-search-hit{outline:2px solid #d68b00;outline-offset:-2px;background:#fff4cf}
    `;
    document.head.append(style);
  }

  function locateOnlySearch(select) {
    return /(?:^|-)group$/i.test(select.id || "") || select.dataset.searchMode === "locate";
  }

  function renderChecklist(select) {
    if (!select) return;
    select.hidden = true;
    const hostId = `${select.id}-checklist`;
    let host = document.getElementById(hostId);
    if (!host) {
      host = document.createElement("div");
      host.id = hostId;
      host.className = "field-checklist";
      host.setAttribute("role", "group");
      select.insertAdjacentElement("afterend", host);
    }

    const locateOnly = locateOnlySearch(select);
    const previousQuery = host.querySelector(".field-checklist-search")?.value || "";
    host.innerHTML = "";
    const tools = document.createElement("div");
    tools.className = "field-checklist-tools";
    const search = document.createElement("input");
    search.type = "search";
    search.className = "field-checklist-search";
    search.placeholder = locateOnly
      ? "搜尋並定位欄位 Search & locate field"
      : "搜尋欄位 Search fields";
    search.value = previousQuery;
    const summary = document.createElement("div");
    summary.className = "field-checklist-summary";
    const items = document.createElement("div");
    items.className = "field-checklist-items";
    tools.append(search, summary);
    host.append(tools, items);

    const rows = [];
    for (const option of Array.from(select.options)) {
      if (!option.value) continue;
      const label = document.createElement("label");
      label.className = "field-check-item";
      label.dataset.searchText = normalize(`${option.textContent || ""} ${option.value}`);

      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.checked = option.selected;
      checkbox.value = option.value;
      checkbox.addEventListener("change", () => {
        option.selected = checkbox.checked;
        select.dispatchEvent(new Event("change", { bubbles: true }));
        updateSummary();
      });

      const text = document.createElement("span");
      text.textContent = option.textContent;
      label.append(checkbox, text);
      items.append(label);
      rows.push({ label, checkbox, option });
    }

    function flash(row) {
      rows.forEach(item => item.label.classList.remove("field-search-hit"));
      if (!row) return;
      row.label.classList.add("field-search-hit");
      row.label.scrollIntoView({ block: "nearest" });
      setTimeout(() => row.label.classList.remove("field-search-hit"), 1800);
    }

    function updateSummary() {
      summary.innerHTML = "";
      const selected = rows.filter(row => row.checkbox.checked);
      const count = document.createElement("strong");
      count.textContent = `已選 ${selected.length} Selected`;
      summary.append(count);
      selected.forEach(row => {
        const chip = document.createElement("button");
        chip.type = "button";
        chip.className = "field-checklist-chip";
        chip.textContent = row.option.value;
        chip.title = row.option.textContent || row.option.value;
        chip.addEventListener("click", () => flash(row));
        summary.append(chip);
      });
    }

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
      rows.forEach(row => {
        row.label.hidden = Boolean(query) && !row.label.dataset.searchText.includes(query);
      });
    }

    search.addEventListener("input", () => { locateIndex = -1; applySearch(false); });
    search.addEventListener("keydown", event => {
      if (event.key === "Enter" && locateOnly) {
        event.preventDefault();
        applySearch(true);
      }
    });
    select.addEventListener("change", () => {
      rows.forEach(row => { row.checkbox.checked = row.option.selected; });
      updateSummary();
    });

    updateSummary();
    if (previousQuery) applySearch(false);
  }

  function observeSelect(select) {
    if (select.dataset.checklistObserved === "1") return;
    select.dataset.checklistObserved = "1";
    renderChecklist(select);
    const observer = new MutationObserver(() => renderChecklist(select));
    observer.observe(select, { childList: true });
    select.addEventListener("treepolo:checklist-refresh", () => renderChecklist(select));
  }

  function init() {
    injectStyles();
    document.querySelectorAll('select[multiple][data-field-select]').forEach(observeSelect);
    document.addEventListener("treepolo:fields-updated", () => {
      document.querySelectorAll('select[multiple][data-field-select]').forEach(select => {
        observeSelect(select);
        renderChecklist(select);
      });
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init, { once: true });
  } else {
    init();
  }
})();
