(() => {
  "use strict";

  const SOURCES = [
    { source:"pitch3d", dataset:"mlb", key:"pitch3d-mlb", title:"Pitch3D 三維球路資料 — MLB", note:"每位投手整包抓取完整 Pitch3D CSV；所有來源欄位完整保存。" },
    { source:"pitch3d", dataset:"milb", key:"pitch3d-milb", title:"Pitch3D 三維球路資料 — MiLB", note:"與 MLB 明確分離，使用 minors=1；涵蓋範圍取決於實際有追蹤資料的場地與賽事。" },
    { source:"spin_aggregate", dataset:"mlb", key:"spin-aggregate", title:"Hawk-Eye 旋轉／縫線姿態聚合資料", note:"球員 × 球季 × 球種聚合資料；完整保存 image_spin_x/y/z、image_orientation_angle、hawkeye_measured 等來源欄位。" },
  ];

  function fmtDuration(seconds) {
    if (seconds == null || !Number.isFinite(Number(seconds))) return "—";
    let value = Math.max(0, Math.round(Number(seconds)));
    const h = Math.floor(value / 3600); value %= 3600;
    const m = Math.floor(value / 60); const s = value % 60;
    return h ? `${h}h ${m}m` : m ? `${m}m ${s}s` : `${s}s`;
  }
  const num = value => Number(value || 0).toLocaleString("zh-TW");

  async function post(action, payload) {
    const response = await fetch(`/api/data/${action}`, {
      method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(payload || {}),
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.error || `HTTP ${response.status}`);
    return body;
  }

  function blockHtml(item) {
    return `<fieldset class="supp-block" data-supp-key="${item.key}">
      <legend>${item.title}</legend>
      <p class="hint">${item.note}</p>
      <p class="hint">目前既有 Statcast 分析器不讀取此資料；資料表與原始快照獨立保存，未來再由多資料源分析架構顯式整合。</p>
      <div class="supp-controls">
        <label>指定投手 IDs（選填） Pitcher IDs<input class="supp-pitchers" type="text" placeholder="留空 = 本機 Statcast 已知全部投手；例 453286,660271"></label>
        <label class="checkbox-line"><input class="supp-resume" type="checkbox" checked> 歷史抓取略過已成功投手 Resume</label>
      </div>
      <div class="button-row supp-buttons">
        <button type="button" data-action="backfill">歷史抓取 Backfill</button>
        <button type="button" data-action="update">更新 Update</button>
        <button type="button" data-action="retry_failed">重試失敗 Retry Failed</button>
        <button type="button" data-action="verify">驗證 Verify</button>
        <button type="button" data-action="rebuild">從原始快照重建 Rebuild</button>
      </div>
      <div class="supp-progress-track"><div class="supp-progress-fill"></div></div>
      <div class="supp-progress-summary">尚無執行中的工作 No active sync</div>
      <div class="supp-progress-grid">
        <span class="supp-current">目前投手 Current: —</span>
        <span class="supp-rows">本次資料列 Rows: 0</span>
        <span class="supp-failed">失敗投手 Failed: 0</span>
        <span class="supp-skipped">略過投手 Skipped: 0</span>
        <span class="supp-elapsed">已耗時 Elapsed: —</span>
        <span class="supp-eta">預估剩餘 ETA: —</span>
      </div>
      <pre class="supp-result" hidden></pre>
    </fieldset>`;
  }

  function install() {
    if (document.querySelector("#supplemental-data-section")) return true;
    const backfill = document.querySelector("#run-backfill");
    const host = backfill?.closest(".panel-body") || backfill?.closest(".panel") || document.querySelector("#data-panel .panel-body");
    if (!host) return false;

    const style = document.createElement("style");
    style.id = "supplemental-data-style";
    style.textContent = `
      #supplemental-data-section { margin-top:14px; border-top:2px solid #7d8da0; padding-top:10px; }
      #supplemental-data-section > h3 { margin:0 0 8px; color:#174f8b; }
      .supp-block { margin:10px 0; border:1px solid #8998a8; background:#f7f9fb; }
      .supp-controls { display:grid; grid-template-columns:minmax(300px,1fr) minmax(260px,auto); gap:8px 12px; align-items:end; }
      .supp-controls label:not(.checkbox-line) { display:flex; flex-direction:column; gap:3px; }
      .supp-buttons { display:flex; flex-wrap:wrap; gap:6px; margin:8px 0; }
      .supp-progress-track { height:18px; border:1px solid #6e7f8e; background:#fff; box-shadow:inset 1px 1px 2px #b8c2cc; overflow:hidden; }
      .supp-progress-fill { height:100%; width:0%; background:linear-gradient(#86d75d,#39a52d 52%,#75cf54); transition:width .25s linear; }
      .supp-progress-summary { margin-top:5px; font-weight:700; }
      .supp-progress-grid { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:3px 14px; margin-top:5px; font-size:12px; }
      .supp-result { white-space:pre-wrap; max-height:220px; overflow:auto; background:#fff; border:1px solid #b7c0ca; padding:6px; }
      @media(max-width:900px){.supp-controls,.supp-progress-grid{grid-template-columns:1fr;}}
    `;
    document.head.append(style);

    const section = document.createElement("section");
    section.id = "supplemental-data-section";
    section.innerHTML = `<h3>補充資料來源 Supplemental Data Sources</h3>
      <p class="hint">三個入口彼此獨立管理。Backfill 用於首次完整抓取並可續傳；Update 會重新抓來源的完整投手資料以接收歷史修訂。</p>
      ${SOURCES.map(blockHtml).join("")}`;
    host.append(section);

    SOURCES.forEach(item => wire(item));
    refreshStatus();
    return true;
  }

  function payloadFor(item, block) {
    const ids = block.querySelector(".supp-pitchers").value.trim();
    return {
      source:item.source, dataset:item.dataset,
      pitcher_ids:ids || undefined,
      resume:block.querySelector(".supp-resume").checked,
    };
  }

  function setResult(block, value, isError = false) {
    const out = block.querySelector(".supp-result");
    out.hidden = false;
    out.textContent = isError ? `ERROR: ${value}` : JSON.stringify(value, null, 2);
  }

  function wire(item) {
    const block = document.querySelector(`[data-supp-key="${item.key}"]`);
    if (!block) return;
    block.querySelectorAll("button[data-action]").forEach(button => button.addEventListener("click", async () => {
      const action = button.dataset.action;
      const payload = payloadFor(item, block);
      try {
        if (action === "verify") {
          setResult(block, await post("supplemental-verify", payload));
          return;
        }
        if (action === "rebuild") {
          if (!confirm(`確定要從最新原始快照重建 ${item.title}？`)) return;
          payload.confirmation = "REBUILD";
          setResult(block, await post("supplemental-rebuild", payload));
          await refreshStatus();
          return;
        }
        payload.mode = action;
        button.disabled = true;
        setResult(block, { status:"started", source:item.source, dataset:item.dataset, mode:action });
        const result = await post("supplemental-run", payload);
        setResult(block, result);
        await refreshStatus();
      } catch (error) {
        setResult(block, error?.message || String(error), true);
      } finally {
        button.disabled = false;
      }
    }));
  }

  function renderProgress(item, progress) {
    if (!progress) return;
    const block = document.querySelector(`[data-supp-key="${item.key}"]`);
    if (!block) return;
    const percent = Math.max(0, Math.min(100, Number(progress.percent || 0)));
    block.querySelector(".supp-progress-fill").style.width = `${percent}%`;
    const label = {running:"進行中 Running",success:"完成 Completed",partial:"完成但有失敗 Completed with Failures",failed:"失敗 Failed"}[progress.status] || progress.status;
    block.querySelector(".supp-progress-summary").textContent = `${label} — ${num(progress.completed_units)} / ${num(progress.total_units)} 投手 (${percent.toFixed(1)}%)`;
    block.querySelector(".supp-current").textContent = `目前投手 Current: ${progress.current_unit || "—"}`;
    block.querySelector(".supp-rows").textContent = `本次資料列 Rows: ${num(progress.rows_received)}`;
    block.querySelector(".supp-failed").textContent = `失敗投手 Failed: ${num(progress.failed_units)}`;
    block.querySelector(".supp-skipped").textContent = `略過投手 Skipped: ${num(progress.skipped_units)}`;
    block.querySelector(".supp-elapsed").textContent = `已耗時 Elapsed: ${fmtDuration(progress.elapsed_seconds)}`;
    block.querySelector(".supp-eta").textContent = `預估剩餘 ETA: ${fmtDuration(progress.eta_seconds)}`;
  }

  async function poll() {
    for (const item of SOURCES) {
      try {
        const body = await post("supplemental-progress", {source:item.source,dataset:item.dataset});
        renderProgress(item, body.progress);
      } catch (_) {}
    }
  }

  async function refreshStatus() {
    try {
      const status = await post("supplemental-status", {source:"spin_aggregate",dataset:"mlb"});
      let badge = document.querySelector("#supplemental-data-status");
      if (!badge) {
        badge = document.createElement("div"); badge.id = "supplemental-data-status"; badge.className = "hint";
        document.querySelector("#supplemental-data-section")?.insertAdjacentElement("afterbegin", badge);
      }
      badge.textContent = `目前保存：Pitch3D MLB ${num(status.pitch3d_mlb_rows)} 列 / ${num(status.pitch3d_mlb_pitchers)} 投手；MiLB ${num(status.pitch3d_milb_rows)} 列 / ${num(status.pitch3d_milb_pitchers)} 投手；旋轉／縫線聚合 ${num(status.spin_aggregate_rows)} 列 / ${num(status.spin_aggregate_pitchers)} 投手；原始快照 ${num(status.raw_snapshots)}。`;
    } catch (_) {}
  }

  if (!install()) {
    const observer = new MutationObserver(() => { if (install()) observer.disconnect(); });
    observer.observe(document.documentElement, {childList:true,subtree:true});
    setTimeout(() => observer.disconnect(), 15000);
  }
  setTimeout(poll, 250);
  setInterval(poll, 1000);
})();
