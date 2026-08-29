(() => {
  "use strict";

  const STORAGE_KEY = "treepolo_perf_diagnostics";
  const queryEnabled = new URLSearchParams(window.location.search).get("perf") === "1";
  const enabled = queryEnabled || window.localStorage.getItem(STORAGE_KEY) === "1";
  const nativeSetTimeout = window.setTimeout.bind(window);
  const nativeSetInterval = window.setInterval.bind(window);
  const nativeRequestAnimationFrame = window.requestAnimationFrame?.bind(window);
  const diagnosticsFile = "performance-diagnostics.js";

  const state = {
    enabled,
    startedAt: performance.now(),
    aggregates: new Map(),
    slowCallbacks: [],
    longTasks: [],
    eventLoopStalls: [],
    heapPeak: 0,
    heapSamples: [],
  };

  function sourceFromStack(stack) {
    const lines = String(stack || "").split("\n");
    for (const line of lines) {
      if (line.includes(diagnosticsFile)) continue;
      const match = line.match(/\/([^/\s)]+\.js):(\d+):(\d+)/);
      if (match) return `${match[1]}:${match[2]}`;
    }
    return "unknown";
  }

  function captureSource() {
    try { return sourceFromStack(new Error().stack); }
    catch { return "unknown"; }
  }

  function record(kind, source, duration) {
    const key = `${kind} @ ${source}`;
    const item = state.aggregates.get(key) || {
      kind, source, count: 0, total_ms: 0, max_ms: 0, over_16ms: 0, over_50ms: 0,
    };
    item.count += 1;
    item.total_ms += duration;
    item.max_ms = Math.max(item.max_ms, duration);
    if (duration >= 16) item.over_16ms += 1;
    if (duration >= 50) item.over_50ms += 1;
    state.aggregates.set(key, item);
    if (duration >= 8) {
      state.slowCallbacks.push({
        at_ms: performance.now() - state.startedAt,
        kind,
        source,
        duration_ms: duration,
      });
      if (state.slowCallbacks.length > 300) state.slowCallbacks.splice(0, state.slowCallbacks.length - 300);
    }
  }

  function timed(kind, source, callback, thisArg, args) {
    const started = performance.now();
    try { return callback.apply(thisArg, args); }
    finally { record(kind, source, performance.now() - started); }
  }

  function installCallbackInstrumentation() {
    const NativeMutationObserver = window.MutationObserver;
    if (NativeMutationObserver) {
      class InstrumentedMutationObserver extends NativeMutationObserver {
        constructor(callback) {
          const source = captureSource();
          super((records, observer) => timed("MutationObserver", source, callback, observer, [records, observer]));
        }
      }
      window.MutationObserver = InstrumentedMutationObserver;
    }

    const originalSetTimeout = window.setTimeout.bind(window);
    window.setTimeout = function instrumentedSetTimeout(handler, delay, ...args) {
      if (typeof handler !== "function") return originalSetTimeout(handler, delay, ...args);
      const source = captureSource();
      if (source === "unknown") return originalSetTimeout(handler, delay, ...args);
      return originalSetTimeout(function (...callbackArgs) {
        return timed("setTimeout", source, handler, this, callbackArgs);
      }, delay, ...args);
    };

    const originalSetInterval = window.setInterval.bind(window);
    window.setInterval = function instrumentedSetInterval(handler, delay, ...args) {
      if (typeof handler !== "function") return originalSetInterval(handler, delay, ...args);
      const source = captureSource();
      if (source === "unknown") return originalSetInterval(handler, delay, ...args);
      return originalSetInterval(function (...callbackArgs) {
        return timed("setInterval", source, handler, this, callbackArgs);
      }, delay, ...args);
    };

    if (nativeRequestAnimationFrame) {
      window.requestAnimationFrame = function instrumentedRequestAnimationFrame(callback) {
        const source = captureSource();
        if (source === "unknown") return nativeRequestAnimationFrame(callback);
        return nativeRequestAnimationFrame(timestamp => timed("requestAnimationFrame", source, callback, window, [timestamp]));
      };
    }

    if (typeof window.queueMicrotask === "function") {
      const originalQueueMicrotask = window.queueMicrotask.bind(window);
      window.queueMicrotask = function instrumentedQueueMicrotask(callback) {
        const source = captureSource();
        if (source === "unknown") return originalQueueMicrotask(callback);
        return originalQueueMicrotask(() => timed("queueMicrotask", source, callback, window, []));
      };
    }

    const originalAddEventListener = EventTarget.prototype.addEventListener;
    const originalRemoveEventListener = EventTarget.prototype.removeEventListener;
    const listenerRegistry = new WeakMap();

    function captureFlag(options) {
      return typeof options === "boolean" ? options : Boolean(options?.capture);
    }

    function registryFor(target, listener) {
      let byListener = listenerRegistry.get(target);
      if (!byListener) {
        byListener = new WeakMap();
        listenerRegistry.set(target, byListener);
      }
      let wrappers = byListener.get(listener);
      if (!wrappers) {
        wrappers = new Map();
        byListener.set(listener, wrappers);
      }
      return wrappers;
    }

    EventTarget.prototype.addEventListener = function instrumentedAddEventListener(type, listener, options) {
      if (!listener || (typeof listener !== "function" && typeof listener.handleEvent !== "function")) {
        return originalAddEventListener.call(this, type, listener, options);
      }
      const source = captureSource();
      if (source === "unknown") return originalAddEventListener.call(this, type, listener, options);
      const key = `${String(type)}:${captureFlag(options) ? 1 : 0}`;
      const wrappers = registryFor(this, listener);
      let wrapper = wrappers.get(key);
      if (!wrapper) {
        if (typeof listener === "function") {
          wrapper = function (event) {
            return timed(`event:${type}`, source, listener, this, [event]);
          };
        } else {
          wrapper = {
            handleEvent(event) {
              return timed(`event:${type}`, source, listener.handleEvent, listener, [event]);
            },
          };
        }
        wrappers.set(key, wrapper);
      }
      return originalAddEventListener.call(this, type, wrapper, options);
    };

    EventTarget.prototype.removeEventListener = function instrumentedRemoveEventListener(type, listener, options) {
      if (listener && (typeof listener === "function" || typeof listener === "object")) {
        const byListener = listenerRegistry.get(this);
        const wrappers = byListener?.get(listener);
        const key = `${String(type)}:${captureFlag(options) ? 1 : 0}`;
        const wrapper = wrappers?.get(key);
        if (wrapper) return originalRemoveEventListener.call(this, type, wrapper, options);
      }
      return originalRemoveEventListener.call(this, type, listener, options);
    };
  }

  function installLongTaskObserver() {
    if (typeof PerformanceObserver !== "function") return;
    try {
      const supported = PerformanceObserver.supportedEntryTypes || [];
      if (!supported.includes("longtask")) return;
      const observer = new PerformanceObserver(list => {
        for (const entry of list.getEntries()) {
          state.longTasks.push({
            at_ms: entry.startTime,
            duration_ms: entry.duration,
            name: entry.name || "longtask",
            attribution: Array.from(entry.attribution || []).map(item => ({
              name: item.name || "",
              container_type: item.containerType || "",
              container_name: item.containerName || "",
              container_src: item.containerSrc || "",
            })),
          });
        }
        if (state.longTasks.length > 200) state.longTasks.splice(0, state.longTasks.length - 200);
      });
      observer.observe({ type: "longtask", buffered: true });
    } catch {}
  }

  function installEventLoopSampler() {
    let expected = performance.now() + 100;
    nativeSetInterval(() => {
      const now = performance.now();
      const lag = now - expected;
      expected = now + 100;
      if (lag >= 30) {
        state.eventLoopStalls.push({ at_ms: now - state.startedAt, lag_ms: lag });
        if (state.eventLoopStalls.length > 300) state.eventLoopStalls.splice(0, state.eventLoopStalls.length - 300);
      }
    }, 100);

    if (performance.memory) {
      nativeSetInterval(() => {
        const used = Number(performance.memory.usedJSHeapSize || 0);
        state.heapPeak = Math.max(state.heapPeak, used);
        state.heapSamples.push({ at_ms: performance.now() - state.startedAt, used_bytes: used });
        if (state.heapSamples.length > 120) state.heapSamples.shift();
      }, 1000);
    }
  }

  function retainedRows(result) {
    if (!result || typeof result !== "object") return 0;
    if (Array.isArray(result.sections)) {
      return result.sections.reduce((sum, section) => sum + (Array.isArray(section?.rows) ? section.rows.length : 0), 0);
    }
    return Array.isArray(result.rows) ? result.rows.length : 0;
  }

  function snapshotReport() {
    const callbacks = Array.from(state.aggregates.values())
      .map(item => ({
        ...item,
        total_ms: Number(item.total_ms.toFixed(3)),
        max_ms: Number(item.max_ms.toFixed(3)),
      }))
      .sort((a, b) => b.total_ms - a.total_ms)
      .slice(0, 80);
    const current = window.treepoloLastAnalysis;
    const memory = performance.memory ? {
      used_js_heap_bytes: Number(performance.memory.usedJSHeapSize || 0),
      total_js_heap_bytes: Number(performance.memory.totalJSHeapSize || 0),
      js_heap_limit_bytes: Number(performance.memory.jsHeapSizeLimit || 0),
      peak_used_js_heap_bytes: state.heapPeak,
    } : null;
    return {
      generated_at: new Date().toISOString(),
      session_ms: Number((performance.now() - state.startedAt).toFixed(1)),
      page: window.location.href,
      active_panel: document.querySelector(".panel.active-panel")?.id || null,
      dom: {
        elements: document.getElementsByTagName("*").length,
        result_rows_rendered: document.querySelectorAll("#result-content tbody tr").length,
        checklist_items: document.querySelectorAll(".field-check-item").length,
        field_popups: document.querySelectorAll(".xp-field-popup").length,
      },
      analysis: {
        mode: current?.payload?.mode || null,
        cache_hit: Boolean(current?.result?.cache?.hit),
        retained_rows: retainedRows(current?.result),
        history_id: current?.history_id || current?.result?.history_id || null,
      },
      memory,
      callback_aggregates: callbacks,
      slow_callbacks: state.slowCallbacks.slice(-120).map(item => ({
        ...item,
        at_ms: Number(item.at_ms.toFixed(1)),
        duration_ms: Number(item.duration_ms.toFixed(3)),
      })),
      long_tasks: state.longTasks.slice(-100).map(item => ({
        ...item,
        at_ms: Number(item.at_ms.toFixed(1)),
        duration_ms: Number(item.duration_ms.toFixed(3)),
      })),
      event_loop_stalls: state.eventLoopStalls.slice(-150).map(item => ({
        at_ms: Number(item.at_ms.toFixed(1)),
        lag_ms: Number(item.lag_ms.toFixed(3)),
      })),
      heap_samples: state.heapSamples.slice(-60),
    };
  }

  function ensurePanel() {
    let layer = document.querySelector("#perf-diagnostics-layer");
    if (layer) return layer;
    layer = document.createElement("div");
    layer.id = "perf-diagnostics-layer";
    layer.hidden = true;
    layer.style.cssText = "position:fixed;inset:0;z-index:30000;background:rgba(0,0,0,.24);display:grid;place-items:center;padding:18px";
    layer.innerHTML = `
      <section style="width:min(900px,calc(100vw - 36px));height:min(78vh,720px);display:flex;flex-direction:column;background:#ece9d8;border:1px solid #003b7a;box-shadow:0 10px 28px rgba(0,0,0,.45)">
        <div style="padding:5px 8px;background:#0a5dbb;color:white;font-weight:700">效能診斷 Performance Diagnostics</div>
        <textarea id="perf-diagnostics-report" readonly spellcheck="false" style="flex:1;min-height:0;margin:8px;font:11px/1.35 Consolas,monospace;white-space:pre;resize:none"></textarea>
        <div style="display:flex;gap:7px;justify-content:flex-end;padding:0 8px 8px">
          <button type="button" data-perf-reset>清除並重新記錄 Reset</button>
          <button type="button" data-perf-copy>複製診斷報告 Copy Report</button>
          <button type="button" data-perf-stop>停止診斷 Stop + Reload</button>
          <button type="button" data-perf-close>關閉 Close</button>
        </div>
      </section>`;
    document.body.append(layer);
    const textarea = layer.querySelector("#perf-diagnostics-report");
    const refresh = () => { textarea.value = JSON.stringify(snapshotReport(), null, 2); };
    layer.querySelector("[data-perf-close]").addEventListener("click", () => { layer.hidden = true; });
    layer.querySelector("[data-perf-copy]").addEventListener("click", async () => {
      refresh();
      try { await navigator.clipboard.writeText(textarea.value); }
      catch { textarea.focus(); textarea.select(); document.execCommand?.("copy"); }
    });
    layer.querySelector("[data-perf-reset]").addEventListener("click", () => {
      state.aggregates.clear();
      state.slowCallbacks.length = 0;
      state.longTasks.length = 0;
      state.eventLoopStalls.length = 0;
      state.heapSamples.length = 0;
      state.heapPeak = performance.memory ? Number(performance.memory.usedJSHeapSize || 0) : 0;
      state.startedAt = performance.now();
      refresh();
    });
    layer.querySelector("[data-perf-stop]").addEventListener("click", () => {
      window.localStorage.removeItem(STORAGE_KEY);
      const url = new URL(window.location.href);
      url.searchParams.delete("perf");
      window.location.replace(url.toString());
    });
    layer.addEventListener("mousedown", event => { if (event.target === layer) layer.hidden = true; });
    layer._refreshReport = refresh;
    return layer;
  }

  function installToolbarButton() {
    const toolbar = document.querySelector(".toolbar");
    if (!toolbar || document.querySelector("#perf-diagnostics-button")) return;
    const separator = document.createElement("div");
    separator.className = "toolbar-separator";
    const button = document.createElement("button");
    button.id = "perf-diagnostics-button";
    button.className = "tool-button";
    button.textContent = enabled ? "效能診斷 Perf: ON" : "效能診斷 Perf: OFF";
    button.addEventListener("click", () => {
      if (!enabled) {
        window.localStorage.setItem(STORAGE_KEY, "1");
        window.location.reload();
        return;
      }
      const layer = ensurePanel();
      layer._refreshReport?.();
      layer.hidden = false;
    });
    toolbar.append(separator, button);
  }

  window.treepoloPerformanceDiagnostics = {
    enabled,
    report: snapshotReport,
    open() {
      if (!enabled) return;
      const layer = ensurePanel();
      layer._refreshReport?.();
      layer.hidden = false;
    },
  };

  if (enabled) {
    installCallbackInstrumentation();
    installLongTaskObserver();
    installEventLoopSampler();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", installToolbarButton, { once: true });
  else installToolbarButton();
})();
