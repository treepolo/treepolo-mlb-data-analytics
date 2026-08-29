(() => {
  "use strict";

  if (window.treepoloNativeSelectArrowDonors) return;
  window.treepoloNativeSelectArrowDonors = true;

  function ensureDonor(shell) {
    if (!shell) return null;
    let donor = shell.querySelector(":scope > .xp-native-select-arrow");
    if (donor) return donor;

    donor = document.createElement("select");
    donor.className = "xp-native-select-arrow";
    donor.tabIndex = -1;
    donor.setAttribute("aria-hidden", "true");
    donor.dataset.nativeArrowDonor = "1";

    const blank = document.createElement("option");
    blank.value = "";
    blank.textContent = "";
    donor.append(blank);
    shell.append(donor);
    return donor;
  }

  function syncShell(shell) {
    if (!shell?.matches?.(".xp-field-shell,.xp-field-input-shell,.xp-semantic-shell")) return;
    if (shell.classList.contains("xp-semantic-shell")) {
      const donor = shell.querySelector(":scope > .xp-native-select-arrow");
      if (shell.classList.contains("xp-has-domain")) ensureDonor(shell);
      else donor?.remove();
      return;
    }
    ensureDonor(shell);
  }

  function sync(root = document) {
    if (root?.matches?.(".xp-field-shell,.xp-field-input-shell,.xp-semantic-shell")) syncShell(root);
    root?.querySelectorAll?.(".xp-field-shell,.xp-field-input-shell,.xp-semantic-shell").forEach(syncShell);
  }

  function syncRelated(target) {
    const shell = target?.closest?.(".xp-field-shell,.xp-field-input-shell,.xp-semantic-shell");
    if (shell) queueMicrotask(() => syncShell(shell));
  }

  function init() {
    sync(document);
    document.addEventListener("treepolo:fields-updated", () => queueMicrotask(() => sync(document)));
    document.addEventListener("treepolo:analysis-options-changed", () => queueMicrotask(() => sync(document)));
    document.addEventListener("change", event => syncRelated(event.target));
    document.addEventListener("input", event => syncRelated(event.target));

    new MutationObserver(mutations => {
      mutations.forEach(mutation => {
        Array.from(mutation.addedNodes || []).forEach(node => {
          if (node.nodeType === 1) sync(node);
        });
      });
    }).observe(document.body, { childList:true, subtree:true });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init, { once:true });
  else init();
})();
