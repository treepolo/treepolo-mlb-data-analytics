(() => {
  "use strict";

  const PANEL_ID = "research-pipeline-panel";
  const ROUTE = "research-pipeline";
  const QUERY_PANEL_ID = "research-query-panel";
  const RUNS_PANEL_ID = "research-runs-panel";
  const STATUS = { pending: ["⏳", "等待"], running: ["▶", "執行中"], done: ["✔", "完成"], error: ["✖", "失敗"] };
  const state = { info: null, job: null, timer: null, tier: null, groups: null, formRunning: null };

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

  async function api(path, body) {
    const options = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
    const response = await fetch(path, options);
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
    return data;
  }

  const num = (v, d = 4) => (v === null || v === undefined || Number.isNaN(v) ? "—" : Number(v).toFixed(d));
  const minutes = s => (s == null ? "" : s < 90 ? `${s.toFixed(0)} 秒` : `${(s / 60).toFixed(1)} 分`);
  const say = text => { const box = document.getElementById("rp-message"); if (box) box.textContent = text || ""; };

  function injectStyles() {
    if (document.getElementById("research-pipeline-styles")) return;
    const style = el("style", { id: "research-pipeline-styles" });
    style.textContent = `
      .rp-form { display:flex; flex-wrap:wrap; gap:14px; margin:8px 0; align-items:flex-start; }
      .rp-form fieldset { border:1px solid #adb8c7; padding:6px 10px; }
      .rp-form label { display:block; font-size:13px; margin:2px 0; }
      .rp-steps { margin:10px 0; border:1px solid #adb8c7; }
      .rp-step { display:grid; grid-template-columns:28px 190px 70px 1fr; gap:8px; padding:6px 8px; border-bottom:1px solid #d5dbe3; align-items:center; font-size:13px; }
      .rp-step:last-child { border-bottom:none; }
      .rp-step.running { background:#eef5ff; } .rp-step.done { background:#f1faf3; } .rp-step.error { background:#fdeeee; }
      .rp-bar { height:6px; background:#dfe5ec; margin-top:3px; } .rp-bar > div { height:6px; background:#2f6fb2; }
      .rp-error { margin:8px 0; padding:8px; border:1px solid #c0392b; background:#fdeeee; font-size:13px; }
      .rp-card { border:1px solid #adb8c7; background:#f7f8fa; padding:8px; margin:8px 0; }
      .rp-card h4 { margin:0 0 6px 0; }
      .rp-table { border-collapse:collapse; font-size:12px; margin:4px 0; }
      .rp-table th, .rp-table td { border:1px solid #bbc3ce; padding:2px 8px; text-align:right; }
      .rp-table th:first-child, .rp-table td:first-child { text-align:left; }
      .rp-ok { color:#0b6b2e; font-weight:700; } .rp-bad { color:#b03a2e; font-weight:700; }
    `;
    document.head.append(style);
  }

  function selectedGroups() { return [...document.querySelectorAll("input[name=rp-group]:checked")].map(n => n.value); }

  async function start() {
    const tier = state.tier || document.querySelector("input[name=rp-tier]:checked")?.value;
    const groups = selectedGroups();
    if (!groups.length) throw new Error("請至少勾選一個手別組 Select at least one group");
    state.job = await api("/api/research/pipeline/start", { tier, groups });
    render(); poll();
  }

  async function refresh() {
    state.job = await api("/api/research/pipeline/status");
    render();
    return state.job;
  }

  function poll() {
    clearInterval(state.timer);
    state.timer = setInterval(async () => {
      try { const job = await refresh(); if (job.state !== "running") clearInterval(state.timer); } catch (error) { say(`錯誤 Error: ${error.message}`); clearInterval(state.timer); }
    }, 2000);
  }

  function renderForm() {
    const host = document.getElementById("rp-form");
    const info = state.info;
    const env = info.environment;
    const running = state.job && state.job.state === "running";
    const envText = env.xgboost ? (env.cuda ? "已偵測到 XGBoost 與 GPU，會用 GPU 訓練。" : "已偵測到 XGBoost，但沒有可用的 GPU，會用 CPU 訓練。") : "沒有安裝 XGBoost，會用 scikit-learn（CPU，較慢）。";
    host.replaceChildren(
      el("p", { class: "hint", id: "rp-env", text: envText }),
      el("div", { class: "rp-form" },
        el("fieldset", {}, el("legend", { text: "運算量 Size" }),
          ...info.tiers.map((t, i) => el("label", {}, el("input", { type: "radio", name: "rp-tier", value: t.id, ...((state.tier ? state.tier === t.id : i === 0) ? { checked: "checked" } : {}), ...(running ? { disabled: "disabled" } : {}),
          onchange: () => { state.tier = t.id; } }), ` ${t.label}`))),
        el("fieldset", {}, el("legend", { text: "手別組 Groups" }),
          ...info.groups.map(g => el("label", {}, el("input", { type: "checkbox", name: "rp-group", value: g, ...((!state.groups || state.groups.includes(g)) ? { checked: "checked" } : {}), ...(running ? { disabled: "disabled" } : {}),
          onchange: () => { state.groups = selectedGroups(); } }), ` ${g}`))),
        el("div", {},
          el("button", { type: "button", id: "rp-start", text: running ? "執行中…" : "▶ 開始 Start", ...(running ? { disabled: "disabled" } : {}), onclick: () => guard(start) }),
          el("button", { type: "button", id: "rp-cancel", text: "停止（目前步驟結束後）Stop", ...(running ? {} : { disabled: "disabled" }), onclick: () => guard(async () => { state.job = await api("/api/research/pipeline/cancel", {}); render(); }) }))));
  }

  function renderSteps() {
    const job = state.job;
    const host = document.getElementById("rp-steps");
    if (!job || !job.steps.length) { host.replaceChildren(el("p", { class: "hint", text: "按「開始」後，這裡會顯示每個步驟的進度。" })); return; }
    host.replaceChildren(el("div", { class: "rp-steps" }, ...job.steps.map((s, i) => {
      const [icon, label] = s.status === "done" && s.reused ? ["♻", "沿用"] : (STATUS[s.status] || ["", s.status]);
      const bar = s.status === "running" && s.fraction != null ? el("div", { class: "rp-bar" }, el("div", { style: `width:${Math.round(s.fraction * 100)}%` })) : null;
      return el("div", { class: `rp-step ${s.status}`, "data-step": s.id },
        el("div", { text: icon }), el("div", { text: `${i + 1}. ${s.title}` }), el("div", { text: `${label}${s.seconds != null ? ` ${minutes(s.seconds)}` : ""}` }),
        el("div", {}, el("div", { text: s.message || "" }), bar));
    })));
  }

  function renderState() {
    const job = state.job;
    const host = document.getElementById("rp-state");
    const parts = [];
    if (job && job.state === "error") parts.push(el("div", { class: "rp-error", id: "rp-error", text: job.error }));
    if (job && job.state === "cancelled") parts.push(el("div", { class: "hint", text: "已停止。已完成的步驟會保留，再按開始會從停下的地方繼續。" }));
    if (job && job.state === "done") {
      parts.push(el("div", { class: "button-row" },
        el("button", { type: "button", id: "rp-open-query", text: "→ 開啟情境查詢 Open Situation Query", onclick: () => window.treepoloPanels?.activate?.(QUERY_PANEL_ID, { updateUrl: true, source: "pipeline" }) }),
        el("button", { type: "button", text: "查看研究紀錄 Research Runs", onclick: () => window.treepoloPanels?.activate?.(RUNS_PANEL_ID, { updateUrl: true, source: "pipeline" }) })));
      parts.push(...summary(job));
    }
    host.replaceChildren(...parts);
  }

  function summary(job) {
    const s = job.summary;
    if (!s) return [];
    const out = [];
    if (s.best_params) out.push(el("div", { class: "hint", id: "rp-best", text: `選到的模型設定：樹深 ${s.best_params.max_depth}、葉節點 ${s.best_params.min_leaf}、學習率 ${s.best_params.learning_rate}、L2 ${s.best_params.l2}` }));
    Object.entries(s.groups).forEach(([group, g]) => {
      const r1 = g.r1;
      const card = el("div", { class: "rp-card", "data-group": group }, el("h4", { text: `${group}` }),
        el("div", {}, "引擎檢查（照實際配球算出的期望失分，要對得上得分期望值表）：", el("span", { class: r1.passed ? "rp-ok" : "rp-bad", text: r1.passed ? " 通過" : " 沒通過" }),
          ` （各狀態中位數差 ${num(r1.median_abs_diff)}、最大差 ${num(r1.max_abs_diff)}、打席起點差 ${num(r1.start_diff)}）`));
      if (g.policies.length) card.append(el("table", { class: "rp-table" }, el("thead", {}, el("tr", {}, ...["政策", "算法", "每打席少失分", "區間", "每隊每季少失分"].map(h => el("th", { text: h })))),
        el("tbody", {}, ...g.policies.map(p => el("tr", {}, el("td", { text: p.policy }), el("td", { text: p.kind }), el("td", { text: num(p.saving_pa) }), el("td", { text: p.lo == null ? "—" : `[${num(p.lo)}, ${num(p.hi)}]` }), el("td", { text: num(p.saving_per_team_season, 1) }))))));
      if (g.gates.length) card.append(el("div", { class: "hint", text: `球員描述有沒有用（沒見過的球員）：${g.gates.filter(x => x.gate === "G1a" || x.gate === "G1b").map(x => `${x.side} ${x.gate} ${x.passed ? "過" : "沒過"}`).join("；")}` }));
      if (g.actions.length) card.append(el("table", { class: "rp-table" }, el("thead", {}, el("tr", {}, ...["球種", "相對一般配球", "區間", "多投 10pp 的變化", "偵測到的比例"].map(h => el("th", { text: h })))),
        el("tbody", {}, ...g.actions.map(a => el("tr", {}, el("td", { text: a.action }), el("td", { text: num(a.advantage) }), el("td", { text: `[${num(a.adv_lo)}, ${num(a.adv_hi)}]` }), el("td", { text: num(a.marginal) }), el("td", { text: `${(a.detected_share * 100).toFixed(0)}%` }))))));
      out.push(card);
    });
    return out;
  }

  function render() {
    const running = !!(state.job && state.job.state === "running");
    if (state.formRunning !== running || !document.getElementById("rp-start")) { renderForm(); state.formRunning = running; }      // the form is rebuilt only when it must change, so a poll never resets the choices
    renderSteps(); renderState();
  }
  async function guard(fn) { try { say(""); await fn(); } catch (error) { say(`錯誤 Error: ${error.message}`); } }

  async function open() {
    if (!state.info) state.info = await api("/api/research/pipeline/info");
    await refresh();
    if (state.job && state.job.state === "running") poll();
  }

  function inject() {
    if (document.getElementById(PANEL_ID)) return;
    injectStyles();
    const nav = document.querySelector(".navigation-pane");
    const main = document.querySelector(".main-pane");
    if (!nav || !main) return;
    const research = [...nav.querySelectorAll(".task-group")].find(g => (g.textContent || "").includes("研究 Research"));
    const button = el("button", { class: "nav-item", "data-panel": PANEL_ID, text: "一鍵研究 Run Everything" });
    if (research) research.insertBefore(button, research.children[1] || null); else nav.append(el("div", { class: "task-group" }, el("div", { class: "task-group-title", text: "研究 Research" }), button));
    button.addEventListener("click", () => window.treepoloPanels?.activate?.(PANEL_ID, { updateUrl: true, source: "nav" }));
    const panel = el("div", { id: PANEL_ID, class: "panel" },
      el("div", { class: "panel-heading", text: "一鍵研究 Run Everything" }),
      el("div", { class: "panel-body" },
        el("p", { class: "hint", text: "按一下，依序做完：①模型參數搜尋 → ②訓練全聯盟模型 → ③固定效應對照答案 → ④決策引擎與情境表 → ⑤查詢工具。結果完成後到「情境查詢」使用。相同設定做過的步驟會直接沿用。" }),
        el("div", { id: "rp-message", class: "rr-status" }), el("div", { id: "rp-form" }), el("div", { id: "rp-steps" }), el("div", { id: "rp-state" })));
    main.insertBefore(panel, document.querySelector("#result-window"));
    window.treepoloPanels?.register?.(PANEL_ID, ROUTE);
    document.addEventListener("treepolo:panel-activated", event => { if (event.detail?.panelId === PANEL_ID) guard(open); });
    if (new URL(window.location.href).searchParams.get("page") === ROUTE) window.treepoloPanels?.activate?.(PANEL_ID, { updateUrl: false, source: "initial-route" });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", () => setTimeout(inject, 0), { once: true });
  else setTimeout(inject, 0);
})();
