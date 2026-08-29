(() => {
  "use strict";

  function loadScriptOnce(src, marker) {
    const existing = Array.from(document.scripts).find(script => script.src.endsWith(src));
    if (existing) {
      if (existing.dataset.loaded === "1" || existing.readyState === "complete") return Promise.resolve();
      return new Promise(resolve => {
        existing.addEventListener("load", resolve, { once:true });
        existing.addEventListener("error", resolve, { once:true });
      });
    }
    return new Promise(resolve => {
      const script = document.createElement("script");
      script.src = src;
      script.dataset[marker] = "1";
      script.addEventListener("load", () => { script.dataset.loaded = "1"; resolve(); }, { once:true });
      script.addEventListener("error", resolve, { once:true });
      document.head.append(script);
    });
  }

  function loadStyleOnce(href, marker) {
    const existing = Array.from(document.querySelectorAll('link[rel="stylesheet"]')).find(link => link.href.endsWith(href));
    if (existing) return Promise.resolve();
    return new Promise(resolve => {
      const link = document.createElement("link");
      link.rel = "stylesheet";
      link.href = href;
      link.dataset[marker] = "1";
      link.addEventListener("load", resolve, { once:true });
      link.addEventListener("error", resolve, { once:true });
      document.head.append(link);
    });
  }

  function fieldCatalogReady() {
    return Boolean((window.treepoloFieldCatalog?.fields?.() || []).length);
  }

  function waitForFieldCatalog() {
    if (fieldCatalogReady()) return Promise.resolve();
    return new Promise(resolve => {
      const onFieldsUpdated = () => {
        if (!fieldCatalogReady()) return;
        document.removeEventListener("treepolo:fields-updated", onFieldsUpdated);
        resolve();
      };
      document.addEventListener("treepolo:fields-updated", onFieldsUpdated);
    });
  }

  function commitFieldLegality() {
    const provider = window.treepoloLegalFieldOptions;
    if (!provider) {
      console.error("Field legality provider failed to initialize");
      return;
    }
    provider.refresh?.();
    window.treepoloFieldChecklistsApi?.refreshRoot?.(document);
    window.treepoloUnifiedFieldControlsApi?.scan?.(document);
    window.treepoloUnifiedFieldControlsApi?.sync?.();
    document.dispatchEvent(new CustomEvent("treepolo:field-legality-ready", {
      detail: {
        source:"field-catalog",
        fieldCount:(window.treepoloFieldCatalog?.fields?.() || []).length,
      },
    }));
  }

  async function loadUiEnhancements() {
    // Field legality is a foundational dependency of both checklist and
    // single-field controls. Load it before the rest of the enhancement layer.
    await loadScriptOnce("/field-option-legality-v3.js", "fieldOptionLegality");

    await loadScriptOnce("/performance-diagnostics.js", "performanceDiagnostics");
    await loadScriptOnce("/result-paging.js", "resultPaging");
    await loadScriptOnce("/acceptance-fixes.js", "acceptanceFixes");
    await loadScriptOnce("/cluster-comparison-page.js", "clusterComparisonPage");
    await loadScriptOnce("/field-controls-unified.js", "unifiedFieldControls");
    await loadStyleOnce("/field-controls-native-arrow.css", "nativeFieldArrow");
    await loadScriptOnce("/field-controls-native-arrow.js", "nativeFieldArrowDonor");
    await loadScriptOnce("/ui-consistency-fixes.js", "uiConsistencyFixes");
    await loadScriptOnce("/navigation-routes.js", "navigationRoutes");

    await waitForFieldCatalog();
    commitFieldLegality();
  }
  loadUiEnhancements();

  let timer = null;

  function ensureNotice() {
    const host = document.querySelector("#data-status");
    if (!host) return null;
    let notice = document.querySelector("#fast-status-notice");
    if (!notice) {
      notice = document.createElement("div");
      notice.id = "fast-status-notice";
      notice.className = "hint";
      host.insertAdjacentElement("afterend", notice);
    }
    return notice;
  }

  async function poll() {
    try {
      const response = await fetch("/api/data/status", { cache:"no-store" });
      if (!response.ok) return;
      const status = await response.json();
      const notice = ensureNotice();
      if (!notice) return;
      if (status.summary_state === "ready") {
        notice.remove();
        if (timer) clearTimeout(timer);
        timer = null;
        const button = document.querySelector("#status-refresh");
        if (button && !button.disabled) button.click();
        return;
      }
      const message = status.summary_state === "error"
        ? "快速摘要建立失敗；可重新啟動程式再試。 Fast status bootstrap failed; restart to retry."
        : "正在背景建立快速摘要；介面可先使用。 Building fast status summary in background; the interface remains usable.";
      if (notice.textContent !== message) notice.textContent = message;
      timer = setTimeout(poll, 1000);
    } catch {
      timer = setTimeout(poll, 2000);
    }
  }

  document.addEventListener("DOMContentLoaded", () => setTimeout(poll, 300));
})();
