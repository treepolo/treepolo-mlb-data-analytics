(() => {
  "use strict";

  const checklistScript = document.createElement("script");
  checklistScript.src = "/field-checklists.js";
  document.head.append(checklistScript);

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
