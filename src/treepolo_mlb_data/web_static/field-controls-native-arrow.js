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

  function sync(root = document) {
    root.querySelectorAll?.(".xp-field-shell,.xp-field-input-shell").forEach(ensureDonor);
    root.querySelectorAll?.(".xp-semantic-shell").forEach(shell => {
      const donor = shell.querySelector(":scope > .xp-native-select-arrow");
      if (shell.classList.contains("xp-has-domain")) ensureDonor(shell);
      else donor?.remove();
    });
  }

  function init() {
    sync(document);
    document.addEventListener("treepolo:fields-updated", () => setTimeout(() => sync(document), 0));
    document.addEventListener("treepolo:analysis-options-changed", () => setTimeout(() => sync(document), 0));

    let queued = false;
    new MutationObserver(mutations => {
      if (queued || !mutations.some(mutation => mutation.addedNodes.length || mutation.removedNodes.length)) return;
      queued = true;
      setTimeout(() => {
        queued = false;
        sync(document);
      }, 0);
    }).observe(document.body, { childList:true, subtree:true });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init, { once:true });
  else init();
})();
