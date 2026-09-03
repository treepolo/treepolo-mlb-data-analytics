(() => {
  "use strict";

  if (window.__treepoloStage4DLoadStability) return;
  window.__treepoloStage4DLoadStability = true;

  const upstreamFetch = window.fetch.bind(window);
  let generation = 0;
  let pendingSaved = null;
  let latestScheduledGeneration = 0;

  const $ = selector => document.querySelector(selector);

  function savedSectionIndex(item) {
    if (item?.save_mode === "frozen" && !item?.snapshot_hash) return 0;
    const parsed = Number(item?.section_index ?? 0);
    return Number.isFinite(parsed) && parsed >= 0 ? parsed : 0;
  }

  function setSelect(selector, value, missing) {
    const el = $(selector);
    if (!el) return;
    const wanted = value == null ? "" : String(value);
    if ([...el.options].some(option => option.value === wanted)) el.value = wanted;
    else if (wanted) missing.push(wanted);
  }

  function setValue(selector, value) {
    const el = $(selector);
    if (!el) return;
    el.value = value == null ? "" : String(value);
  }

  function setChecked(selector, value) {
    const el = $(selector);
    if (el && value != null) el.checked = Boolean(value);
  }

  function restoreSavedSpec(item, prepared, token) {
    if (token !== latestScheduledGeneration) return;
    const spec = item?.spec;
    if (!spec || typeof spec !== "object") return;

    const expectedSection = savedSectionIndex(item);
    const section = $("#viz-section");
    if (section && [...section.options].some(option => Number(option.value) === expectedSection)) {
      section.value = String(expectedSection);
    }

    const missing = [];
    setSelect("#viz-type", spec.type || "scatter", missing);
    setSelect("#viz-preset", spec.preset || "", []);

    const mapping = spec.mapping || {};
    for (const key of ["x", "y", "series", "label", "lower", "upper"]) {
      setSelect(`#viz-${key}`, mapping[key] || "", missing);
    }

    const display = spec.display || {};
    for (const [selector, value] of Object.entries({
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
    })) setValue(selector, value);

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
    const appStatus = $("#status-message");
    let message;
    let error = false;
    if (missing.length) {
      const unique = [...new Set(missing)];
      message = `已載入，但保存的欄位目前不存在：${unique.join(", ")} · Saved visualization loaded with missing fields.`;
      error = true;
    } else if (item.save_mode === "frozen" && !item.snapshot_hash) {
      message = "已載入舊版 Frozen 視覺化；舊快照只包含當時保存的單一 Result Section。 Legacy Frozen visualization loaded.";
    } else {
      message = "已完整恢復儲存的視覺化設定 Saved visualization restored";
    }
    if (status) {
      status.textContent = message;
      status.classList.toggle("error-box", error);
    }
    if (appStatus) appStatus.textContent = message;

    if (prepared && Number(prepared.section_index) !== expectedSection) {
      const mismatch = `Saved visualization section mismatch: expected ${expectedSection}, received ${prepared.section_index}.`;
      if (status) {
        status.textContent = mismatch;
        status.classList.add("error-box");
      }
      if (appStatus) appStatus.textContent = mismatch;
    }
  }

  function scheduleRestore(item, prepared, token) {
    latestScheduledGeneration = token;
    let attempts = 0;
    const tryRestore = () => {
      if (token !== latestScheduledGeneration) return;
      attempts += 1;
      const expected = savedSectionIndex(item);
      const section = $("#viz-section");
      const wantedY = item?.spec?.mapping?.y;
      const y = $("#viz-y");
      const sectionReady = !section || [...section.options].some(option => Number(option.value) === expected);
      const mappingReady = !wantedY || (y && [...y.options].some(option => option.value === String(wantedY)));
      if ((sectionReady && mappingReady) || attempts >= 20) {
        restoreSavedSpec(item, prepared, token);
        return;
      }
      requestAnimationFrame(tryRestore);
    };
    setTimeout(tryRestore, 0);
  }

  async function maybeUpgradeToFullResult(input, requestInit, requestBody, response) {
    if (!response.ok) return {response, prepared: null};
    let prepared = null;
    try { prepared = await response.clone().json(); } catch { return {response, prepared: null}; }
    if (!prepared?.requires_rerun) return {response, prepared};

    const fullRequest = {...requestBody, allow_rerun: true};
    const nextInit = {...requestInit, body: JSON.stringify(fullRequest)};
    const fullResponse = await upstreamFetch(input, nextInit);
    if (!fullResponse.ok) return {response: fullResponse, prepared: null};
    try { prepared = await fullResponse.clone().json(); } catch { prepared = null; }
    return {response: fullResponse, prepared};
  }

  window.fetch = async function treepoloStage4DLoadStabilityFetch(input, init = {}) {
    const url = typeof input === "string" ? input : input?.url || "";
    const method = String(init?.method || (typeof input !== "string" ? input?.method : "GET") || "GET").toUpperCase();

    if (method === "GET" && /\/api\/visualizations\/\d+(?:\?.*)?$/.test(url)) {
      const response = await upstreamFetch(input, init);
      try {
        if (response.ok) {
          const body = await response.clone().json();
          if (body?.item) {
            const token = ++generation;
            pendingSaved = {item: body.item, token};
            latestScheduledGeneration = token;
          }
        }
      } catch {}
      return response;
    }

    if (method !== "POST" || !url.includes("/api/visualization/data") || typeof init.body !== "string") {
      return upstreamFetch(input, init);
    }

    let requestBody;
    try { requestBody = JSON.parse(init.body); }
    catch { return upstreamFetch(input, init); }

    let activeSaved = null;
    if (
      pendingSaved &&
      requestBody?.source?.kind === "visualization" &&
      Number(requestBody.source.id) === Number(pendingSaved.item?.id)
    ) {
      activeSaved = pendingSaved;
      pendingSaved = null;
      requestBody.section = savedSectionIndex(activeSaved.item);
    }

    const requestInit = {...init, body: JSON.stringify(requestBody)};
    let response = await upstreamFetch(input, requestInit);
    const upgraded = await maybeUpgradeToFullResult(input, requestInit, requestBody, response);
    response = upgraded.response;

    if (activeSaved && response.ok) {
      let prepared = upgraded.prepared;
      if (!prepared) {
        try { prepared = await response.clone().json(); } catch { prepared = null; }
      }
      if (prepared?.result_available) scheduleRestore(activeSaved.item, prepared, activeSaved.token);
    }

    return response;
  };

  function hideLegacyRerunButton() {
    const button = $("#viz-rerun");
    if (button) button.style.display = "none";
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", hideLegacyRerunButton, {once:true});
  } else hideLegacyRerunButton();
})();
