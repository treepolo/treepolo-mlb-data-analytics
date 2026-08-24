(() => {
  "use strict";

  function formatDuration(seconds) {
    if (seconds === null || seconds === undefined || !Number.isFinite(Number(seconds))) return "—";
    let value = Math.max(0, Math.round(Number(seconds)));
    const hours = Math.floor(value / 3600);
    value %= 3600;
    const minutes = Math.floor(value / 60);
    const secs = value % 60;
    if (hours) return `${hours} 小時 ${minutes} 分 / ${hours}h ${minutes}m`;
    if (minutes) return `${minutes} 分 ${secs} 秒 / ${minutes}m ${secs}s`;
    return `${secs} 秒 / ${secs}s`;
  }

  function number(value) {
    return Number(value || 0).toLocaleString("zh-TW");
  }

  function install() {
    const runButton = document.querySelector("#run-backfill");
    const fieldset = runButton && runButton.closest("fieldset");
    if (!fieldset || document.querySelector("#backfill-progress-panel")) return;

    const style = document.createElement("style");
    style.textContent = `
      #backfill-progress-panel { margin-top: 10px; padding: 8px 10px; border: 1px solid #9aa8b5; background: linear-gradient(#f7f9fb, #e7edf3); box-shadow: inset 1px 1px #fff; color: #23384d; }
      .bf-progress-title { font-weight: 700; margin-bottom: 6px; color: #174f8b; }
      .bf-progress-track { height: 18px; border: 1px solid #6e7f8e; background: #fff; box-shadow: inset 1px 1px 2px #b8c2cc; overflow: hidden; }
      .bf-progress-fill { height: 100%; width: 0%; background: linear-gradient(#86d75d, #39a52d 52%, #75cf54); border-right: 1px solid #2d8425; transition: width .25s linear; }
      .bf-progress-summary { margin-top: 5px; font-weight: 700; }
      .bf-progress-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 3px 16px; margin-top: 6px; font-size: 12px; }
      .bf-progress-grid span { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
      @media (max-width: 900px) { .bf-progress-grid { grid-template-columns: 1fr; } }
    `;
    document.head.append(style);

    const panel = document.createElement("div");
    panel.id = "backfill-progress-panel";
    panel.innerHTML = `
      <div class="bf-progress-title">下載進度 Backfill Progress</div>
      <div class="bf-progress-track"><div id="bf-progress-fill" class="bf-progress-fill"></div></div>
      <div id="bf-progress-summary" class="bf-progress-summary">尚無執行中的歷史回補 No active backfill</div>
      <div class="bf-progress-grid">
        <span id="bf-progress-current">目前區段 Current: —</span>
        <span id="bf-progress-rows">本次逐球數 Rows: 0</span>
        <span id="bf-progress-failed">失敗區段 Failed: 0</span>
        <span id="bf-progress-elapsed">已耗時 Elapsed: —</span>
        <span id="bf-progress-eta">預估剩餘 ETA: —</span>
        <span id="bf-progress-range">範圍 Range: —</span>
      </div>
    `;
    fieldset.append(panel);
  }

  function render(progress) {
    if (!progress) return;
    const percent = Math.max(0, Math.min(100, Number(progress.percent || 0)));
    document.querySelector("#bf-progress-fill").style.width = `${percent}%`;

    const status = {
      running: "進行中 Running",
      success: "完成 Completed",
      partial: "完成但有失敗區段 Completed with Failures",
      failed: "失敗 Failed",
    }[progress.status] || String(progress.status || "—");

    document.querySelector("#bf-progress-summary").textContent =
      `${status} — ${number(progress.completed_chunks)} / ${number(progress.total_chunks)} 區段 Chunks (${percent.toFixed(1)}%)`;

    const current = progress.current_start
      ? `${progress.current_start} ～ ${progress.current_end}`
      : "—";
    document.querySelector("#bf-progress-current").textContent = `目前區段 Current: ${current}`;
    document.querySelector("#bf-progress-rows").textContent = `本次逐球數 Rows: ${number(progress.rows_received)}`;
    document.querySelector("#bf-progress-failed").textContent = `失敗區段 Failed: ${number(progress.failed_chunks)}`;
    document.querySelector("#bf-progress-elapsed").textContent = `已耗時 Elapsed: ${formatDuration(progress.elapsed_seconds)}`;
    document.querySelector("#bf-progress-eta").textContent = `預估剩餘 ETA: ${formatDuration(progress.eta_seconds)}`;
    document.querySelector("#bf-progress-range").textContent = `範圍 Range: ${progress.start_date} ～ ${progress.end_date}`;
  }

  async function poll() {
    try {
      const response = await fetch("/api/data/backfill-progress", { cache: "no-store" });
      if (!response.ok) return;
      const body = await response.json();
      render(body.progress);
    } catch (_) {
      // Progress is auxiliary; transient polling failures should not interrupt the backfill.
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", install, { once: true });
  } else {
    install();
  }
  setTimeout(poll, 100);
  setInterval(poll, 1000);
})();
