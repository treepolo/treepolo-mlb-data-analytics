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

  async function loadUiEnhancements() {
    // Acceptance controls create the Stage 4 fields first. Cluster Comparison is
    // another dynamic page. The classic control layer restores the XP visual
    // language, then the legality layer narrows every field popup to choices that
    // are actually available and semantically valid in that exact control.
    await loadScriptOnce("/acceptance-fixes.js", "acceptanceFixes");
    await loadScriptOnce("/cluster-comparison-page.js", "clusterComparisonPage");
    await loadScriptOnce("/field-controls-classic.js", "classicFieldControls");
    await loadScriptOnce("/field-option-legality.js", "fieldOptionLegality");
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
      const response = await fetch("/api/data/status", { cache: "no-store" });
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
      notice.textContent = status.summary_state === "error"
        ? "快速摘要建立失敗；可重新啟動程式再試。 Fast status bootstrap failed; restart to retry."
        : "正在背景建立快速摘要；介面可先使用。 Building fast status summary in background; the interface remains usable.";
      timer = setTimeout(poll, 1000);
    } catch {
      timer = setTimeout(poll, 2000);
    }
  }

  document.addEventListener("DOMContentLoaded", () => setTimeout(poll, 300));
})();
