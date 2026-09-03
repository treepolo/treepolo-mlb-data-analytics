(() => {
  "use strict";

  if (window.__treepoloStage4DSavedRestore) return;
  window.__treepoloStage4DSavedRestore = true;

  const upstreamFetch = window.fetch.bind(window);
  let pendingSaved = null;

  const $ = (selector) => document.querySelector(selector);

  function setSelect(selector, value, missing) {
    const el = $(selector);
    if (!el) return;
    const wanted = value == null ? "" : String(value);
    if ([...el.options].some((option) => option.value === wanted)) {
      el.value = wanted;
    } else if (wanted) {
      missing.push(wanted);
    }
  }

  function setValue(selector, value) {
    const el = $(selector);
    if (el && value != null) el.value = String(value);
  }

  function setChecked(selector, value) {
    const el = $(selector);
    if (el && value != null) el.checked = Boolean(value);
  }

  function restoreSpec(item) {
    const spec = item?.spec;
    if (!spec || typeof spec !== "object") return;
    const missing = [];

    setSelect("#viz-type", spec.type || "scatter", missing);
    const preset = spec.preset || "";
    if (preset) setSelect("#viz-preset", preset, []);
    else setSelect("#viz-preset", "", []);

    const mapping = spec.mapping || {};
    for (const key of ["x", "y", "series", "label", "lower", "upper"]) {
      setSelect(`#viz-${key}`, mapping[key] || "", missing);
    }

    const display = spec.display || {};
    const values = {
      "#viz-title": display.title,
      "#viz-subtitle": display.subtitle,
      "#viz-width": display.width,
      "#viz-height": display.height,
      "#viz-point-size": display.point_size,
      "#viz-opacity": display.opacity,
      "#viz-x-min": display.x_min,
      "#viz-x-max": display.x_max,
      "#viz-y-min": display.y_min,
      "#viz-y-max": display.y_max,
      "#viz-ref-x": display.reference_x,
      "#viz-ref-y": display.reference_y,
      "#viz-bar-orientation": display.bar_orientation,
    };
    Object.entries(values).forEach(([selector, value]) => {
      const el = $(selector);
      if (!el) return;
      el.value = value == null ? "" : String(value);
    });

    setChecked("#viz-stacked", display.stacked);
    setChecked("#viz-legend", display.legend);
    setChecked("#viz-data-labels", display.data_labels);
    setChecked("#viz-show-n", display.show_n);
    setChecked("#viz-equal-axes", display.equal_axes);

    const sampling = spec.sampling || {};
    setValue("#viz-sampling", sampling.mode);
    setValue("#viz-sample-method", sampling.method);
    setValue("#viz-sample-size", sampling.size);
    setValue("#viz-sample-seed", sampling.seed);

    const render = $("#viz-render");
    if (render) render.click();

    const status = $("#viz-status");
    if (missing.length) {
      const unique = [...new Set(missing)];
      if (status) {
        status.textContent = `已載入，但保存的欄位目前不存在：${unique.join(", ")} · Saved visualization loaded with missing fields.`;
        status.classList.add("error-box");
      }
    } else if (status) {
      const legacy = item.save_mode === "frozen" && !item.snapshot_hash;
      status.textContent = legacy
        ? "已載入舊版 Frozen 視覺化；舊快照只包含當時保存的單一 Result Section。 Legacy Frozen visualization loaded."
        : "已完整恢復儲存的視覺化設定 Saved visualization restored";
      status.classList.remove("error-box");
    }
  }

  window.fetch = async function treepoloStage4DSavedRestoreFetch(input, init = {}) {
    const url = typeof input === "string" ? input : input?.url || "";
    const method = String(init?.method || "GET").toUpperCase();
    const response = await upstreamFetch(input, init);

    try {
      if (method === "GET" && /\/api\/visualizations\/\d+/.test(url) && response.ok) {
        const body = await response.clone().json();
        if (body?.item) pendingSaved = body.item;
      } else if (method === "POST" && url.includes("/api/visualization/data") && response.ok && pendingSaved) {
        const request = typeof init.body === "string" ? JSON.parse(init.body) : null;
        if (request?.source?.kind === "visualization" && Number(request.source.id) === Number(pendingSaved.id)) {
          const item = pendingSaved;
          pendingSaved = null;
          setTimeout(() => restoreSpec(item), 0);
        }
      }
    } catch {}

    return response;
  };
})();
