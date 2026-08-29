(() => {
  "use strict";

  if (window.treepoloAnalysisSaveUi) return;
  window.treepoloAnalysisSaveUi = true;

  let activeSource = null;

  const MODE_LABELS = {
    basic: "基本分析 Basic",
    sequence_pattern: "球序模式 Sequence",
    follow_event: "後續事件 Follow-up",
    arsenal: "球種武器庫 Arsenal",
    pitch_role: "球種角色 Pitch Role",
    temporal: "時間序列 Temporal",
    percentile: "個別百分位門檻 Threshold",
    cross_level: "層級比較 Level Comparison",
    arsenal_change: "武器庫變化 Arsenal Change",
    workflow: "研究工作流 Workflow",
    clustering: "自動分群 Clustering",
    regression: "迴歸 Regression",
    bootstrap: "Bootstrap",
    cluster_compare: "多階段分群比較 Cluster Comparison",
  };

  async function api(path, options = {}) {
    const response = await fetch(path, {
      headers: { "Content-Type": "application/json" },
      ...options,
    });
    let body = {};
    try { body = await response.json(); } catch {}
    if (!response.ok) throw new Error(body.error || `${response.status} ${response.statusText}`);
    return body;
  }

  function modeLabel(mode) {
    return MODE_LABELS[mode] || mode || "分析 Analysis";
  }

  function setStatus(message) {
    const status = document.querySelector("#status-message");
    if (status) status.textContent = message;
  }

  function injectStyles() {
    if (document.querySelector("#analysis-save-ui-styles")) return;
    const style = document.createElement("style");
    style.id = "analysis-save-ui-styles";
    style.textContent = `
      .xp-save-dialog-layer{position:fixed;inset:0;z-index:20050;display:grid;place-items:center;background:rgba(0,0,0,.18);padding:18px}
      .xp-save-dialog-layer[hidden]{display:none}
      .xp-save-dialog{width:min(470px,calc(100vw - 36px));border:1px solid #003b7a;border-radius:5px 5px 1px 1px;background:#ece9d8;box-shadow:0 10px 28px rgba(0,0,0,.42),inset 0 0 0 1px #fff}
      .xp-save-dialog-title{min-height:30px;display:flex;align-items:center;gap:8px;padding:3px 4px 3px 8px;color:#fff;font-weight:700;text-shadow:1px 1px 1px #003b7a;background:linear-gradient(rgba(255,255,255,.36),rgba(255,255,255,.04) 46%,rgba(0,0,0,.12) 48%,rgba(0,0,0,.02)),linear-gradient(90deg,#003b92,#2677df 28%,#0f67c8);border-bottom:1px solid #00336f}
      .xp-save-dialog-title-text{flex:1}
      .xp-save-dialog-close{width:24px;min-width:24px;min-height:21px;padding:0;color:#fff;font-weight:700;border:1px solid rgba(255,255,255,.8);border-radius:3px;background:linear-gradient(#f59b7c,#d84c2d 55%,#a82b17);box-shadow:inset 0 0 0 1px rgba(110,20,5,.45)}
      .xp-save-dialog-body{padding:12px;background:#ece9d8}
      .xp-save-source{margin-bottom:10px;padding:6px 8px;border:1px solid #aca899;background:#f7f6ef;box-shadow:inset 1px 1px 1px rgba(0,0,0,.08);color:#333}
      .xp-save-dialog-body label{display:flex;flex-direction:column;gap:4px;margin:0 0 9px;font-weight:700;color:#333}
      .xp-save-dialog-body textarea{min-height:74px;resize:vertical;border:1px solid #7f9db9;border-radius:1px;background:#fff;color:#111;padding:4px;font:inherit;box-shadow:inset 1px 1px 1px rgba(0,0,0,.08)}
      .xp-save-dialog-body textarea:focus{outline:1px dotted #111;outline-offset:-2px}
      .xp-save-dialog-error{min-height:17px;margin:-2px 0 6px;color:#a40000;font-weight:700}
      .xp-save-dialog-buttons{display:flex;justify-content:flex-end;gap:7px;padding-top:3px}
    `;
    document.head.append(style);
  }

  function ensureDialog() {
    let layer = document.querySelector("#analysis-save-dialog-layer");
    if (layer) return layer;
    layer = document.createElement("div");
    layer.id = "analysis-save-dialog-layer";
    layer.className = "xp-save-dialog-layer";
    layer.hidden = true;
    layer.innerHTML = `
      <section class="xp-save-dialog" role="dialog" aria-modal="true" aria-labelledby="analysis-save-dialog-title">
        <div class="xp-save-dialog-title">
          <span id="analysis-save-dialog-title" class="xp-save-dialog-title-text">另存分析 Save Analysis As</span>
          <button type="button" class="xp-save-dialog-close" aria-label="關閉 Close">×</button>
        </div>
        <div class="xp-save-dialog-body">
          <div class="xp-save-source"></div>
          <label>名稱 Name<input class="xp-save-name" type="text" autocomplete="off"></label>
          <label>備註 Notes<textarea class="xp-save-notes"></textarea></label>
          <div class="xp-save-dialog-error" aria-live="polite"></div>
          <div class="xp-save-dialog-buttons">
            <button type="button" class="xp-save-cancel">取消 Cancel</button>
            <button type="button" class="xp-save-confirm primary">儲存 Save</button>
          </div>
        </div>
      </section>`;
    document.body.append(layer);
    layer.querySelector(".xp-save-dialog-close").addEventListener("click", closeSaveDialog);
    layer.querySelector(".xp-save-cancel").addEventListener("click", closeSaveDialog);
    layer.addEventListener("mousedown", event => { if (event.target === layer) closeSaveDialog(); });
    layer.querySelector(".xp-save-confirm").addEventListener("click", () => commitSave().catch(showDialogError));
    layer.querySelector(".xp-save-name").addEventListener("keydown", event => {
      if (event.key !== "Enter") return;
      event.preventDefault();
      commitSave().catch(showDialogError);
    });
    document.addEventListener("keydown", event => {
      if (event.key === "Escape" && !layer.hidden) closeSaveDialog();
    });
    return layer;
  }

  function showDialogError(error) {
    const host = ensureDialog().querySelector(".xp-save-dialog-error");
    if (host) host.textContent = error?.message || String(error);
  }

  function sourceDescription(source) {
    const mode = modeLabel(source?.payload?.mode);
    if (source?.kind === "history") return `來源 Source: History · ${source.label || mode}`;
    if (source?.kind === "saved") return `來源 Source: Saved Analysis · ${source.label || mode}`;
    return `來源 Source: 目前分析結果 Current Result · ${mode}`;
  }

  function openSaveDialog(source) {
    if (!source?.payload || typeof source.payload !== "object") {
      setStatus("目前沒有可儲存的分析。 No analysis is available to save.");
      return;
    }
    activeSource = source;
    const layer = ensureDialog();
    layer.querySelector(".xp-save-source").textContent = sourceDescription(source);
    layer.querySelector(".xp-save-name").value = "";
    layer.querySelector(".xp-save-notes").value = "";
    layer.querySelector(".xp-save-dialog-error").textContent = "";
    layer.hidden = false;
    requestAnimationFrame(() => layer.querySelector(".xp-save-name")?.focus());
  }

  function closeSaveDialog() {
    const layer = document.querySelector("#analysis-save-dialog-layer");
    if (layer) layer.hidden = true;
    activeSource = null;
  }

  async function resolveSource(source) {
    const historyId = Number(source?.history_id || 0);
    if (!historyId) return source;
    const body = await api(`/api/analysis/history/${historyId}`);
    const item = body.item;
    if (!item?.payload) throw new Error("找不到此分析的歷史紀錄。 Analysis history record is unavailable.");
    return {
      ...source,
      payload: item.payload,
      cache_key: item.cache_key || null,
      data_revision: item.data_revision || null,
      result_available: Boolean(item.result_available),
    };
  }

  async function saveSource(source, name, notes) {
    const resolved = await resolveSource(source);
    return api("/api/analysis/saved", {
      method: "POST",
      body: JSON.stringify({
        name,
        notes,
        analysis_payload: resolved.payload,
        cache_key: resolved.cache_key || null,
        data_revision: resolved.data_revision || null,
      }),
    });
  }

  async function commitSave() {
    if (!activeSource) throw new Error("沒有可儲存的分析來源。 No analysis source is selected.");
    const layer = ensureDialog();
    const name = layer.querySelector(".xp-save-name")?.value.trim() || "";
    const notes = layer.querySelector(".xp-save-notes")?.value || "";
    if (!name) throw new Error("請輸入分析名稱。 Analysis name is required.");
    const confirm = layer.querySelector(".xp-save-confirm");
    confirm.disabled = true;
    try {
      await saveSource(activeSource, name, notes);
      closeSaveDialog();
      setStatus(`已儲存分析 Saved analysis: ${name}`);
      document.dispatchEvent(new CustomEvent("treepolo:analysis-library-refresh-request"));
    } finally {
      confirm.disabled = false;
    }
  }

  function init() {
    injectStyles();
    ensureDialog();
    document.addEventListener("treepolo:analysis-save-request", event => openSaveDialog(event.detail?.source || null));
  }

  window.treepoloAnalysisSaveUiApi = { open: openSaveDialog };

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init, { once:true });
  else init();
})();
