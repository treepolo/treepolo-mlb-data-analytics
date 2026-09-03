(() => {
  "use strict";

  if (window.__treepoloStage4DLoadStability) return;
  window.__treepoloStage4DLoadStability = true;

  const upstreamFetch = window.fetch.bind(window);
  let generation = 0;
  let pendingSaved = null;
  let pendingFinalRestore = null;
  let scheduled = false;

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

  function savedLoadHasFinished(item) {
    const sourceKind = $("#viz-source-kind")?.value;
    const sourceItem = $("#viz-source-item")?.value;
    const status = $("#viz-status")?.textContent || "";
    if (sourceKind !== "visualization" || Number(sourceItem) !== Number(item?.id)) return false;
    return status.includes("Saved visualization loaded") || status.includes("已載入儲存的視覺化");
  }

  function fieldsAreReady(item) {
    const expected = savedSectionIndex(item);
    const section = $("#viz-section");
    if (section && ![...section.options].some(option => Number(option.value) === expected)) return false;
    const mapping = item?.spec?.mapping || {};
    for (const key of ["x", "y", "series", "label", "lower", "upper"]) {
      const wanted = mapping[key];
      if (!wanted) continue;
      const select = $(`#viz-${key}`);
      if (!select || ![...select.options].some(option => option.value === String(wanted))) return false;
    }
    return true;
  }

  function restoreSavedSpec(item, prepared, token) {
    if (!pendingFinalRestore || token !== pendingFinalRestore.token) return;
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

    // The primary Saved Visualization loader has fully finished at this point.
    // This render is deliberately the final presentation write for this load.
    $("#viz-render")?.click();

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
    pendingFinalRestore = null;
  }

  function scheduleFinalRestore() {
    if (scheduled) return;
    scheduled = true;
    requestAnimationFrame(() => {
      scheduled = false;
      const pending = pendingFinalRestore;
      if (!pending) return;
      if (!savedLoadHasFinished(pending.item) || !fieldsAreReady(pending.item)) return;
      restoreSavedSpec(pending.item, pending.prepared, pending.token);
    });
  }

  function watchForLoadCompletion() {
    const status = $("#viz-status");
    if (!status) return;
    const observer = new MutationObserver(scheduleFinalRestore);
    observer.observe(status, {childList:true, subtree:true, characterData:true});
    document.addEventListener("change", event => {
      if (event.target?.matches?.("#viz-source-kind,#viz-source-item,#viz-section,#viz-type,#viz-x,#viz-y,#viz-series,#viz-label,#viz-lower,#viz-upper")) {
        scheduleFinalRestore();
      }
    }, true);
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
            pendingFinalRestore = null;
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
      if (prepared?.result_available) {
        pendingFinalRestore = {item: activeSaved.item, prepared, token: activeSaved.token};
        scheduleFinalRestore();
      }
    }

    return response;
  };

  function hideLegacyRerunButton() {
    const style = document.createElement("style");
    style.id = "stage4d-hide-rerun";
    style.textContent = "#viz-rerun{display:none!important}";
    document.head.append(style);
  }

  function boot() {
    hideLegacyRerunButton();
    watchForLoadCompletion();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot, {once:true});
  else boot();
})();
