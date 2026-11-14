(() => {
  "use strict";

  const PANEL_ID = "research-pipeline-panel";
  const ROUTE = "research-pipeline";
  const QUERY_PANEL_ID = "research-query-panel";
  const RUNS_PANEL_ID = "research-runs-panel";
  const STATUS = { pending: ["⏳", "等待"], running: ["▶", "執行中"], done: ["✔", "完成"], error: ["✖", "失敗"] };
  const state = { info: null, job: null, timer: null, tier: null, groups: null, formRunning: null, mode: "simple", doc: null, methods: null, stepKey: "search", form: null, docErrors: [], valid: null,
    cmp: { models: ["logistic", "gbm"], overrides: "{}", years: "2023, 2024", groups: ["RvR"], form: null, estimate: null, result: null } };

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
      .rp-mode { margin:6px 0; } .rp-mode label { margin-right:16px; font-size:13px; }
      .rp-tabs { display:flex; flex-wrap:wrap; gap:4px; margin:8px 0 4px; }
      .rp-tab { padding:3px 10px; border:1px solid #adb8c7; background:#eef2f7; cursor:pointer; } .rp-tab.active { background:#2f6fb2; color:#fff; } .rp-tab.bad { border-color:#c0392b; color:#b03a2e; font-weight:700; }
      .rp-estimate { margin:6px 0; padding:6px 8px; border:1px solid #adb8c7; background:#f7f8fa; font-size:12px; }
      .rp-file-errors { margin:6px 0; padding:6px 8px; border:1px solid #c0392b; background:#fdeeee; font-size:12px; } .rp-file-ok { margin:6px 0; padding:6px 8px; border:1px solid #2e7d4f; background:#f1faf3; font-size:12px; }
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

  // ---- the two modes -------------------------------------------------------------------------------------------------------
  async function loadMethods() {
    if (state.methods) return;
    const body = await api("/api/research/methods");
    state.methods = Object.fromEntries(body.methods.map(m => [m.kind, m]));
  }

  async function enterFull() {
    if (!window.TreepoloSettingsForm) throw new Error("設定表單模組沒有載入 The settings form module is not loaded");
    await loadMethods();
    if (!state.doc) {
      const tier = state.tier || document.querySelector("input[name=rp-tier]:checked")?.value || state.info.tiers[0].id;
      state.doc = (await api("/api/research/pipeline/full-config", { tier, groups: selectedGroups().length ? selectedGroups() : (state.groups || state.info.groups) })).file;     // what the simple mode would run, written out in full
      state.valid = null; state.docErrors = [];
    }
    state.mode = "full"; state.stepKey = state.stepKey || "search";
    renderForm();
  }

  function leaveFull() { if (state.mode === "full") collectDoc(); state.mode = "simple"; renderForm(); }

  function currentMethod() { return state.methods[state.info.step_methods[state.stepKey].kind]; }

  function collectCurrentStep() {
    if (!state.form || !state.doc) return {};
    const { values, errors } = state.form.read();
    const next = { ...(state.doc.steps[state.stepKey] || {}) };
    state.form.names().forEach(n => { delete next[n]; });
    state.doc.steps[state.stepKey] = Object.assign(next, values);
    return errors;
  }

  function collectDoc() {
    if (!state.doc) return {};
    const parseErrors = collectCurrentStep();
    const groups = [...document.querySelectorAll("input[name=rp-full-group]:checked")].map(n => n.value);
    if (document.querySelector("input[name=rp-full-group]")) state.doc.groups = groups;
    const years = document.getElementById("rp-full-years");
    if (years) state.doc.years = years.value.split(",").map(x => x.trim()).filter(Boolean).map(Number);
    return parseErrors;
  }

  const errorsFor = step => Object.fromEntries(state.docErrors.filter(e => e.step === step && e.field).map(e => [e.field, e.message]));
  const stepHasError = step => state.docErrors.some(e => e.step === step);

  async function validateDoc() {
    const parseErrors = collectDoc();
    const res = await api("/api/research/pipeline/validate", { file: state.doc });
    state.docErrors = res.errors.concat(Object.entries(parseErrors).map(([field, message]) => ({ step: state.stepKey, field, message })));
    state.valid = res.ok && !Object.keys(parseErrors).length;
    renderForm();
    return state.valid;
  }

  async function startFull() {
    if (!(await validateDoc())) throw new Error(`設定檔有 ${state.docErrors.length} 個問題，請修正標紅的欄位後再開始（驗證通過才能開始） Fix the marked settings first`);
    state.job = await api("/api/research/pipeline/start", { file: state.doc });
    render(); poll();
  }

  function exportDoc() {
    collectDoc();
    state.doc.created_at = new Date().toISOString();
    const blob = new Blob([JSON.stringify(state.doc, null, 2)], { type: "application/json" });
    const link = el("a", { href: URL.createObjectURL(blob), download: "treepolo-pipeline-settings.json" });
    document.body.append(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(link.href), 1000);
  }

  async function importDoc(file) {
    const raw = await file.text();
    let parsed;
    try { parsed = JSON.parse(raw.replace(/^\uFEFF/, "")); } catch (e) { throw new Error(`這不是有效的 JSON 檔：${e.message} Not a valid JSON file`); }
    await loadMethods();
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) { parsed.steps = (parsed.steps && typeof parsed.steps === "object") ? parsed.steps : {}; }
    state.doc = parsed; state.mode = "full"; state.stepKey = state.stepKey || "search";
    const res = await api("/api/research/pipeline/validate", { file: parsed });         // the form shows the file whatever is wrong with it, and marks what is
    state.docErrors = res.errors; state.valid = res.ok;
    renderForm();
    say(res.ok ? "已匯入，驗證通過 Imported and valid" : `已匯入，但有 ${res.errors.length} 個問題，請看標紅的欄位 Imported with ${res.errors.length} problems`);
  }

  // ---- the comparison mode ---------------------------------------------------------------------------------------------------
  const COMPARE_HIDDEN = ["models", "overrides", "groups", "bunt_policy", "filters"];

  function compareConfig() {
    const c = state.cmp;
    const { values, errors } = c.form ? c.form.read() : { values: {}, errors: {} };
    let overrides = {};
    try { overrides = JSON.parse(c.overrides || "{}"); } catch (e) { throw new Error(`各模型自己的設定不是有效的 JSON：${e.message}`); }
    if (Object.keys(errors).length) throw new Error(`設定有格式錯誤：${Object.entries(errors).map(([k, v]) => `${k}（${v}）`).join("、")}`);
    return { ...values, models: c.models, overrides };
  }

  async function enterCompare() {
    if (!window.TreepoloSettingsForm) throw new Error("設定表單模組沒有載入 The settings form module is not loaded");
    await loadMethods();
    state.mode = "compare"; renderForm();
  }

  async function estimateCompare() {
    const cfg = compareConfig();
    const years = state.cmp.years.split(",").map(x => x.trim()).filter(Boolean).map(Number);
    const settings = Object.fromEntries(Object.entries(cfg).filter(([k]) => !["models", "overrides", "train_years", "test_years", "cold_by", "cold_folds", "compare_baseline"].includes(k)));
    say("預估中：每種模型會先在約 2% 的資料上試跑一小段，請稍候… Estimating…");
    state.cmp.estimate = await api("/api/research/pipeline/estimate", { models: cfg.models, groups: state.cmp.groups, years, train_years: cfg.train_years || [years[0]], cold_by: cfg.cold_by || [], cold_folds: cfg.cold_folds || 3,
      settings, overrides: cfg.overrides });
    say(""); renderForm();
  }

  async function startCompare() {
    const cfg = compareConfig();
    const years = state.cmp.years.split(",").map(x => x.trim()).filter(Boolean).map(Number);
    state.job = await api("/api/research/compare/start", { years, groups: state.cmp.groups, config: cfg });
    render(); poll();
  }

  function estimateBox() {
    const e = state.cmp.estimate;
    if (!e) return el("div", { class: "hint", text: "按「預估時間」會先實際試跑一小段，再顯示每種模型大約要多久；看過預估再按「開始比較」。" });
    const rows = e.estimates.map(r => el("tr", {}, el("td", { text: r.group }), el("td", { text: MODEL_LABEL[r.model] || r.model }), el("td", { text: String(r.rows_train) }), el("td", { text: minutes(r.probe_seconds) }),
      el("td", { text: minutes(r.seconds) }), el("td", { text: `${minutes(r.low)} ～ ${minutes(r.high)}` })));
    return el("div", { class: "rp-estimate", id: "rp-estimate" },
      el("table", { class: "rp-table" }, el("thead", {}, el("tr", {}, ...["分組", "模型", "訓練列數", "試跑耗時", "預估總耗時", "可能範圍"].map(h => el("th", { text: h })))), el("tbody", {}, ...rows)),
      el("div", { text: `合計約 ${minutes(e.total_seconds)}；試跑本身花了 ${minutes(e.trial_seconds)}。${e.note}` }),
      ...(e.skipped.length ? [el("div", { class: "hint", text: `沒有預估：${e.skipped.map(x => `${x.model}（${x.reason}）`).join("；")}` })] : []));
  }

  function compareForm(running) {
    const info = state.info, c = state.cmp, method = state.methods["model_compare"];
    const modelBoxes = el("fieldset", {}, el("legend", { text: "要比較的模型 Models" }), ...info.model_choices.map(m => {
      const available = info.models.includes(m);
      return el("label", {}, el("input", { type: "checkbox", name: "rp-cmp-model", value: m, ...(c.models.includes(m) ? { checked: "checked" } : {}), ...(!available || running ? { disabled: "disabled" } : {}),
        onchange: () => { c.models = [...document.querySelectorAll("input[name=rp-cmp-model]:checked")].map(n => n.value); } }), ` ${MODEL_LABEL[m] || m}${available ? "" : "（需要安裝 PyTorch）"}`);
    }));
    const groups = el("fieldset", {}, el("legend", { text: "手別組 Groups" }), ...info.groups.map(g => el("label", {}, el("input", { type: "checkbox", name: "rp-cmp-group", value: g, ...(c.groups.includes(g) ? { checked: "checked" } : {}),
      ...(running ? { disabled: "disabled" } : {}), onchange: () => { c.groups = [...document.querySelectorAll("input[name=rp-cmp-group]:checked")].map(n => n.value); } }), ` ${g}`)));
    const years = el("fieldset", {}, el("legend", { text: "資料年份 Years（逗號分開）" }), el("input", { type: "text", id: "rp-cmp-years", value: c.years, ...(running ? { disabled: "disabled" } : {}), onchange: event => { c.years = event.target.value; } }));
    const keep = c.form ? c.form.read().values : {};
    c.form = window.TreepoloSettingsForm.buildForm(method.fields, keep, { exclude: COMPARE_HIDDEN });
    const overrides = el("label", { class: "hint" }, "各模型自己的設定（JSON，模型名稱對設定，例如 {\"gbm\": {\"max_depth\": 8}}）", el("textarea", { id: "rp-cmp-overrides", style: "width:100%;min-height:48px", spellcheck: "false", onchange: event => { c.overrides = event.target.value; } }, c.overrides));
    return el("div", {},
      el("div", { class: "rp-form" }, modelBoxes, groups, years), overrides, c.form.node, estimateBox(),
      el("div", { class: "button-row" },
        el("button", { type: "button", id: "rp-estimate-btn", text: "預估時間 Estimate", ...(running ? { disabled: "disabled" } : {}), onclick: () => guard(estimateCompare) }),
        el("button", { type: "button", id: "rp-start", text: running ? "執行中…" : "▶ 開始比較 Start comparison", ...(running ? { disabled: "disabled" } : {}), onclick: () => guard(startCompare) }),
        el("button", { type: "button", id: "rp-cancel", text: "停止 Stop", ...(running ? {} : { disabled: "disabled" }), onclick: () => guard(async () => { state.job = await api("/api/research/pipeline/cancel", {}); render(); }) })));
  }

  function compareResult(job) {
    const id = job.summary && job.summary.compare_run_id;
    if (!id) return [];
    const host = el("div", { id: "rp-compare-result", class: "rp-card" }, el("div", { class: "hint", text: "讀取比較結果…" }));
    api(`/api/research/runs/${id}/result?section=0&limit=200`).then(page => {
      const cols = ["group", "model", "mode", "n_eval", "logloss", "brier", "calibration_error", "fit_seconds"];
      host.replaceChildren(el("div", { class: "hint", text: "並排比較（每個模型一列，依你選的欄位排序；沒有「哪個最好」的判斷。完整的球種效果與兩兩差異在研究紀錄裡）：" }),
        el("table", { class: "rp-table" }, el("thead", {}, el("tr", {}, ...cols.map(h => el("th", { text: h })))),
          el("tbody", {}, ...page.rows.map(r => el("tr", {}, ...cols.map(h => el("td", { text: typeof r[h] === "number" && !Number.isInteger(r[h]) ? r[h].toFixed(5) : (r[h] == null ? "—" : String(r[h])) })))))));
    }).catch(error => host.replaceChildren(el("div", { class: "rp-error", text: `讀取失敗：${error.message}` })));
    return [host];
  }

  function modeToggle(running) {
    const radio = (value, label, handler) => el("label", {}, el("input", { type: "radio", name: "rp-mode", value, ...(state.mode === value ? { checked: "checked" } : {}), ...(running ? { disabled: "disabled" } : {}), onchange: () => guard(handler) }), ` ${label}`);
    return el("div", { class: "rp-mode", id: "rp-mode" }, radio("simple", "簡單模式 Simple", leaveFull), radio("full", "完整設定模式 Full settings", enterFull), radio("compare", "比較模式 Compare models", enterCompare));
  }

  function simpleForm(running) {
    const info = state.info;
    return el("div", { class: "rp-form" },
      el("fieldset", {}, el("legend", { text: "運算量 Size" }),
        ...info.tiers.map((t, i) => el("label", {}, el("input", { type: "radio", name: "rp-tier", value: t.id, ...((state.tier ? state.tier === t.id : i === 0) ? { checked: "checked" } : {}), ...(running ? { disabled: "disabled" } : {}),
        onchange: () => { state.tier = t.id; } }), ` ${t.label}`))),
      el("fieldset", {}, el("legend", { text: "手別組 Groups" }),
        ...info.groups.map(g => el("label", {}, el("input", { type: "checkbox", name: "rp-group", value: g, ...((!state.groups || state.groups.includes(g)) ? { checked: "checked" } : {}), ...(running ? { disabled: "disabled" } : {}),
        onchange: () => { state.groups = selectedGroups(); } }), ` ${g}`))),
      el("div", {},
        el("button", { type: "button", id: "rp-start", text: running ? "執行中…" : "▶ 開始 Start", ...(running ? { disabled: "disabled" } : {}), onclick: () => guard(start) }),
        el("button", { type: "button", id: "rp-cancel", text: "停止（目前步驟結束後）Stop", ...(running ? {} : { disabled: "disabled" }), onclick: () => guard(async () => { state.job = await api("/api/research/pipeline/cancel", {}); render(); }) })));
  }

  function fullForm(running) {
    const info = state.info, doc = state.doc, meta = info.step_methods[state.stepKey];
    const fileInput = el("input", { type: "file", accept: ".json,application/json", id: "rp-import-file", style: "display:none", onchange: event => { const f = event.target.files[0]; event.target.value = ""; if (f) guard(() => importDoc(f)); } });
    const top = el("div", { class: "rp-form" },
      el("fieldset", {}, el("legend", { text: "手別組 Groups" }),
        ...info.groups.map(g => el("label", {}, el("input", { type: "checkbox", name: "rp-full-group", value: g, ...((doc.groups || []).includes(g) ? { checked: "checked" } : {}), ...(running ? { disabled: "disabled" } : {}) }), ` ${g}`))),
      el("fieldset", {}, el("legend", { text: "年份 Years（逗號分開）" }), el("input", { type: "text", id: "rp-full-years", value: (doc.years || []).join(", "), ...(running ? { disabled: "disabled" } : {}) })));
    const tabs = el("div", { class: "rp-tabs", id: "rp-tabs" }, ...Object.entries(info.step_methods).map(([key, m]) =>
      el("button", { type: "button", class: `rp-tab${key === state.stepKey ? " active" : ""}${stepHasError(key) ? " bad" : ""}`, "data-step-key": key, text: m.title, onclick: () => { collectDoc(); state.stepKey = key; renderForm(); } })));
    const method = currentMethod();
    const exclude = [...info.from_the_top, ...meta.auto_fields, ...(["anchor", "policy"].includes(state.stepKey) ? ["re_scope"] : [])];
    state.form = window.TreepoloSettingsForm.buildForm(method.fields, doc.steps[state.stepKey] || {}, { exclude });
    state.form.setErrors(errorsFor(state.stepKey));
    const optional = meta.optional ? el("label", { class: "hint" }, el("input", { type: "checkbox", id: "rp-step-enabled", ...((doc.enabled || {})[state.stepKey] !== false ? { checked: "checked" } : {}), ...(running ? { disabled: "disabled" } : {}),
      onchange: event => { doc.enabled = { ...(doc.enabled || {}), [state.stepKey]: event.target.checked }; } }), " 做這一步（取消勾選就略過）") : null;
    const auto = meta.auto_fields.length ? el("div", { class: "hint", text: `這一步會自動接上前面步驟的結果（${meta.auto_fields.join("、")}），不用填。` }) : null;
    const status = state.valid === true ? el("div", { class: "rp-file-ok", id: "rp-file-ok", text: "驗證通過 Valid" })
      : (state.docErrors.length ? el("div", { class: "rp-file-errors", id: "rp-file-errors", text: `有 ${state.docErrors.length} 個問題：` + state.docErrors.slice(0, 12).map(e => `${e.step ? `[${e.step}]` : ""}${e.field ? `${e.field}：` : ""}${e.message}`).join("；") }) : null);
    return el("div", {},
      top, tabs, optional, auto, state.form.node, status,
      el("div", { class: "button-row" },
        el("button", { type: "button", id: "rp-validate", text: "驗證 Validate", ...(running ? { disabled: "disabled" } : {}), onclick: () => guard(validateDoc) }),
        el("button", { type: "button", id: "rp-export", text: "匯出設定檔 Export", onclick: () => guard(async () => exportDoc()) }),
        el("button", { type: "button", id: "rp-import", text: "匯入設定檔 Import", ...(running ? { disabled: "disabled" } : {}), onclick: () => fileInput.click() }), fileInput,
        el("button", { type: "button", id: "rp-start", text: running ? "執行中…" : "▶ 開始 Start", ...(running ? { disabled: "disabled" } : {}), onclick: () => guard(startFull) }),
        el("button", { type: "button", id: "rp-cancel", text: "停止（目前步驟結束後）Stop", ...(running ? {} : { disabled: "disabled" }), onclick: () => guard(async () => { state.job = await api("/api/research/pipeline/cancel", {}); render(); }) })));
  }

  function renderForm() {
    const host = document.getElementById("rp-form");
    const info = state.info;
    const env = info.environment;
    const running = state.job && state.job.state === "running";
    const envText = env.xgboost ? (env.cuda ? "已偵測到 XGBoost 與 GPU，會用 GPU 訓練。" : "已偵測到 XGBoost，但沒有可用的 GPU，會用 CPU 訓練。") : "沒有安裝 XGBoost，會用 scikit-learn（CPU，較慢）。";
    host.replaceChildren(el("p", { class: "hint", id: "rp-env", text: envText }), modeToggle(running), state.mode === "compare" && state.methods ? compareForm(running) : (state.mode === "full" && state.doc ? fullForm(running) : simpleForm(running)));
  }

  function timeText(step, job) {
    if (step.status !== "running" || !step.started_at || !job.server_time) return "";
    const elapsed = Math.max(0, job.server_time - step.started_at);
    const f = step.fraction;
    const left = f != null && f > 0.02 && f < 1 ? elapsed * (1 - f) / f : null;
    return `已用 ${minutes(elapsed)}${left != null ? `，約再 ${minutes(left)}（依目前進度推算，粗估）` : ""}`;
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
        el("div", {}, el("div", { text: [s.message || "", timeText(s, job)].filter(Boolean).join("　") }), bar));
    })));
  }

  function renderState() {
    const job = state.job;
    const host = document.getElementById("rp-state");
    const parts = [];
    if (job && job.state === "error") parts.push(el("div", { class: "rp-error", id: "rp-error", text: job.error }));
    if (job && job.state === "cancelled") parts.push(el("div", { class: "hint", text: "已停止。已完成的步驟會保留，再按開始會從停下的地方繼續。" }));
    if (job && job.state === "done" && job.kind === "compare") {
      parts.push(el("div", { class: "button-row" }, el("button", { type: "button", text: "查看研究紀錄 Research Runs", onclick: () => window.treepoloPanels?.activate?.(RUNS_PANEL_ID, { updateUrl: true, source: "pipeline" }) })));
      parts.push(...compareResult(job));
    } else if (job && job.state === "done") {
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
    if (s.model && s.model.kind) {
      const used = Object.entries(s.model.controls || {}).map(([k, v]) => `${CONTROL_LABEL[k] || k}＝${v}`);
      out.push(el("div", { class: "hint", id: "rp-model", text: `用的模型：${MODEL_LABEL[s.model.kind] || s.model.kind}；偏差控制：${used.length ? used.join("、") : "沒有（全部關閉）"}。決策引擎與情境表是在這些假設下算的。` }));
    }
    if (s.hit_cap && s.hit_cap.length) out.push(el("div", { class: "rp-error", id: "rp-hit-cap", text: `⚠ 有 ${s.hit_cap.length} 個擬合撞到迭代上限（hit_cap），表示還沒收斂，數字可能還在變：${s.hit_cap.map(x => `${x.group} ${x.role === "baseline" ? "對照" : "模型"} ${x.mode}`).join("、")}。到設定裡把 max_iter 調高再跑。` }));
    if (s.best_params) out.push(el("div", { class: "hint", id: "rp-best", text: `選到的模型設定：${Object.entries(s.best_params).map(([k, v]) => `${PARAM_LABEL[k] || k} ${v}`).join("、")}` }));
    Object.entries(s.groups).forEach(([group, g]) => {
      const r1 = g.r1;
      const card = el("div", { class: "rp-card", "data-group": group }, el("h4", { text: `${group}` }),
        el("div", { class: "hint", text: `引擎對照（照實際配球算出的期望失分，和真實資料統計出的得分期望值表差多少；0＝完全一致）：各狀態中位數差 ${num(r1.median_abs_diff)}、最大差 ${num(r1.max_abs_diff)}、打席起點差 ${num(r1.start_diff)}` }));
      if (g.policies.length) card.append(el("table", { class: "rp-table" }, el("thead", {}, el("tr", {}, ...["政策", "算法", "每打席少失分", "區間", "每隊每季少失分"].map(h => el("th", { text: h })))),
        el("tbody", {}, ...g.policies.map(p => el("tr", {}, el("td", { text: p.policy }), el("td", { text: p.kind }), el("td", { text: num(p.saving_pa) }), el("td", { text: p.lo == null ? "—" : `[${num(p.lo)}, ${num(p.hi)}]` }), el("td", { text: num(p.saving_per_team_season, 1) }))))));
      if (g.effect_gains.length) {
        card.append(el("div", { class: "hint", text: "球員描述或效應的預測增益（對「全部控制都關掉」的同一個模型；沒見過的球員、沒訓練過的年份；正值＝加了控制比較準；只列數字，不下判斷）：" }));
        card.append(el("table", { class: "rp-table" }, el("thead", {}, el("tr", {}, ...["評估對象", "比較", "數值（對數損失改善）", "區間", "樣本數"].map(h => el("th", { text: h })))),
          el("tbody", {}, ...g.effect_gains.map(x => el("tr", {}, el("td", { text: MODE_LABEL[x.mode] || x.mode }), el("td", { text: x.comparison }),
            el("td", { text: num(x.value, 5) }), el("td", { text: x.lo == null ? "—" : `[${num(x.lo, 5)}, ${num(x.hi, 5)}]` }), el("td", { text: x.n == null ? "—" : String(x.n) }))))));
      }
      if (g.actions.length) card.append(el("table", { class: "rp-table" }, el("thead", {}, el("tr", {}, ...["球種", "相對一般配球", "區間", "多投 10pp 的變化"].map(h => el("th", { text: h })))),
        el("tbody", {}, ...g.actions.map(a => el("tr", {}, el("td", { text: a.action }), el("td", { text: num(a.advantage) }), el("td", { text: `[${num(a.adv_lo)}, ${num(a.adv_hi)}]` }), el("td", { text: num(a.marginal) }))))));
      Object.entries(g.comparisons || {}).forEach(([section, c]) => card.append(el("div", { class: "hint rp-chance", "data-section": section, text: chanceText(section, c) })));
      out.push(card);
    });
    if (s.replication) out.push(el("div", { class: "hint", id: "rp-replication", text: `換年份重看：${s.replication.years.join("、")} 每個年份單獨訓練一份模型（每年 ${s.replication.members_per_year} 個成員），各年份的估計並排放在「研究紀錄」該次執行的 Replication 區段；只並排數字，不做同號就算重現的判斷。` }));
    return out;
  }

  const MODE_LABEL = { season: "留出年份", cold_pitcher: "沒見過的投手", cold_batter: "沒見過的打者", season_new_pitcher: "留出年份裡的新投手", season_new_batter: "留出年份裡的新打者" };
  const MODEL_LABEL = { logistic: "邏輯迴歸", gbm: "梯度提升樹", mixed: "混合效應", nn_embed: "神經網路＋嵌入", sequence: "序列模型" };
  const CONTROL_LABEL = { control_pitcher: "投手整體", control_pitcher_pitch: "投手×球種", control_batter_pitch: "打者×球種", control_day: "當天狀態", control_batter: "打者整體", control_survival: "存活偏誤",
    control_location: "球的位置", control_day_pitch: "當天×球種" };
  const PARAM_LABEL = { max_depth: "樹深", min_leaf: "葉節點", learning_rate: "學習率", l2: "L2" };
  const SECTION_LABEL = { marginal: "邊際價值摘要（各球種）", direction: "方向圖（情境×球種）", anchor: "錨點對照（模型效果對固定效應效果）" };
  function chanceText(section, c) {
    const what = c.what === "outside_anchor_interval" ? "模型效果落在對照區間之外" : "區間不含 0";
    return `${SECTION_LABEL[section] || section}：共比較了 ${c.n} 個；在 ${(c.confidence * 100).toFixed(0)}% 區間下，就算全部其實沒差，純靠運氣也預期約 ${c.expected_by_chance.toFixed(1)} 個「${what}」；實際有 ${c.found} 個。`;
  }

  function render() {
    const running = !!(state.job && state.job.state === "running");
    if (state.formRunning !== running || !document.getElementById("rp-start")) { renderForm(); state.formRunning = running; }      // the form is rebuilt only when it must change, so a poll never resets the choices
    renderSteps(); renderState();
  }
  async function guard(fn) { try { say(""); await fn(); } catch (error) { say(`錯誤 Error: ${error.message}`); } }

  async function open() {
    if (!state.info) {
      const host = document.getElementById("rp-form");
      if (host) host.replaceChildren(el("p", { class: "hint", id: "rp-loading", text: "載入中：正在檢查這台電腦能不能用 XGBoost 與 GPU（第一次打開會實際啟動一次 GPU 測試，約十幾秒到半分鐘，請稍候）…" }));
      state.info = await api("/api/research/pipeline/info");
    }
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
    document.addEventListener("treepolo:panel-activated", event => {
      const here = event.detail?.panelId === PANEL_ID;
      const generic = document.getElementById("result-window");               // the general "Analysis Result / Save / Export" block does not apply to this page
      if (generic) generic.hidden = here;
      if (here) guard(open);
    });
    if (new URL(window.location.href).searchParams.get("page") === ROUTE) window.treepoloPanels?.activate?.(PANEL_ID, { updateUrl: false, source: "initial-route" });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", () => setTimeout(inject, 0), { once: true });
  else setTimeout(inject, 0);
})();
