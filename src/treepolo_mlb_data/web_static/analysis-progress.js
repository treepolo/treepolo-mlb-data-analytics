(() => {
  "use strict";

  let timer = null;
  let active = false;

  const STAGE_LABELS = {
    queued: "等待開始 Queued",
    cache_hit: "直接讀取快取 Loading Cached Result",
    building_analysis: "建立分析計畫 Building Analysis Plan",
    planning: "規劃執行 Preparing Execution",
    analytics_mirror_wait: "檢查分析資料庫 Checking Analytics Database",
    analytics_mirror_rebuild: "首次建立 DuckDB 分析鏡像 Building DuckDB Mirror",
    analytics_mirror_sync: "同步 DuckDB 分析鏡像 Refreshing DuckDB Mirror",
    analytics_mirror_ready: "分析鏡像就緒 Analytics Mirror Ready",
    duckdb_query: "執行 DuckDB 分析 Running DuckDB Query",
    sqlite_fallback: "SQLite 備援分析 SQLite Fallback",
    sqlite_query: "執行 SQLite 分析 Running SQLite Query",
    formatting: "整理結果 Formatting Result",
    numerical_prepare: "準備數值資料 Preparing Numerical Data",
    numerical_compute: "執行數值分析 Running Numerical Analysis",
    completed: "分析完成 Analysis Complete",
    failed: "分析失敗 Analysis Failed",
  };

  function ensurePanel() {
    const result = document.querySelector("#result-window");
    if (!result) return null;
    let panel = document.querySelector("#analysis-progress-panel");
    if (panel) return panel;
    panel = document.createElement("div");
    panel.id = "analysis-progress-panel";
    panel.className = "analysis-progress-panel";
    panel.hidden = true;
    panel.innerHTML = `
      <div class="analysis-progress-title">分析進度 Analysis Progress</div>
      <div class="analysis-progress-track"><div class="analysis-progress-bar"></div></div>
      <div class="analysis-progress-meta">
        <strong class="analysis-progress-stage">等待開始 Queued</strong>
        <span class="analysis-progress-percent">—</span>
        <span class="analysis-progress-elapsed">0.0s</span>
        <span class="analysis-progress-backend">—</span>
      </div>
      <div class="analysis-progress-detail muted"></div>`;
    result.insertAdjacentElement("beforebegin", panel);
    return panel;
  }

  function render(progress) {
    const panel = ensurePanel();
    if (!panel || !progress) return;
    panel.hidden = false;
    const percent = typeof progress.percentage === "number" ? progress.percentage : null;
    const track = panel.querySelector(".analysis-progress-track");
    const bar = panel.querySelector(".analysis-progress-bar");
    track.classList.toggle("indeterminate", percent === null && progress.status === "running");
    bar.style.width = percent === null ? "35%" : `${Math.max(0, Math.min(percent, 100))}%`;
    panel.querySelector(".analysis-progress-stage").textContent = STAGE_LABELS[progress.stage] || progress.stage || "分析中 Working";
    panel.querySelector(".analysis-progress-percent").textContent = percent === null ? "進度計算中 Progress unavailable" : `${percent.toFixed(1)}%`;
    panel.querySelector(".analysis-progress-elapsed").textContent = `已耗時 Elapsed: ${Number(progress.elapsed_seconds || 0).toFixed(1)}s`;
    panel.querySelector(".analysis-progress-backend").textContent = progress.backend ? `執行器 Backend: ${progress.backend}` : "";
    panel.querySelector(".analysis-progress-detail").textContent = progress.detail || "";
  }

  async function poll() {
    if (!active) return;
    try {
      const response = await fetch("/api/analysis/progress", { cache: "no-store" });
      if (response.ok) {
        const body = await response.json();
        if (body.progress) {
          render(body.progress);
          if (body.progress.status !== "running") {
            active = false;
            timer = null;
            return;
          }
        }
      }
    } catch {
      // Keep polling; the analysis request itself owns error reporting.
    }
    timer = setTimeout(poll, 250);
  }

  function start() {
    const panel = ensurePanel();
    if (panel) {
      panel.hidden = false;
      render({ status: "running", stage: "queued", percentage: 0, elapsed_seconds: 0, detail: "正在建立分析工作 Starting analysis job" });
    }
    active = true;
    if (timer) clearTimeout(timer);
    timer = setTimeout(poll, 80);
  }

  function finish() {
    // One final poll captures success/failure/backend after the POST resolves.
    if (!active) return;
    setTimeout(poll, 0);
  }

  function loadStage4Pages() {
    if (document.querySelector('script[data-treepolo-stage4-pages]')) return;
    const script = document.createElement("script");
    script.src = "/stage4-analysis-pages.js";
    script.dataset.treepoloStage4Pages = "true";
    document.head.append(script);
  }

  window.treepoloAnalysisProgress = { start, finish };
  loadStage4Pages();
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", ensurePanel, { once: true });
  else ensurePanel();
})();
