(() => {
  "use strict";

  function renderChecklist(select) {
    // Basic Analysis already owns its checklist because it must also refresh
    // the computed-metric sort selector when Group By changes.
    if (!select || select.id === "basic-group") return;

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

    host.innerHTML = "";
    for (const option of Array.from(select.options)) {
      if (!option.value) continue;
      const label = document.createElement("label");
      label.className = "field-check-item";

      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.checked = option.selected;
      checkbox.value = option.value;
      checkbox.addEventListener("change", () => {
        option.selected = checkbox.checked;
        select.dispatchEvent(new Event("change", { bubbles: true }));
      });

      const text = document.createElement("span");
      text.textContent = option.textContent;
      label.append(checkbox, text);
      host.append(label);
    }
  }

  function observeSelect(select) {
    renderChecklist(select);
    const observer = new MutationObserver(() => renderChecklist(select));
    observer.observe(select, { childList: true });
  }

  function init() {
    document
      .querySelectorAll('select[multiple][data-field-select]')
      .forEach(observeSelect);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init, { once: true });
  } else {
    init();
  }
})();
