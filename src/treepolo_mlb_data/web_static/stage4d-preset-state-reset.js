(() => {
  "use strict";

  if (window.__treepoloStage4DPresetStateReset) return;
  window.__treepoloStage4DPresetStateReset = true;

  const $ = selector => document.querySelector(selector);

  function resetPresetGeometry({ clearPreset = false } = {}) {
    ["#viz-x-min", "#viz-x-max", "#viz-y-min", "#viz-y-max", "#viz-ref-x", "#viz-ref-y"].forEach(selector => {
      const element = $(selector);
      if (element) element.value = "";
    });

    const equalAxes = $("#viz-equal-axes");
    if (equalAxes) equalAxes.checked = false;

    const stacked = $("#viz-stacked");
    if (stacked) stacked.checked = false;

    const orientation = $("#viz-bar-orientation");
    if (orientation) orientation.value = "vertical";

    if (clearPreset) {
      const preset = $("#viz-preset");
      if (preset) preset.value = "";
    }
  }

  // Use capture phase so stale geometry is cleared before the main Stage 4D
  // handlers load the new result section or apply the newly selected preset.
  document.addEventListener("change", event => {
    const target = event.target;
    if (!(target instanceof Element)) return;

    if (target.matches("#viz-section")) {
      resetPresetGeometry({ clearPreset: true });
      return;
    }

    if (target.matches("#viz-preset")) {
      resetPresetGeometry();
    }
  }, true);
})();
