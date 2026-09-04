(() => {
  "use strict";

  if (window.__treepoloStage4DLatestRequest) return;
  window.__treepoloStage4DLatestRequest = true;

  const upstreamFetch = window.fetch.bind(window);
  let generation = 0;
  let active = null;

  function isVisualizationDataRequest(url, method, init) {
    return method === "POST" && url.includes("/api/visualization/data") && typeof init?.body === "string";
  }

  function deferred() {
    let resolve;
    let reject;
    const promise = new Promise((res, rej) => {
      resolve = res;
      reject = rej;
    });
    // Superseded callers may be the only consumers of an older slot. Attach a
    // rejection handler immediately so a latest-request failure cannot create
    // an unhandled rejection before those callers join it.
    promise.catch(() => {});
    return {promise, resolve, reject};
  }

  async function waitForLatest() {
    while (true) {
      const slot = active;
      if (!slot) throw new DOMException("Visualization load superseded", "AbortError");
      try {
        const response = await slot.done.promise;
        if (slot === active) return response.clone();
      } catch (error) {
        if (slot === active) throw error;
      }
    }
  }

  function linkCallerSignal(controller, signal) {
    if (!signal) return;
    if (signal.aborted) {
      controller.abort(signal.reason);
      return;
    }
    signal.addEventListener("abort", () => controller.abort(signal.reason), {once:true});
  }

  function resetResultSectionForNewSource() {
    if (typeof document === "undefined") return;
    const section = document.querySelector("#viz-section");
    if (!section) return;
    section.innerHTML = "";
    const option = document.createElement("option");
    option.value = "0";
    option.textContent = "載入資料後顯示 Result sections load with data";
    option.dataset.sourcePending = "true";
    section.append(option);
    section.value = "0";
    section.disabled = true;
    section.dataset.sourcePending = "true";
  }

  function bindSourceSectionLifecycle() {
    if (typeof document === "undefined") return;

    // A Result Section index belongs to one analysis result only. Never carry
    // section 2 from a three-section result into a source that may only expose
    // section 0 or 1. Programmatic Saved Visualization restore does not emit a
    // change event, so its explicitly saved section remains untouched.
    document.addEventListener("change", event => {
      if (!event.target?.matches?.("#viz-source-kind,#viz-source-item")) return;
      resetResultSectionForNewSource();
    }, true);

    document.addEventListener("click", event => {
      if (!event.target?.closest?.("#viz-load")) return;
      const section = document.querySelector("#viz-section");
      if (section?.dataset.sourcePending === "true") section.value = "0";
    }, true);

    const attachObserver = () => {
      const section = document.querySelector("#viz-section");
      if (!section || section.dataset.sourceLifecycleObserved === "true") return;
      section.dataset.sourceLifecycleObserved = "true";
      const observer = new MutationObserver(() => {
        if (section.dataset.sourcePending !== "true") return;
        const stillPending = section.querySelector('option[data-source-pending="true"]');
        if (stillPending) return;
        section.disabled = false;
        delete section.dataset.sourcePending;
      });
      observer.observe(section, {childList:true});
    };

    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", attachObserver, {once:true});
    } else {
      attachObserver();
    }
  }

  window.fetch = async function treepoloStage4DLatestRequestFetch(input, init = {}) {
    const url = typeof input === "string" ? input : input?.url || "";
    const method = String(init?.method || (typeof input !== "string" ? input?.method : "GET") || "GET").toUpperCase();
    if (!isVisualizationDataRequest(url, method, init)) return upstreamFetch(input, init);

    const token = ++generation;
    const previous = active;
    const controller = new AbortController();
    linkCallerSignal(controller, init.signal || (typeof input !== "string" ? input?.signal : null));
    const slot = {
      token,
      controller,
      done: deferred(),
    };
    active = slot;

    // Abort after publishing the new slot. A superseded caller that wakes up
    // immediately can already join the new request instead of surfacing an
    // AbortError into the primary loadData() catch handler.
    previous?.controller.abort("superseded-by-newer-visualization-load");

    const nextInit = {...init, signal: controller.signal};
    try {
      const response = await upstreamFetch(input, nextInit);
      if (slot !== active || token !== generation) return await waitForLatest();
      slot.done.resolve(response.clone());
      return response;
    } catch (error) {
      if (slot !== active || token !== generation || controller.signal.aborted) {
        return await waitForLatest();
      }
      slot.done.reject(error);
      throw error;
    }
  };

  bindSourceSectionLifecycle();
})();
