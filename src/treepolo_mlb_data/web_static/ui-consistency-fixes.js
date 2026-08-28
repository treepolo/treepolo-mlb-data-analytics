(() => {
  "use strict";

  if (window.treepoloUiConsistencyFixes) return;
  window.treepoloUiConsistencyFixes = true;

  const DENSE_RANK_LABEL = "保留並列（不跳號） Dense Rank";

  function injectStyles() {
    if (document.getElementById("ui-consistency-fixes-styles")) return;
    const style = document.createElement("style");
    style.id = "ui-consistency-fixes-styles";
    style.textContent = `
      .s4-metric-row > button.remove-row {
        grid-column: 1 / -1;
        justify-self: start;
        width: auto;
        min-width: 170px;
        max-width: 100%;
        white-space: nowrap;
        writing-mode: horizontal-tb;
      }
    `;
    document.head.append(style);
  }

  function normalizeTieLabels(root = document) {
    const options = [];
    if (root.matches?.('option[value="dense_rank"]')) options.push(root);
    root.querySelectorAll?.('select option[value="dense_rank"]').forEach(option => options.push(option));
    options.forEach(option => {
      if (option.textContent !== DENSE_RANK_LABEL) option.textContent = DENSE_RANK_LABEL;
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
    injectStyles();
    normalizeTieLabels(document);
    observeStageLists();
    document.addEventListener("treepolo:analysis-options-changed", () => {
      normalizeTieLabels(document);
      observeStageLists();
    });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init, { once:true });
  else init();
})();
