(() => {
  "use strict";

  let resolveReady;
  const ready = new Promise(resolve => { resolveReady = resolve; });
  window.treepoloDatasetReady = ready;

  function installStyle() {
    if (document.querySelector("#dataset-workspace-style")) return;
    const style = document.createElement("style");
    style.id = "dataset-workspace-style";
    style.textContent = `
      .dataset-workspace-badge {
        display:inline-flex; align-items:center; gap:5px; margin-left:8px; padding:2px 7px;
        border:1px solid #7c8b9a; background:#f4f6f8; font-size:12px; white-space:nowrap;
      }
      .dataset-workspace-badge strong { color:#174f8b; }
    `;
    document.head.append(style);
  }

  function applyDataset(dataset) {
    const id = String(dataset?.id || "mlb").toLowerCase();
    const label = String(dataset?.label || (id === "mlb" ? "MLB / Baseball Savant" : id.toUpperCase()));
    const speedUnit = dataset?.speed_unit ? String(dataset.speed_unit) : "—";
    const distanceUnit = dataset?.distance_unit ? String(dataset.distance_unit) : "—";
    window.treepoloDataset = { ...dataset, id, label, speed_unit:speedUnit, distance_unit:distanceUnit };
    document.documentElement.dataset.treepoloDataset = id;

    const productName = `treepolo ${label} Data Analytics`;
    document.title = productName;
    const title = document.querySelector(".title-text");
    if (title) title.textContent = productName;
    const app = document.querySelector(".app-window");
    if (app) app.setAttribute("aria-label", productName);

    installStyle();
    let badge = document.querySelector("#dataset-workspace-badge");
    if (!badge) {
      badge = document.createElement("span");
      badge.id = "dataset-workspace-badge";
      badge.className = "dataset-workspace-badge";
      const indicator = document.querySelector("#db-indicator");
      if (indicator) indicator.insertAdjacentElement("afterend", badge);
      else document.querySelector(".toolbar")?.append(badge);
    }
    badge.innerHTML = `<strong>${label}</strong><span>Speed ${speedUnit}</span><span>Distance ${distanceUnit}</span>`;

    document.dispatchEvent(new CustomEvent("treepolo:dataset-ready", { detail:window.treepoloDataset }));
    return window.treepoloDataset;
  }

  async function load() {
    try {
      const response = await fetch("/api/meta", { cache:"no-store" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const meta = await response.json();
      resolveReady(applyDataset(meta.dataset || { id:"mlb", label:"MLB / Baseball Savant", speed_unit:"mph", distance_unit:"ft" }));
    } catch (error) {
      console.error("Dataset workspace metadata failed to load", error);
      resolveReady(applyDataset({ id:"mlb", label:"MLB / Baseball Savant", speed_unit:"mph", distance_unit:"ft" }));
    }
  }

  load();
})();
