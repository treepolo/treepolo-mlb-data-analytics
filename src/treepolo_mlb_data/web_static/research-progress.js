(() => {
  "use strict";

  // A progress panel for every research job that is running (training, scoring, the decision engine, the direct check, the evaluations): a bar, the time used and the time still to go, updated live.
  // It reads /api/research/progress; the server works out the time still to go from how far the job is. A job whose method cannot say how far it is gets a bar that follows the time
  // earlier runs of the same kind took, and the panel says so.

  const POLL_RUNNING_MS = 1000;
  const POLL_IDLE_MS = 4000;
  const KIND_LABELS = {
    outcome_model_train: "訓練模型", pitch_policy: "決策引擎", policy_score: "2025 評分", policy_check: "直接檢查", pitch_query: "情境查詢表", action_effects: "固定效應對照",
    model_compare: "比較模型", param_search: "挑選參數", league_model: "聯盟模型", player_profiles: "球員描述", data_profile: "資料概況",
  };

  function minutes(seconds) {
    if (seconds == null || !Number.isFinite(seconds)) return "—";
    const s = Math.max(0, Math.round(seconds));
    if (s < 60) return `${s} 秒`;
    const m = Math.floor(s / 60), r = s % 60;
    return r ? `${m} 分 ${r} 秒` : `${m} 分`;
  }

  function title(job) {
    const kind = String(job.mode || "").replace(/^research:/, "");
    return KIND_LABELS[kind] || kind || "研究";
  }

  // the numbers the panel shows for one job: the width of the bar, the percent text, the time used and the time still to go
  function describe(job) {
    const running = job.status === "running";
    const pct = typeof job.percentage === "number" ? Math.max(0, Math.min(100, job.percentage)) : null;
    const left = running && job.eta_seconds != null ? job.eta_seconds : null;
    return {
      title: title(job),
      width: pct === null ? 35 : pct,
      indeterminate: running && pct === null,
      percentText: pct === null ? "進度計算中" : `${pct.toFixed(pct < 10 ? 1 : 0)}%`,
      elapsedText: `已用 ${minutes(job.elapsed_seconds)}`,
      etaText: !running ? (job.status === "failed" ? "失敗" : "完成") : (left != null ? `預估再 ${minutes(left)}${job.estimated ? "（依先前同類研究估計）" : ""}` : "預估時間計算中"),
      state: running ? "running" : (job.status === "failed" ? "failed" : "done"),
      detail: job.detail || job.stage || "",
    };
  }

  const api = { minutes, describe, KIND_LABELS };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (typeof window === "undefined" || typeof document === "undefined") return;
  window.TreepoloResearchProgress = api;

  let panel = null;
  let timer = null;

  function el(tag, attrs = {}, ...children) {
    const node = document.createElement(tag);
    Object.entries(attrs).forEach(([k, v]) => { if (k === "text") node.textContent = v; else if (k === "style") node.setAttribute("style", v); else node.setAttribute(k, v); });
    children.forEach(c => { if (c) node.append(c); });
    return node;
  }

  function ensurePanel() {
    if (panel && document.body.contains(panel)) return panel;
    panel = el("div", { id: "research-progress-panel", class: "research-progress-panel", hidden: "hidden", "aria-live": "polite" });
    document.body.append(panel);
    return panel;
  }

  function render(jobs) {
    const host = ensurePanel();
    const shown = jobs.filter(j => j.status === "running" || j.status === "success" || j.status === "failed");
    if (!shown.length) { host.hidden = true; host.replaceChildren(); return; }
    host.hidden = false;
    host.replaceChildren(el("div", { class: "research-progress-head", text: "研究進度 Research progress" }), ...shown.map(job => {
      const d = describe(job);
      return el("div", { class: `research-progress-job ${d.state}` },
        el("div", { class: "research-progress-title" }, el("strong", { text: d.title }), el("span", { text: d.percentText })),
        el("div", { class: `research-progress-track${d.indeterminate ? " indeterminate" : ""}` }, el("div", { class: "research-progress-bar", style: `width:${d.width}%` })),
        el("div", { class: "research-progress-meta" }, el("span", { text: d.elapsedText }), el("span", { text: d.etaText })),
        el("div", { class: "research-progress-detail", text: d.detail }));
    }));
  }

  async function poll() {
    let running = false;
    try {
      const response = await fetch("/api/research/progress", { cache: "no-store" });
      if (response.ok) {
        const body = await response.json();
        const jobs = Array.isArray(body.jobs) ? body.jobs : [];
        running = jobs.some(j => j.status === "running");
        render(jobs);
      }
    } catch {
      // the server is not answering: try again later
    }
    timer = setTimeout(poll, running ? POLL_RUNNING_MS : POLL_IDLE_MS);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", poll); else poll();
})();
