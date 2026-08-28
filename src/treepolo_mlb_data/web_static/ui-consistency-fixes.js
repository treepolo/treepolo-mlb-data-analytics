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
    root.querySelectorAll?.('select option[value="dense_rank"]').forEach(option => {
      if (option.textContent !== DENSE_RANK_LABEL) option.textContent = DENSE_RANK_LABEL;
    });
  }

  function init() {
    injectStyles();
    normalizeTieLabels(document);
    let queued = false;
    new MutationObserver(mutations => {
      if (queued || !mutations.some(mutation => mutation.addedNodes.length)) return;
      queued = true;
      setTimeout(() => {
        queued = false;
        normalizeTieLabels(document);
      }, 0);
    }).observe(document.body, { childList:true, subtree:true });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init, { once:true });
  else init();
})();
