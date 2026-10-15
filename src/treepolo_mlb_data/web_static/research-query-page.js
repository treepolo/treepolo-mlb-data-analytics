(() => {
  "use strict";

  const PANEL_ID = "research-query-panel";
  const ROUTE = "research-query";
  const PITCH_TYPES = ["FF", "SI", "FC", "SL", "ST", "CH", "CU", "FS", "KC"];
  const FLAG_LABEL = { sufficient: "資料充足", extrapolated: "外推", unsupported: "不支持" };
  const GATE_LABEL = { full: "完整個別化", level_only: "只有水準個別化", off: "關閉", unknown: "尚未判定" };
  const state = { runs: [], runId: null, meta: null, pitcher: null, batter: null, result: null, svg: null };

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

  async function api(path) {
    const response = await fetch(path);
    const body = await response.json();
    if (!response.ok) throw new Error(body.error || `HTTP ${response.status}`);
    return body;
  }

  const num = (v, digits = 4) => (v === null || v === undefined ? "—" : Number(v).toFixed(digits));
  const pct = v => (v === null || v === undefined ? "—" : `${(Number(v) * 100).toFixed(2)}%`);
  const status = text => { const box = document.getElementById("rq-status"); if (box) box.textContent = text || ""; };
  async function guarded(fn) {
    try { status("處理中 Working…"); await fn(); status(""); } catch (error) { status(`錯誤 Error: ${error.message}`); }
  }

  function injectStyles() {
    if (document.getElementById("research-query-styles")) return;
    const style = el("style", { id: "research-query-styles" });
    style.textContent = `
      .rq-form { display:grid; grid-template-columns:repeat(auto-fill,minmax(150px,1fr)); gap:8px; margin:8px 0; }
      .rq-form label { display:flex; flex-direction:column; font-size:12px; gap:2px; }
      .rq-bases label { flex-direction:row; align-items:center; gap:4px; }
      .rq-players { display:grid; grid-template-columns:repeat(auto-fit,minmax(260px,1fr)); gap:10px; margin:8px 0; }
      .rq-player { border:1px solid #adb8c7; padding:6px; background:#f7f8fa; }
      .rq-player select { width:100%; }
      .rq-banner { margin:6px 0; padding:6px; border:1px solid #adb8c7; background:#eef3f9; font-size:13px; }
      .rq-notice { margin:4px 0; padding:6px; border:1px solid #c9a227; background:#fff8e1; font-size:13px; }
      .rq-table { border-collapse:collapse; width:100%; font-size:12px; margin-top:6px; }
      .rq-table th, .rq-table td { border:1px solid #bbc3ce; padding:3px 6px; text-align:right; }
      .rq-table th:first-child, .rq-table td:first-child, .rq-table td.rq-flag { text-align:left; }
      .rq-row-extrapolated td { border-style:dashed; background:#fffbe6; }
      .rq-row-unsupported td { color:#8a94a1; background:#f1f3f5; text-decoration:line-through; }
      .rq-detected { font-weight:700; color:#0b6b2e; }
      .rq-notcovered { margin-top:8px; font-size:12px; color:#44505e; }
      .rq-chart { margin-top:8px; overflow:auto; }
    `;
    document.head.append(style);
  }

  // ---- the form -------------------------------------------------------------------------------------------------
  function select(id, options, value) {
    const node = el("select", { id }, ...options.map(([v, label]) => el("option", { value: v, text: label })));
    if (value !== undefined) node.value = String(value);
    return node;
  }
  const val = id => document.getElementById(id).value;

  function playerBox(side, title) {
    const input = el("input", { id: `rq-${side}-text`, type: "text", placeholder: "名稱或 ID Name or ID" });
    const list = el("select", { id: `rq-${side}-list`, size: "5" });
    const chosen = el("div", { id: `rq-${side}-chosen`, class: "hint", text: "未指定（聯盟平均） Not specified (league average)" });
    const search = () => guarded(async () => {
      const group = val("rq-group");
      const body = await api(`/api/research/pitch-query/players?run=${state.runId}&group=${encodeURIComponent(group)}&side=${side}&q=${encodeURIComponent(input.value)}&limit=30`);
      list.replaceChildren(...body.players.map(p => el("option", { value: p.id, text: `${p.name || "（無名稱）"} · ${p.id} · ${p.pitches} 球` })));
    });
    list.addEventListener("change", () => {
      const opt = list.options[list.selectedIndex];
      if (opt) { state[side] = { id: opt.value, label: opt.textContent }; chosen.textContent = `已指定 Selected: ${opt.textContent}`; }
    });
    const clear = el("button", { type: "button", text: "清除 Clear", onclick: () => { state[side] = null; chosen.textContent = "未指定（聯盟平均） Not specified (league average)"; } });
    return el("div", { class: "rq-player" }, el("div", { text: title }), el("div", {}, input, el("button", { type: "button", text: "搜尋 Search", onclick: search }), clear), list, chosen);
  }

  function renderForm() {
    const host = document.getElementById("rq-form-host");
    if (!state.meta) { host.replaceChildren(el("p", { class: "hint", text: "請先選擇一個 pitch_query 研究紀錄。 Choose a pitch_query run first." })); return; }
    const meta = state.meta;
    const groupSel = select("rq-group", meta.groups.map(g => [g, g]), meta.groups[0]);
    const form = el("div", { class: "rq-form" },
      el("label", {}, "左右手組 Group", groupSel),
      el("label", {}, "壞球 Balls", select("rq-balls", [0, 1, 2, 3].map(v => [v, v]), 0)),
      el("label", {}, "好球 Strikes", select("rq-strikes", [0, 1, 2].map(v => [v, v]), 0)),
      el("label", {}, "出局 Outs", select("rq-outs", [0, 1, 2].map(v => [v, v]), 0)),
      el("div", { class: "rq-bases" }, el("div", { text: "壘包 Bases" }),
        ...[["rq-b1", "一壘 1B"], ["rq-b2", "二壘 2B"], ["rq-b3", "三壘 3B"]].map(([id, label]) => el("label", {}, el("input", { id, type: "checkbox" }), label))),
      el("label", {}, "前一球球種 Previous pitch", select("rq-prev", [["none", "無（第一球）none"], ...PITCH_TYPES.map(t => [t, t])], "none")),
      el("label", {}, "連投顆數 Streak", select("rq-streak", [[1, "1"], [2, "2"], [3, "3 以上 or more"]], 1)),
      el("button", { type: "button", id: "rq-go", text: "查詢 Query", onclick: () => guarded(ask) }));
    const players = el("div", { class: "rq-players" });
    if (meta.players_available) players.append(playerBox("pitcher", "投手（可選）Pitcher"), playerBox("batter", "打者（可選）Batter"));
    else players.append(el("p", { class: "hint", text: "這個查詢紀錄沒有模型包，只能做聯盟平均查詢（M0）。 No model package: league-average (M0) queries only." }));
    host.replaceChildren(form, players);
    document.getElementById("rq-balls").addEventListener("change", syncPrev);
    document.getElementById("rq-strikes").addEventListener("change", syncPrev);
    syncPrev();
  }

  function syncPrev() {
    const first = val("rq-balls") === "0" && val("rq-strikes") === "0";
    const prev = document.getElementById("rq-prev");
    if (first) prev.value = "none"; else if (prev.value === "none") prev.value = "FF";
    prev.disabled = first;
  }

  // ---- asking and showing -----------------------------------------------------------------------------------------
  function basesText() { return ["rq-b1", "rq-b2", "rq-b3"].map(id => (document.getElementById(id).checked ? "1" : "0")).join(""); }

  async function ask() {
    if (!state.runId) throw new Error("請先選擇研究紀錄 Choose a run first");
    const query = new URLSearchParams({ run: state.runId, group: val("rq-group"), balls: val("rq-balls"), strikes: val("rq-strikes"), outs: val("rq-outs"), bases: basesText(),
      prev: val("rq-prev"), streak: val("rq-streak") });
    if (state.pitcher) query.set("pitcher", state.pitcher.id);
    if (state.batter) query.set("batter", state.batter.id);
    state.result = await api(`/api/research/pitch-query?${query.toString()}`);
    renderResult();
  }

  function renderResult() {
    const host = document.getElementById("rq-result");
    const r = state.result;
    if (!r) { host.replaceChildren(); return; }
    const parts = [];
    const modeText = { M0: "M0 聯盟平均", M1: "M1 特定投手 × 平均打者", M2: "M2 平均投手 × 特定打者", M3: "M3 特定投手 × 特定打者" }[r.mode] || r.mode;
    const gates = r.gates && Object.keys(r.gates).length ? `　閘門：投手側 ${GATE_LABEL[r.gates.pitcher] || r.gates.pitcher}、打者側 ${GATE_LABEL[r.gates.batter] || r.gates.batter}` : "";
    parts.push(el("div", { class: "rq-banner", id: "rq-banner", text: `${modeText}　分組 ${r.group}${r.level ? `　特徵階 ${r.level}` : ""}${gates}` }));
    Object.entries(r.players || {}).forEach(([side, info]) => {
      const shrink = info.shrinkage == null ? "" : `；個人資料權重 own-data weight ${(info.shrinkage * 100).toFixed(0)}%（愈低愈接近聯盟平均）`;
      const thin = info.pitches < 300 ? "　⚠ 資料量少，描述幾乎等於聯盟平均" : "";
      parts.push(el("div", { class: "rq-banner", text: `${side === "pitcher" ? "投手" : "打者"} ${info.name || info.id}：${info.pitches} 球${shrink}${thin}` }));
    });
    (r.notice || []).forEach(text => parts.push(el("div", { class: "rq-notice", text })));
    if (r.sampling) parts.push(el("div", { class: "hint", text: `抽樣 ${r.sampling.pairs} 對、成員 ${r.sampling.members}；${r.sampling.note}` }));
    parts.push(table(r));
    const charts = window.TreepoloResearchCharts;
    if (charts) {
      const rows = r.rows.map(x => ({ action: x.action, q: x.supported ? x.q : null, q_lo: x.q_lo, q_hi: x.q_hi, indistinct_group: x.indistinct_group || "—", unsupported: x.flag === "extrapolated" ? 1 : 0 }));
      const preset = charts.PRESETS.situationBars;
      const tree = charts.build(preset.type, rows, preset.guess(Object.keys(rows[0])), { ...preset.options, title: "各球種的期望失分 Q（越低越好）", labelOrder: PITCH_TYPES });
      state.svg = charts.toSvgString(tree);
      parts.push(el("div", { class: "rq-chart", id: "rq-chart" }, charts.toDom(tree, document)));
    }
    parts.push(el("div", { class: "rq-notcovered", id: "rq-notcovered", text: `不涵蓋 Not covered：${(r.not_covered || []).join("；")}` }));
    host.replaceChildren(...parts);
  }

  function table(r) {
    const head = ["球種", "旗標", "Q 期望失分 [99%]", "相對一般配球", "多投 10pp 的變化", "全壘打/球 [99%]", "n", "群組", "偵測到", "證據"];
    const body = r.rows.map(x => el("tr", { class: `rq-row-${x.flag}`, "data-action": x.action },
      el("td", { text: x.action }), el("td", { class: "rq-flag", text: FLAG_LABEL[x.flag] || x.flag }),
      el("td", { text: x.supported ? `${num(x.q)} [${num(x.q_lo)}, ${num(x.q_hi)}]` : "—" }),
      el("td", { text: x.supported ? `${num(x.advantage)} [${num(x.adv_lo)}, ${num(x.adv_hi)}]` : "—" }),
      el("td", { text: x.supported ? `${num(x.marginal)} [${num(x.m_lo)}, ${num(x.m_hi)}]` : "—" }),
      el("td", { text: `${pct(x.hr_probability)} [${pct(x.hr_lo)}, ${pct(x.hr_hi)}]` }),
      el("td", { text: x.n }), el("td", { text: x.supported ? (x.indistinct_group || "—") : "—" }),
      el("td", { class: x.detected ? "rq-detected" : "", text: x.supported ? (x.detected ? "是" : "否") : "—" }),
      el("td", { class: "rq-flag", text: x.supported ? x.evidence : "不支持，不得建議" })));
    return el("table", { class: "rq-table", id: "rq-table" }, el("thead", {}, el("tr", {}, ...head.map(h => el("th", { text: h })))), el("tbody", {}, ...body));
  }

  // ---- runs ---------------------------------------------------------------------------------------------------------
  async function loadRuns() {
    const body = await api("/api/research/runs?kind=pitch_query&status=success&limit=50");
    state.runs = body.runs || [];
    const sel = document.getElementById("rq-run");
    sel.replaceChildren(el("option", { value: "", text: "（選擇）" }), ...state.runs.map(run => el("option", { value: run.id, text: `#${run.id} · ${run.created_at}` })));
    if (state.runId) sel.value = String(state.runId);
    else if (state.runs.length) { sel.value = String(state.runs[0].id); await chooseRun(state.runs[0].id); }
  }

  async function chooseRun(id) {
    state.runId = id ? Number(id) : null; state.pitcher = state.batter = state.result = null;
    state.meta = state.runId ? await api(`/api/research/pitch-query/meta?run=${state.runId}`) : null;
    renderForm(); renderResult();
  }

  function inject() {
    if (document.getElementById(PANEL_ID)) return;
    injectStyles();
    const nav = document.querySelector(".navigation-pane");
    const main = document.querySelector(".main-pane");
    if (!nav || !main) return;
    const research = [...nav.querySelectorAll(".task-group")].find(g => (g.textContent || "").includes("研究 Research"));
    const button = el("button", { class: "nav-item", "data-panel": PANEL_ID, text: "情境查詢 Situation Query" });
    if (research) research.append(button); else nav.append(el("div", { class: "task-group" }, el("div", { class: "task-group-title", text: "研究 Research" }), button));
    button.addEventListener("click", () => window.treepoloPanels?.activate?.(PANEL_ID, { updateUrl: true, source: "nav" }));
    const panel = el("div", { id: PANEL_ID, class: "panel" },
      el("div", { class: "panel-heading", text: "情境查詢 Situation Query" }),
      el("div", { class: "panel-body" },
        el("p", { class: "hint", text: "輸入一個比賽情境，看九種球種各自的期望失分。結果是模型估計（建模期資料），不是保證；沒有達到偵測準則的差異會標示「否」。 Model estimates from the modeling period, not guarantees." }),
        el("div", { class: "button-row" }, el("label", {}, "查詢紀錄 Run ", el("select", { id: "rq-run", onchange: event => guarded(() => chooseRun(event.target.value)) })),
          el("button", { type: "button", text: "⟳ 重新整理 Refresh", onclick: () => guarded(loadRuns) })),
        el("div", { id: "rq-status", class: "rr-status" }), el("div", { id: "rq-form-host" }), el("div", { id: "rq-result" })));
    main.insertBefore(panel, document.querySelector("#result-window"));
    window.treepoloPanels?.register?.(PANEL_ID, ROUTE);
    document.addEventListener("treepolo:panel-activated", event => { if (event.detail?.panelId === PANEL_ID) guarded(loadRuns); });
    renderForm();
    if (new URL(window.location.href).searchParams.get("page") === ROUTE) window.treepoloPanels?.activate?.(PANEL_ID, { updateUrl: false, source: "initial-route" });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", () => setTimeout(inject, 0), { once: true });
  else setTimeout(inject, 0);
})();
