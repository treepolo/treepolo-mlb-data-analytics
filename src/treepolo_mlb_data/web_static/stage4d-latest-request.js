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
})();
