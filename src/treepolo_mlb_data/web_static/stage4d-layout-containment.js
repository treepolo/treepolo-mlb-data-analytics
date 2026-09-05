(() => {
  "use strict";

  if (window.__treepoloStage4DLayoutContainment) return;
  window.__treepoloStage4DLayoutContainment = true;

  function installLayoutContainment() {
    if (document.querySelector("#stage4d-layout-containment-style")) return;
    const style = document.createElement("style");
    style.id = "stage4d-layout-containment-style";
    style.textContent = `
      #visualization-panel .stage4d-controls,
      #visualization-panel .stage4d-controls fieldset,
      #visualization-panel .stage4d-map-grid,
      #visualization-panel .stage4d-display-grid,
      #visualization-panel .stage4d-map-grid > label,
      #visualization-panel .stage4d-display-grid > label {
        min-width: 0;
      }

      #visualization-panel .stage4d-map-grid,
      #visualization-panel .stage4d-display-grid {
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }

      #visualization-panel .stage4d-map-grid > label > select,
      #visualization-panel .stage4d-map-grid > label > input,
      #visualization-panel .stage4d-map-grid > label > textarea,
      #visualization-panel .stage4d-display-grid > label > select,
      #visualization-panel .stage4d-display-grid > label > input,
      #visualization-panel .stage4d-display-grid > label > textarea {
        box-sizing: border-box;
        width: 100%;
        min-width: 0;
        max-width: 100%;
      }

      #visualization-panel .stage4d-map-grid > label > select,
      #visualization-panel .stage4d-display-grid > label > select {
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }
    `;
    document.head.append(style);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", installLayoutContainment, {once: true});
  } else {
    installLayoutContainment();
  }
})();
