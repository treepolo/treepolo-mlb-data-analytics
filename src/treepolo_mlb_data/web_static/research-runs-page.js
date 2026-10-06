(() => {
  "use strict";

  const PANEL_ID = "research-runs-panel";
  const ROUTE = "research-runs";
  const state = { studies: [], runs: [], studyId: null, runId: null, detail: null, section: 0, offset: 0, page: null };
  const PAGE_SIZE = 200;

  function el(tag, props = {}, ...children) {
    const node = document.createElement(tag);
    Object.entries(props).forEach(([key, value]) => {
      if (key === "class") node.className = value;
      else if (key === "text") node.textContent = value;
      else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
      else node.setAttribute(key, value);
    });
    children.flat().forEach(child => { if (child != null) node.append(child.nodeType ? child : document.createTextNode(String(child))); });
    return node;
  }

  async function api(path, options = {}) {
    const response = await fetch(path, options);
    const type = response.headers.get("Content-Type") || "";
    if (!type.includes("application/json")) {
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return { response, blob: await response.blob() };
    }
    const body = await response.json();
    if (!response.ok) throw new Error(body.error || `HTTP ${response.status}`);
    return body;
  }
  const post = (path, body) => api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });

  function injectStyles() {
    if (document.getElementById("research-runs-styles")) return;
    const style = el("style", { id: "research-runs-styles" });
    style.textContent = `
      .rr-layout { display:grid; grid-template-columns:230px 1fr; gap:10px; margin-top:8px; }
      .rr-studies { border:1px solid #adb8c7; background:#f7f8fa; padding:6px; max-height:520px; overflow:auto; }
      .rr-study { padding:4px 6px; cursor:pointer; border-bottom:1px dotted #bbc3ce; }
      .rr-study.selected { background:#cfe3fa; font-weight:700; }
      .rr-study small { display:block; color:#505b68; }
      .rr-main table { border-collapse:collapse; width:100%; font-size:12px; }
      .rr-main th, .rr-main td { border:1px solid #c5ccd6; padding:2px 5px; text-align:left; }
      .rr-main tr.selected td { background:#cfe3fa; }
      .rr-main tbody tr { cursor:pointer; }
      .rr-badge { font-size:11px; padding:0 4px; border:1px solid #8098b5; background:#eef5ff; margin-left:4px; }
      .rr-badge.bad { border-color:#b06060; background:#ffeeee; }
      .rr-detail { margin-top:10px; border:1px solid #adb8c7; padding:8px; background:#fff; }
      .rr-detail pre { max-height:220px; overflow:auto; background:#f4f6f9; padding:6px; margin:4px 0; }
      .rr-detail textarea { width:100%; min-height:70px; box-sizing:border-box; }
      .rr-result { overflow:auto; max-height:360px; }
      .rr-status { margin-top:6px; color:#505b68; font-size:12px; min-height:16px; }
      @media (max-width:900px) { .rr-layout { grid-template-columns:1fr; } }
    `;
    document.head.append(style);
  }

  function setStatus(text, bad = false) {
    const node = document.getElementById("rr-status");
    if (node) { node.textContent = text || ""; node.style.color = bad ? "#a02020" : ""; }
  }
  async function guarded(task) {
    try { await task(); } catch (error) { setStatus(`錯誤 Error: ${error.message}`, true); }
  }

  function scopeText(scope) {
    if (!scope || !Object.keys(scope).length) return "—";
    const range = scope.game_years ? scope.game_years.join(", ") : `${scope.date_from} → ${scope.date_to}`;
    return `${range} · ${(scope.game_types || []).join("/")}`;
  }

  async function loadStudies() {
    state.studies = (await api("/api/research/studies")).studies;
    renderStudies();
  }
  async function loadRuns() {
    const query = state.studyId ? `?study_id=${state.studyId}&limit=500` : "?limit=500";
    state.runs = (await api(`/api/research/runs${query}`)).runs;
    renderRuns();
  }
  async function refresh() { await loadStudies(); await loadRuns(); if (state.runId) await showRun(state.runId, false); }

  function renderStudies() {
    const host = document.getElementById("rr-studies");
    if (!host) return;
    host.replaceChildren(
      el("div", { class: `rr-study${state.studyId === null ? " selected" : ""}`, onclick: () => selectStudy(null) }, "全部執行紀錄 All runs"),
      ...state.studies.map(study => el("div", { class: `rr-study${state.studyId === study.id ? " selected" : ""}`, onclick: () => selectStudy(study.id) },
        study.name, el("small", { text: `${study.run_count} 次執行 runs${study.origin === "imported" ? " · 匯入 Imported" : ""}` }))),
    );
  }
  function selectStudy(id) { state.studyId = id; state.runId = null; state.detail = null; renderDetail(); renderStudies(); guarded(loadRuns); }

  function renderRuns() {
    const host = document.getElementById("rr-runs");
    if (!host) return;
    const rows = state.runs.map(run => el("tr", { class: state.runId === run.id ? "selected" : "", onclick: () => guarded(() => showRun(run.id, true)) },
      el("td", { text: run.id }), el("td", { text: run.kind }),
      el("td", { text: run.status }, run.origin === "imported" ? el("span", { class: "rr-badge", text: "匯入 Imported" }) : null),
      el("td", { text: scopeText(run.scope) }), el("td", { text: (run.created_at || "").slice(0, 19).replace("T", " ") }),
      el("td", { text: run.summary && run.summary.total_rows != null ? run.summary.total_rows : "—" })));
    host.replaceChildren(el("table", {},
      el("thead", {}, el("tr", {}, ...["#", "方法 Method", "狀態 Status", "範圍 Scope", "時間 Created", "列數 Rows"].map(t => el("th", { text: t })))),
      el("tbody", {}, rows.length ? rows : el("tr", {}, el("td", { colspan: "6", text: "沒有研究紀錄 No runs" })))));
  }

  async function showRun(id, resetPage) {
    state.runId = id;
    state.detail = (await api(`/api/research/runs/${id}`)).item;
    if (resetPage) { state.section = 0; state.offset = 0; }
    state.page = state.detail.status === "success"
      ? await api(`/api/research/runs/${id}/result?section=${state.section}&offset=${state.offset}&limit=${PAGE_SIZE}`) : null;
    renderRuns(); renderDetail();
  }

  function badge(detail) {
    if (detail.local_data_match === true) return el("span", { class: "rr-badge", text: "資料相符 Matches local data" });
    if (detail.local_data_match === false) return el("span", { class: "rr-badge bad", text: "資料不同 Differs from local data" });
    return null;
  }

  function renderDetail() {
    const host = document.getElementById("rr-detail");
    if (!host) return;
    const detail = state.detail;
    if (!detail) { host.replaceChildren(el("p", { class: "hint", text: "選擇一筆研究紀錄以檢視 Select a run to inspect." })); return; }
    const study = state.studies.find(item => item.id === state.studyId);
    const parts = [
      el("div", {}, el("b", { text: `#${detail.id} ${detail.kind}` }), " ", badge(detail),
        detail.origin === "imported" ? el("span", { class: "rr-badge", text: "匯入 Imported" }) : null),
      el("div", { class: "hint", text: `狀態 ${detail.status} · 程式版本 ${detail.code_version} · 耗時 ${detail.duration_seconds == null ? "—" : detail.duration_seconds.toFixed(2) + " s"}` }),
      detail.error ? el("div", { class: "rr-badge bad", text: detail.error }) : null,
      el("div", { text: "設定 Settings" }), el("pre", { text: JSON.stringify(detail.config, null, 2) }),
      el("div", { text: "資料指紋 Data fingerprint" }), el("pre", { text: JSON.stringify(detail.data_fingerprint, null, 2) }),
      el("div", { class: "button-row" },
        el("button", { type: "button", text: "匯出此紀錄 Export Run", onclick: () => guarded(() => exportBundle({ run_ids: [detail.id] })) }),
        el("button", { type: "button", text: "以本機資料重跑 Re-run Locally", onclick: () => guarded(async () => {
          setStatus("執行中 Running…"); const out = await post(`/api/research/runs/${detail.id}/rerun`, {});
          setStatus(out.reused ? "已有相同紀錄 Reused existing run" : "完成 Finished"); await refresh(); await showRun(out.run.id, true);
        }) }),
        el("button", { type: "button", text: "刪除 Delete", onclick: () => guarded(async () => {
          if (!window.confirm(`刪除研究紀錄 #${detail.id}？ Delete run #${detail.id}?`)) return;
          await api(`/api/research/runs/${detail.id}`, { method: "DELETE" }); state.runId = null; state.detail = null; await refresh(); renderDetail();
        }) })),
    ];
    if (study) {
      const purpose = el("textarea", { id: "rr-purpose" }); purpose.value = study.purpose || "";
      const insight = el("textarea", { id: "rr-insight" }); insight.value = study.insight_markdown || "";
      parts.push(el("div", { text: `研究專案：${study.name}  目的 Purpose` }), purpose, el("div", { text: "見解 Insights (Markdown)" }), insight,
        el("div", { class: "button-row" }, el("button", { type: "button", text: "儲存目的與見解 Save", onclick: () => guarded(async () => {
          await post(`/api/research/studies/${study.id}`, { purpose: purpose.value, insight_markdown: insight.value }); setStatus("已儲存 Saved"); await loadStudies();
        }) })));
    }
    parts.push(...renderResult());
    host.replaceChildren(...parts.filter(Boolean));
  }

  function renderResult() {
    const page = state.page;
    if (!page) return [];
    const section = page.section;
    const columns = section.columns || [];
    const table = el("table", {}, el("thead", {}, el("tr", {}, ...columns.map(c => el("th", { text: c })))),
      el("tbody", {}, ...page.rows.map(row => el("tr", {}, ...columns.map(c => el("td", { text: row[c] == null ? "" : String(row[c]) }))))));
    const last = Math.min(page.offset + page.limit, page.total);
    const nav = el("div", { class: "button-row" },
      el("button", { type: "button", text: "◀ 上一頁 Prev", ...(page.offset === 0 ? { disabled: "" } : {}), onclick: () => guarded(() => { state.offset = Math.max(0, state.offset - PAGE_SIZE); return showRun(state.runId, false); }) }),
      el("span", { text: ` ${page.total ? page.offset + 1 : 0}–${last} / ${page.total} 列 rows `, class: "hint" }),
      el("button", { type: "button", text: "下一頁 Next ▶", ...(last >= page.total ? { disabled: "" } : {}), onclick: () => guarded(() => { state.offset += PAGE_SIZE; return showRun(state.runId, false); }) }));
    const tabs = el("div", { class: "button-row" }, ...Array.from({ length: page.section_count }, (_, index) =>
      el("button", { type: "button", text: `結果 ${index + 1}`, ...(index === state.section ? { class: "primary" } : {}),
        onclick: () => guarded(() => { state.section = index; state.offset = 0; return showRun(state.runId, false); }) })));
    return [el("div", { text: `結果 Result：${section.title || ""}` }), page.section_count > 1 ? tabs : null, el("div", { class: "rr-result" }, table), nav];
  }

  async function exportBundle(selection) {
    setStatus("匯出中 Exporting…");
    const response = await fetch("/api/research/export", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(selection) });
    if (!response.ok) { const body = await response.json(); throw new Error(body.error || `HTTP ${response.status}`); }
    const blob = await response.blob();
    const match = /filename="([^"]+)"/.exec(response.headers.get("Content-Disposition") || "");
    const link = el("a", { href: URL.createObjectURL(blob), download: match ? match[1] : "research.treepolo-research.zip" });
    document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(link.href), 5000);
    setStatus("已匯出 Exported");
  }

  async function importFile(file) {
    setStatus("匯入中 Importing…");
    const response = await fetch("/api/research/import", { method: "POST", headers: { "Content-Type": "application/zip" }, body: await file.arrayBuffer() });
    const body = await response.json();
    if (!response.ok) throw new Error(body.error || `HTTP ${response.status}`);
    setStatus(`匯入完成 Imported：新增研究專案 ${body.studies_added}、新增紀錄 ${body.runs_added}、略過重複 ${body.runs_skipped_duplicate}${body.warnings.length ? "；警告 " + body.warnings.join("; ") : ""}`);
    await refresh();
  }

  function inject() {
    if (document.getElementById(PANEL_ID)) return;
    injectStyles();
    const nav = document.querySelector(".navigation-pane");
    const main = document.querySelector(".main-pane");
    if (!nav || !main) return;
    const group = el("div", { class: "task-group" }, el("div", { class: "task-group-title", text: "研究 Research" }),
      el("button", { class: "nav-item", "data-panel": PANEL_ID, text: "研究紀錄 Research Runs" }));
    nav.append(group);
    group.querySelector("button").addEventListener("click", () => window.treepoloPanels?.activate?.(PANEL_ID, { updateUrl: true, source: "nav" }));
    const fileInput = el("input", { id: "rr-import", type: "file", accept: ".zip", style: "display:none", onchange: event => {
      const file = event.target.files[0]; event.target.value = ""; if (file) guarded(() => importFile(file));
    } });
    const panel = el("div", { id: PANEL_ID, class: "panel" },
      el("div", { class: "panel-heading", text: "研究紀錄 Research Runs" }),
      el("div", { class: "panel-body" },
        el("p", { class: "hint", text: "每次研究執行都會自動記錄設定、結果與資料範圍；相同設定且資料未變時直接讀取，不重算。 Every research run is recorded automatically and reused when settings and data are unchanged." }),
        el("div", { class: "button-row" },
          el("button", { type: "button", text: "⟳ 重新整理 Refresh", onclick: () => guarded(refresh) }),
          el("button", { type: "button", text: "＋ 新增研究專案 New Study", onclick: () => guarded(async () => {
            const name = window.prompt("研究專案名稱 Study name"); if (!name) return;
            await post("/api/research/studies", { name }); await loadStudies();
          }) }),
          el("button", { type: "button", text: "匯出研究專案 Export Study", onclick: () => guarded(() => {
            if (!state.studyId) throw new Error("請先選擇研究專案 Select a study first"); return exportBundle({ study_ids: [state.studyId] });
          }) }),
          el("button", { type: "button", text: "匯入研究檔 Import Bundle", onclick: () => fileInput.click() }), fileInput),
        el("div", { id: "rr-status", class: "rr-status" }),
        el("div", { class: "rr-layout" }, el("div", { id: "rr-studies", class: "rr-studies" }),
          el("div", { class: "rr-main" }, el("div", { id: "rr-runs" }), el("div", { id: "rr-detail", class: "rr-detail" })))));
    main.insertBefore(panel, document.querySelector("#result-window"));
    window.treepoloPanels?.register?.(PANEL_ID, ROUTE);
    document.addEventListener("treepolo:panel-activated", event => { if (event.detail?.panelId === PANEL_ID) guarded(refresh); });
    renderStudies(); renderRuns(); renderDetail();
    if (new URL(window.location.href).searchParams.get("page") === ROUTE) {
      window.treepoloPanels?.activate?.(PANEL_ID, { updateUrl: false, source: "initial-route" });
    }
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", () => setTimeout(inject, 0), { once: true });
  else setTimeout(inject, 0);
})();
