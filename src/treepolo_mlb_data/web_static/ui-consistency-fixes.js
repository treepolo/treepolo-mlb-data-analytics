(() => {
  "use strict";

  if (window.treepoloUiConsistencyFixes) return;
  window.treepoloUiConsistencyFixes = true;

  const DENSE_RANK_LABEL = "保留並列（不跳號） Dense Rank";
  const BASIC_METRIC_STYLE_ID = "basic-metric-layout-style";
  const ISO_DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

  function normalizeTieLabels(root = document) {
    const options = [];
    if (root.matches?.('option[value="dense_rank"]')) options.push(root);
    root.querySelectorAll?.('select option[value="dense_rank"]').forEach(option => options.push(option));
    options.forEach(option => {
      if (option.textContent !== DENSE_RANK_LABEL) option.textContent = DENSE_RANK_LABEL;
    });
  }

  function ensureBasicMetricStyle() {
    if (document.getElementById(BASIC_METRIC_STYLE_ID)) return;
    const style = document.createElement("style");
    style.id = BASIC_METRIC_STYLE_ID;
    style.textContent = `
      #basic-panel .form-grid > div { min-width: 0; }
      .basic-metric-head,
      #basic-metrics .metric-row {
        display: grid;
        grid-template-columns: minmax(105px, .9fr) minmax(0, 1.4fr) minmax(105px, .9fr) 28px;
        gap: 5px;
        align-items: center;
        width: 100%;
        min-width: 0;
      }
      .basic-metric-head {
        margin-bottom: 3px;
        color: #333;
        font-weight: 700;
        line-height: 1.25;
      }
      #basic-metrics { min-width: 0; }
      #basic-metrics .metric-row > * { min-width: 0; max-width: 100%; }
      #basic-metrics .metric-row > .metric-function,
      #basic-metrics .metric-row > .metric-field { width: 100%; }
      #basic-metrics .checkbox-line.mini { min-width: 0; white-space: normal; }
      @media (max-width: 1000px) {
        .basic-metric-head,
        #basic-metrics .metric-row {
          grid-template-columns: minmax(95px, .9fr) minmax(0, 1.3fr) minmax(100px, .9fr) 28px;
        }
      }
    `;
    document.head.append(style);
  }

  function normalizeBasicMetrics() {
    const list = document.querySelector("#basic-metrics");
    if (!list) return;
    ensureBasicMetricStyle();

    const container = list.parentElement;
    if (!container) return;
    container.classList.add("basic-metrics-container");

    let head = container.querySelector(":scope > .basic-metric-head");
    if (!head) {
      head = document.createElement("div");
      head.className = "basic-metric-head";
      head.setAttribute("aria-hidden", "true");
      ["統計 Aggregate", "欄位 Field", "不重複 Distinct", ""].forEach(label => {
        const cell = document.createElement("span");
        cell.textContent = label;
        head.append(cell);
      });
      list.insertAdjacentElement("beforebegin", head);
    }
    head.hidden = !list.querySelector(".metric-row");
  }

  function isValidIsoDate(text) {
    if (!ISO_DATE_RE.test(text)) return false;
    const date = new Date(`${text}T00:00:00Z`);
    return Number.isFinite(date.getTime()) && date.toISOString().slice(0, 10) === text;
  }

  function enableWholeDatePaste() {
    if (document.documentElement.dataset.wholeDatePasteReady === "1") return;
    document.documentElement.dataset.wholeDatePasteReady = "1";
    document.addEventListener("paste", event => {
      const input = event.target?.closest?.('input[type="date"]');
      if (!input || input.disabled || input.readOnly) return;
      const text = event.clipboardData?.getData("text")?.trim() || "";
      if (!isValidIsoDate(text)) return;
      event.preventDefault();
      input.value = text;
      input.dispatchEvent(new Event("input", { bubbles:true }));
      input.dispatchEvent(new Event("change", { bubbles:true }));
    });
  }

  function observeStageLists() {
    document.querySelectorAll(".s4-stage-list,.s4-input-stage-list").forEach(list => {
      if (list.dataset.tieLabelObserved === "1") return;
      list.dataset.tieLabelObserved = "1";
      new MutationObserver(mutations => {
        mutations.forEach(mutation => {
          Array.from(mutation.addedNodes || []).forEach(node => {
            if (node.nodeType === 1) normalizeTieLabels(node);
          });
        });
      }).observe(list, { childList:true, subtree:true });
    });
  }

  function init() {
    normalizeTieLabels(document);
    normalizeBasicMetrics();
    enableWholeDatePaste();
    observeStageLists();
    document.addEventListener("treepolo:analysis-options-changed", () => {
      normalizeTieLabels(document);
      normalizeBasicMetrics();
      observeStageLists();
    });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init, { once:true });
  else init();
})();
