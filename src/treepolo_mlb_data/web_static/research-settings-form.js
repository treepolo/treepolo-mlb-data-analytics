(() => {
  "use strict";

  // Builds a settings form from the field list that /api/research/methods gives for a research method (name, type, default, choices, range, Chinese and English labels and help).
  // Nothing about a particular method is written here, so a new setting shows up in the form as soon as the method declares it.

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

  function injectStyles() {
    if (document.getElementById("research-settings-form-styles")) return;
    const style = el("style", { id: "research-settings-form-styles" });
    style.textContent = `
      .sf-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(280px,1fr)); gap:8px 14px; margin:8px 0; }
      .sf-field { display:flex; flex-direction:column; gap:2px; font-size:12px; border:1px solid transparent; padding:2px 4px; }
      .sf-field.sf-bad { border-color:#c0392b; background:#fdeeee; }
      .sf-field input[type=number], .sf-field input[type=text], .sf-field select, .sf-field textarea { width:100%; box-sizing:border-box; }
      .sf-field textarea { min-height:64px; font-family:monospace; font-size:12px; }
      .sf-label { font-weight:700; }
      .sf-help { color:#44505e; } .sf-error { color:#b03a2e; font-weight:700; }
      .sf-name { color:#8a94a1; font-family:monospace; }
    `;
    document.head.append(style);
  }

  const isEmpty = v => v === null || v === undefined || v === "";
  const text = (field, zh, en) => [field[zh], field[en]].filter(Boolean).join(" ");

  function show(value, field) {
    if (value === null || value === undefined) return "";
    if (field.type === "json") return JSON.stringify(value, null, 2);
    if (field.type === "str_list" || field.type === "int_list") return Array.isArray(value) ? value.join(", ") : String(value);
    return String(value);
  }

  function control(field, value) {
    if (field.type === "bool") return el("input", { type: "checkbox", "data-setting": field.name, ...(value ? { checked: "checked" } : {}) });
    if (field.type === "choice") {
      const select = el("select", { "data-setting": field.name }, ...field.choices.map(choice => el("option", { value: choice, text: choice })));
      select.value = value === null || value === undefined ? (field.default ?? field.choices[0]) : value;
      return select;
    }
    if (field.type === "int" || field.type === "float") {
      const props = { type: "number", step: field.type === "int" ? "1" : "any", "data-setting": field.name, value: show(value, field) };
      if (field.minimum !== null && field.minimum !== undefined) props.min = String(field.minimum);
      if (field.maximum !== null && field.maximum !== undefined) props.max = String(field.maximum);
      return el("input", props);
    }
    if (field.type === "json") return el("textarea", { "data-setting": field.name, spellcheck: "false" }, show(value, field));
    return el("input", { type: "text", "data-setting": field.name, value: show(value, field), ...(field.type === "str_list" || field.type === "int_list" ? { placeholder: "a, b, c" } : {}) });
  }

  // fields: the "fields" of one method; values: the current settings; options.exclude: names left out of the form.
  // Returns { node, read(), setErrors(map) }. read() gives { values, errors }: errors are the entries that cannot even be parsed (bad JSON, not a number); the method checks the rest.
  function buildForm(fields, values, options = {}) {
    injectStyles();
    const exclude = new Set(options.exclude || []);
    const shown = fields.filter(f => !exclude.has(f.name));
    const rows = new Map();
    const grid = el("div", { class: "sf-grid" });
    shown.forEach(field => {
      const input = control(field, values && Object.prototype.hasOwnProperty.call(values, field.name) ? values[field.name] : field.default);
      const error = el("div", { class: "sf-error", "data-error-for": field.name });
      const help = text(field, "help_zh", "help_en");
      const row = el("div", { class: "sf-field", "data-field": field.name },
        el("label", {}, el("span", { class: "sf-label", text: text(field, "label_zh", "label_en") || field.name }), " ", el("span", { class: "sf-name", text: field.name }), input),
        help ? el("div", { class: "sf-help", text: help }) : null, error);
      if (field.type === "json") input.addEventListener("input", () => { error.textContent = ""; try { JSON.parse(input.value); row.classList.remove("sf-bad"); } catch (e) { error.textContent = `JSON 格式錯誤 invalid JSON: ${e.message}`; row.classList.add("sf-bad"); } });
      rows.set(field.name, { field, input, error, row });
      grid.append(row);
    });

    function read() {
      const out = {}, errors = {};
      rows.forEach(({ field, input }, name) => {
        const raw = field.type === "bool" ? input.checked : input.value;
        if (field.type === "bool") { out[name] = raw; return; }
        if (isEmpty(raw)) { if (field.type === "json") out[name] = null; return; }          // empty = the method's default
        try {
          if (field.type === "int" || field.type === "float") {
            const n = Number(raw);
            if (!Number.isFinite(n)) throw new Error("不是數字 not a number");
            out[name] = n;
          } else if (field.type === "json") out[name] = JSON.parse(raw);
          else if (field.type === "str_list") out[name] = String(raw).split(",").map(x => x.trim()).filter(Boolean);
          else if (field.type === "int_list") {
            const items = String(raw).split(",").map(x => x.trim()).filter(Boolean).map(Number);
            if (items.some(n => !Number.isFinite(n))) throw new Error("要用逗號分開的整數 comma-separated integers");
            out[name] = items;
          } else out[name] = raw;
        } catch (e) { errors[name] = e.message; }
      });
      return { values: out, errors };
    }

    function setErrors(map) {
      rows.forEach(({ error, row }, name) => { const message = map && map[name] ? map[name] : ""; error.textContent = message; row.classList.toggle("sf-bad", !!message); });
    }

    const node = el("div", { class: "sf-form" }, grid);
    return { node, read, setErrors, names: () => [...rows.keys()] };
  }

  window.TreepoloSettingsForm = { buildForm };
})();
