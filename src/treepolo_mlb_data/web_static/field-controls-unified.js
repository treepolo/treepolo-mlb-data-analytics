(() => {
  "use strict";

  if (window.treepoloUnifiedFieldControls) return;
  window.treepoloUnifiedFieldControls = true;

  const FIELD_INPUT_RULES = [
    [".s4-groups", true], [".s4-metric-field", false], [".s4-metric-cond-field", false],
    [".s4-left", false], [".s4-right-field", false], [".s4-field", false],
    [".s4-value-field", false], [".s4-partition", true], [".s4-order", true],
    [".s4-fields", true], ["#s4-cluster-features", true], ["#s4-cluster-ids", true],
    ["#s4-cluster-partitions", true], ["#s4-reg-dependent", false],
    ["#s4-reg-independent", true], ["#s4-boot-value", false], ["#s4-boot-units", true],
    ["#s4-boot-group", false], ["#cc-entities", true], ["#cc-features", true],
    [".ta-entity-fields", true], [".ta-pitch-field", false], [".ta-value-field", false],
    [".ta-percentile-field", false], [".ta-percentile-partition", true],
    [".ta-event-field", false], [".ta-metric-cond-value-field", false],
  ];

  const NATIVE_SINGLE_FIELD_SELECTORS = [
    "select[data-field-select]:not([multiple])",
    ".s4-filter-field",
    ".s4-field-select",
    ".cc-field",
    ".cc-filter-field",
    ".result-sort-field",
  ].join(",");

  const PITCH_TYPES = [
    ["FF", "四縫線速球 Four-Seam Fastball"], ["SI", "伸卡球 Sinker"], ["FC", "卡特球 Cutter"],
    ["SL", "滑球 Slider"], ["ST", "Sweeper"], ["CU", "曲球 Curveball"],
    ["KC", "彈指曲球 Knuckle Curve"], ["CH", "變速球 Changeup"], ["FS", "指叉球 Split-Finger"],
    ["FO", "Forkball"], ["KN", "蝴蝶球 Knuckleball"], ["EP", "Eephus"],
    ["SC", "螺旋球 Screwball"], ["SV", "Slurve"],
  ];

  const VALUE_DOMAINS = {
    pitch_type: PITCH_TYPES,
    stand: [["R", "右打 Right"], ["L", "左打 Left"]],
    p_throws: [["R", "右投 Right"], ["L", "左投 Left"]],
    inning_topbot: [["Top", "上半局 Top"], ["Bot", "下半局 Bottom"]],
    type: [["B", "壞球 Ball"], ["S", "好球／擦棒 Strike"], ["X", "擊球進場 In Play"]],
    bb_type: [["ground_ball", "滾地球 Ground Ball"], ["line_drive", "平飛球 Line Drive"], ["fly_ball", "飛球 Fly Ball"], ["popup", "高飛球 Popup"]],
    description: [
      ["ball", "壞球 Ball"], ["blocked_ball", "擋球 Blocked Ball"], ["called_strike", "主審判好球 Called Strike"],
      ["swinging_strike", "揮空 Swinging Strike"], ["swinging_strike_blocked", "揮空未接捕 Swinging Strike Blocked"],
      ["foul", "界外 Foul"], ["foul_tip", "擦棒被捕 Foul Tip"], ["foul_bunt", "觸擊界外 Foul Bunt"],
      ["missed_bunt", "觸擊揮空 Missed Bunt"], ["hit_into_play", "擊球進場 In Play"],
      ["hit_by_pitch", "觸身球 Hit By Pitch"], ["pitchout", "Pitchout"],
    ],
    events: [
      ["single", "一壘安打 Single"], ["double", "二壘安打 Double"], ["triple", "三壘安打 Triple"], ["home_run", "全壘打 Home Run"],
      ["walk", "四壞 Walk"], ["intent_walk", "故意四壞 Intentional Walk"], ["hit_by_pitch", "觸身球 Hit By Pitch"],
      ["strikeout", "三振 Strikeout"], ["strikeout_double_play", "三振雙殺 Strikeout Double Play"], ["field_out", "一般出局 Field Out"],
      ["force_out", "封殺 Force Out"], ["grounded_into_double_play", "滾地雙殺 Grounded Into Double Play"], ["double_play", "雙殺 Double Play"],
      ["triple_play", "三殺 Triple Play"], ["field_error", "失誤 Field Error"], ["fielders_choice", "野手選擇 Fielder's Choice"],
      ["fielders_choice_out", "野手選擇出局 Fielder's Choice Out"], ["sac_fly", "高飛犧牲打 Sac Fly"], ["sac_bunt", "犧牲觸擊 Sac Bunt"],
      ["sac_fly_double_play", "高飛犧牲雙殺 Sac Fly Double Play"], ["sac_bunt_double_play", "犧牲觸擊雙殺 Sac Bunt Double Play"],
      ["catcher_interf", "捕手妨礙 Catcher Interference"], ["other_out", "其他出局 Other Out"],
    ],
    game_type: [["R", "例行賽 Regular Season"], ["S", "春訓 Spring Training"], ["F", "外卡 Wild Card"], ["D", "分區系列賽 Division Series"], ["L", "聯盟冠軍賽 League Championship"], ["W", "世界大賽 World Series"], ["A", "明星賽 All-Star"], ["E", "表演賽 Exhibition"]],
    if_fielding_alignment: [["Standard", "標準 Standard"], ["Infield shift", "內野佈陣 Infield Shift"], ["Strategic", "策略性 Strategic"], ["4th outfielder", "第四外野手 4th Outfielder"]],
    of_fielding_alignment: [["Standard", "標準 Standard"], ["Strategic", "策略性 Strategic"], ["4th outfielder", "第四外野手 4th Outfielder"]],
    __arrangement: [["consecutive", "全部連續 All Consecutive"], ["none_adjacent", "完全不相鄰 None Adjacent"], ["any", "不限 Any"]],
  };

  const MLB_TEAMS = ["ARI","ATH","ATL","BAL","BOS","CHC","CWS","CIN","CLE","COL","DET","HOU","KC","LAA","LAD","MIA","MIL","MIN","NYM","NYY","PHI","PIT","SD","SEA","SF","STL","TB","TEX","TOR","WSH"];
  VALUE_DOMAINS.home_team = MLB_TEAMS.map(value => [value, `球隊 Team · ${value}`]);
  VALUE_DOMAINS.away_team = VALUE_DOMAINS.home_team;
  VALUE_DOMAINS.pitch_name = [
    ["4-Seam Fastball","四縫線速球 4-Seam Fastball"], ["Sinker","伸卡球 Sinker"], ["Cutter","卡特球 Cutter"],
    ["Slider","滑球 Slider"], ["Sweeper","Sweeper"], ["Curveball","曲球 Curveball"],
    ["Knuckle Curve","彈指曲球 Knuckle Curve"], ["Changeup","變速球 Changeup"], ["Split-Finger","指叉球 Split-Finger"],
    ["Forkball","Forkball"], ["Knuckleball","蝴蝶球 Knuckleball"], ["Eephus","Eephus"], ["Screwball","螺旋球 Screwball"], ["Slurve","Slurve"],
  ];
  VALUE_DOMAINS.zone = Array.from({ length:14 }, (_, index) => [String(index + 1), `好球區 Zone ${index + 1}`]);
  VALUE_DOMAINS.balls = Array.from({ length:4 }, (_, index) => [String(index), `壞球數 Balls ${index}`]);
  VALUE_DOMAINS.strikes = Array.from({ length:3 }, (_, index) => [String(index), `好球數 Strikes ${index}`]);
  VALUE_DOMAINS.outs_when_up = Array.from({ length:3 }, (_, index) => [String(index), `出局數 Outs ${index}`]);
  VALUE_DOMAINS.inning = Array.from({ length:20 }, (_, index) => [String(index + 1), `第 ${index + 1} 局 Inning ${index + 1}`]);
  VALUE_DOMAINS.game_year = Array.from({ length:Math.max(1, new Date().getFullYear() - 2014) }, (_, index) => {
    const value = String(new Date().getFullYear() - index);
    return [value, `球季 Season ${value}`];
  });

  const norm = value => String(value ?? "").toLowerCase().replace(/\s+/g, " ").trim();
  const baseLabels = () => new Map(Array.from(document.querySelector("#basic-group")?.options || [])
    .filter(option => option.value)
    .map(option => [option.value, option.textContent || option.value]));

  function injectStyles() {
    if (document.getElementById("unified-field-control-styles")) return;
    const style = document.createElement("style");
    style.id = "unified-field-control-styles";
    style.textContent = `
      .ta-field-combo{display:none!important}
      .xp-field-shell,.xp-field-input-shell,.xp-semantic-shell{position:relative;display:block;width:100%;min-width:0}
      .xp-field-shell>select{display:none!important}
      .xp-field-entry,.xp-field-input-shell>input,.xp-semantic-shell>input{
        box-sizing:border-box;width:100%;min-width:0;min-height:24px;padding:2px 19px 2px 4px;
        border:1px solid #7f9db9;border-radius:0;background:#fff;color:#111;
        font:inherit;box-shadow:inset 1px 1px 1px rgba(0,0,0,.08)
      }
      .xp-field-shell::after,.xp-field-input-shell::after,.xp-semantic-shell.xp-has-domain::after{
        content:"▼";position:absolute;right:4px;top:50%;transform:translateY(-50%);
        font-size:9px;color:#333;pointer-events:none
      }
      .xp-field-entry.ta-invalid,.xp-field-input-shell>input.ta-invalid{outline:2px solid #b12828;background:#fff3f3}
      .xp-field-popup{
        position:absolute;left:0;top:calc(100% + 1px);z-index:10030;width:100%;
        min-width:270px;max-width:min(560px,90vw);max-height:245px;overflow:auto;
        border:1px solid #1f4e79;background:#fff;box-shadow:2px 2px 0 rgba(0,0,0,.22);
        padding:2px;color:#111
      }
      .xp-field-option{
        display:block;width:100%;min-height:21px;padding:2px 5px;border:0;border-radius:0;
        background:#fff;box-shadow:none;text-align:left;white-space:nowrap;overflow:hidden;
        text-overflow:ellipsis;color:#111
      }
      .xp-field-option:hover,.xp-field-option:focus{background:#316ac5;color:#fff;outline:0;box-shadow:none}
      .xp-field-option.xp-selected::before{content:"✓ ";font-weight:700}
      .xp-field-empty{padding:5px;color:#666}
    `;
    document.head.append(style);
  }

  function removeNativeDatalist(input) {
    if (!input) return;
    const listId = input.getAttribute("list");
    if (listId) {
      input.removeAttribute("list");
      document.getElementById(listId)?.remove();
    }
    const sibling = input.nextElementSibling;
    if (sibling?.tagName === "DATALIST") sibling.remove();
  }

  function cleanupLegacySingleSelect(select) {
    select.dataset.taCombo = "1";
    const previous = select.previousElementSibling;
    if (previous?.classList?.contains("ta-field-combo")) {
      removeNativeDatalist(previous);
      previous.remove();
    }
    const next = select.nextElementSibling;
    if (next?.tagName === "DATALIST") next.remove();
    const oldShell = select.closest(".xp-select-shell");
    if (oldShell) {
      oldShell.querySelectorAll(".xp-field-search-button,.xp-popup").forEach(node => node.remove());
      oldShell.parentNode.insertBefore(select, oldShell);
      oldShell.remove();
    }
    select.hidden = false;
    select.removeAttribute("hidden");
    select.style.display = "";
  }

  function cleanupLegacyFieldInput(input) {
    input.dataset.taFieldAssist = "1";
    removeNativeDatalist(input);
    const oldShell = input.closest(".xp-edit-shell");
    if (oldShell) {
      oldShell.querySelectorAll(".xp-combo-button,.xp-popup").forEach(node => node.remove());
      oldShell.parentNode.insertBefore(input, oldShell);
      oldShell.remove();
    }
  }

  function labelFor(value, control) {
    if (!value) return "";
    const native = control?.tagName === "SELECT"
      ? Array.from(control.options || []).find(option => option.value === value)
      : null;
    if (native?.textContent) return native.textContent;
    const label = baseLabels().get(value);
    if (label) return label;
    return `前一步輸出 Prior-stage alias · ${value}`;
  }

  function nativeOutputOptions(select) {
    return Array.from(select.options || [])
      .filter(option => option.value)
      .map(option => ({ value:option.value, label:option.textContent || option.value }));
  }

  function legalFieldOptions(control) {
    if (control?.matches?.(".result-sort-field")) return nativeOutputOptions(control);
    const provider = window.treepoloLegalFieldOptions?.available;
    let values = [];
    let resolved = false;
    if (typeof provider === "function") {
      try {
        values = provider(control) || [];
        resolved = true;
      } catch {
        resolved = false;
      }
    }
    if (!resolved && control?.tagName === "SELECT") {
      values = Array.from(control.options || []).map(option => option.value).filter(Boolean);
      resolved = true;
    }
    if (!resolved) values = Array.from(baseLabels().keys());
    return Array.from(new Set(values)).map(value => ({ value, label:labelFor(value, control) }));
  }

  function exactOption(options, text) {
    const target = norm(text);
    if (!target) return null;
    return options.find(option => norm(option.value) === target)
      || options.find(option => norm(option.label) === target)
      || null;
  }

  function closePopups(except = null) {
    document.querySelectorAll(".xp-field-popup").forEach(popup => {
      if (popup !== except) popup.remove();
    });
  }

  function renderPopup(shell, options, query, selectedValues, choose) {
    let popup = shell.querySelector(":scope > .xp-field-popup");
    if (!popup) {
      closePopups();
      popup = document.createElement("div");
      popup.className = "xp-field-popup";
      shell.append(popup);
    }
    const q = norm(query);
    const filtered = options.filter(option => !q || norm(`${option.label} ${option.value}`).includes(q));
    popup.innerHTML = "";
    if (!filtered.length) {
      const empty = document.createElement("div");
      empty.className = "xp-field-empty";
      empty.textContent = "沒有合法項目 No legal matches";
      popup.append(empty);
      return popup;
    }
    const selected = new Set(selectedValues || []);
    filtered.forEach(option => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = `xp-field-option${selected.has(option.value) ? " xp-selected" : ""}`;
      button.textContent = option.label.includes(option.value) ? option.label : `${option.label} (${option.value})`;
      button.addEventListener("mousedown", event => event.preventDefault());
      button.addEventListener("click", () => choose(option));
      popup.append(button);
    });
    return popup;
  }

  function decorateSingleSelect(select) {
    if (!select || select.multiple || select.dataset.unifiedFieldCombo === "1") return;
    cleanupLegacySingleSelect(select);
    select.dataset.unifiedFieldCombo = "1";
    const shell = document.createElement("span");
    shell.className = "xp-field-shell";
    select.parentNode.insertBefore(shell, select);
    shell.append(select);
    const input = document.createElement("input");
    input.type = "text";
    input.className = "xp-field-entry";
    input.autocomplete = "off";
    input.placeholder = "輸入或搜尋欄位 Type or search field";
    shell.insertBefore(input, select);

    const currentOption = () => legalFieldOptions(select).find(option => option.value === select.value) || null;
    const sync = () => {
      const options = legalFieldOptions(select);
      if (select.value && !options.some(option => option.value === select.value)) select.value = "";
      const option = currentOption();
      if (document.activeElement === input && !select.value && input.value.trim()) return;
      input.value = option?.label || "";
      input.classList.remove("ta-invalid");
    };
    const commit = option => {
      const changed = select.value !== option.value;
      select.value = option.value;
      input.value = option.label;
      input.classList.remove("ta-invalid");
      shell.querySelector(":scope > .xp-field-popup")?.remove();
      if (changed) select.dispatchEvent(new Event("change", { bubbles:true }));
    };
    const open = query => renderPopup(shell, legalFieldOptions(select), query, select.value ? [select.value] : [], commit);

    input.addEventListener("focus", () => {
      open("");
      queueMicrotask(() => input.select());
    });
    input.addEventListener("click", () => {
      const allSelected = input.selectionStart === 0 && input.selectionEnd === input.value.length;
      open(allSelected ? "" : input.value);
    });
    input.addEventListener("input", () => {
      const options = legalFieldOptions(select);
      const match = exactOption(options, input.value);
      if (match) {
        commit(match);
        return;
      }
      if (select.value) {
        select.value = "";
        select.dispatchEvent(new Event("change", { bubbles:true }));
      }
      input.classList.remove("ta-invalid");
      open(input.value);
    });
    input.addEventListener("keydown", event => {
      if (event.key === "Escape") shell.querySelector(":scope > .xp-field-popup")?.remove();
      if (event.key === "ArrowDown") {
        event.preventDefault();
        const popup = open(input.value);
        popup.querySelector(".xp-field-option")?.focus();
      }
    });
    input.addEventListener("blur", () => setTimeout(() => {
      if (!select.value && input.value.trim()) input.classList.add("ta-invalid");
      if (!shell.contains(document.activeElement)) shell.querySelector(":scope > .xp-field-popup")?.remove();
    }, 0));
    select.addEventListener("change", sync);
    new MutationObserver(sync).observe(select, { childList:true, subtree:true });
    sync();
  }

  function isMultiFieldInput(input) {
    return FIELD_INPUT_RULES.some(([selector, multi]) => multi && input.matches(selector));
  }

  function parseMulti(raw) {
    return String(raw || "").split(",").map(part => part.trim()).filter(Boolean);
  }

  function tailQuery(input, multi) {
    if (!multi) return String(input.value || "").trim();
    const tail = String(input.value || "").split(",").pop()?.trim() || "";
    return tail.replace(/^[+-]/, "");
  }

  function queryForOpen(input, multi, options) {
    const query = tailQuery(input, multi);
    return exactOption(options, query) ? "" : query;
  }

  function setFieldToken(input, option, multi) {
    if (!multi) {
      input.value = option.value;
    } else {
      const options = legalFieldOptions(input);
      const legal = new Set(options.map(item => item.value));
      const rawParts = String(input.value || "").split(");
      const tail = rawParts.pop()?.trim() || "";
      const prefix = (tail.match(/^[+-]/) || [""])[0];
      const plainTail = tail.replace(/^[+-]/, "");
      const parts = rawParts.map(part => part.trim()).filter(Boolean);
      if (tail && legal.has(plainTail)) parts.push(tail);
      const existing = new Set(parts.map(part => part.replace(/^[+-]/, "")));
      if (!existing.has(option.value)) parts.push(`${prefix}${option.value}`);
      input.value = parts.join(",");
    }
    input.classList.remove("ta-invalid");
    input.dispatchEvent(new Event("change", { bubbles:true }));
  }

  function validateDirectInput(input, multi) {
    const legal = new Set(legalFieldOptions(input).map(option => option.value));
    const tokens = multi
      ? parseMulti(input.value).map(value => value.replace(/^[+-]/, ""))
      : (input.value.trim() ? [input.value.trim()] : []);
    const valid = tokens.every(value => legal.has(value));
    input.classList.toggle("ta-invalid", Boolean(tokens.length && !valid));
    return valid;
  }

  function decorateFieldInput(input, multi = false) {
    if (!input || input.dataset.unifiedFieldInput === "1") return;
    cleanupLegacyFieldInput(input);
    input.dataset.unifiedFieldInput = "1";
    input.dataset.unifiedMulti = multi ? "1" : "0";
    const shell = document.createElement("span");
    shell.className = "xp-field-input-shell";
    input.parentNode.insertBefore(shell, input);
    shell.append(input);

    const open = query => {
      const selected = (multi ? parseMulti(input.value) : [input.value])
        .map(value => value.replace(/^[+-]/, ""))
        .filter(Boolean);
      return renderPopup(shell, legalFieldOptions(input), query, selected, option => {
        setFieldToken(input, option, multi);
        if (!multi) shell.querySelector(":scope > .xp-field-popup")?.remove();
        else open("");
      });
    };

    input.addEventListener("focus", () => {
      const options = legalFieldOptions(input);
      open(queryForOpen(input, multi, options));
    });
    input.addEventListener("click", () => {
      const options = legalFieldOptions(input);
      open(queryForOpen(input, multi, options));
    });
    input.addEventListener("input", () => {
      const options = legalFieldOptions(input);
      const query = tailQuery(input, multi);
      const match = exactOption(options, query);
      if (match) {
        if (!multi) {
          input.value = match.value;
          input.classList.remove("ta-invalid");
          input.dispatchEvent(new Event("change", { bubbles:true }));
          shell.querySelector(":scope > .xp-field-popup")?.remove();
          return;
        }
        const pieces = String(input.value || "").split(",");
        const tail = pieces.pop()?.trim() || "";
        const prefix = (tail.match(/^[+-]/) || [""])[0];
        pieces.push(`${prefix}${match.value}`);
        input.value = pieces.map(part => part.trim()).filter(Boolean).join(",");
      }
      input.classList.remove("ta-invalid");
      open(tailQuery(input, multi));
    });
    input.addEventListener("keydown", event => {
      if (event.key === "Escape") shell.querySelector(":scope > .xp-field-popup")?.remove();
      if (event.key === "ArrowDown") {
        event.preventDefault();
        const popup = open(tailQuery(input, multi));
        popup.querySelector(".xp-field-option")?.focus();
      }
    });
    input.addEventListener("blur", () => setTimeout(() => {
      validateDirectInput(input, multi);
      if (!shell.contains(document.activeElement)) shell.querySelector(":scope > .xp-field-popup")?.remove();
    }, 0));
    validateDirectInput(input, multi);
  }

  function domain(field) {
    return (VALUE_DOMAINS[String(field || "").trim()] || []).map(([value, label]) => ({ value:String(value), label }));
  }

  function valueField(input) {
    const row = input.closest(".condition-row,.s4-filter-row,.cc-filter-row,.s4-metric-row,.s4-stage");
    if (row?.classList.contains("condition-row")) return row.querySelector(".condition-field")?.value || "";
    if (row?.classList.contains("s4-filter-row")) return row.querySelector(".s4-filter-field")?.value || "";
    if (row?.classList.contains("cc-filter-row")) return row.querySelector(".cc-filter-field")?.value || "";
    if (row?.classList.contains("s4-metric-row")) return row.querySelector(".s4-metric-cond-field")?.value?.trim() || "";
    if (row?.classList.contains("s4-stage")) {
      const kind = row.querySelector(".s4-stage-kind")?.value;
      if (kind === "filter" && input.classList.contains("s4-value")) return row.querySelector(".s4-field")?.value?.trim() || "";
      if (kind === "event_pattern_cohorts" && input.classList.contains("ta-event-value")) return row.querySelector(".ta-event-field")?.value?.trim() || "";
    }
    if (input.id === "s4-boot-a" || input.id === "s4-boot-b") return document.getElementById("s4-boot-group")?.value?.trim() || "";
    if (input.id === "s4-boot-success") return document.getElementById("s4-boot-value")?.value?.trim() || "";
    return input.dataset.semanticField || "";
  }

  function isMultiValue(input) {
    const row = input.closest(".condition-row,.s4-filter-row,.cc-filter-row,.s4-metric-row,.s4-stage");
    const op = row?.querySelector(".condition-op,.s4-filter-op,.cc-filter-op,.s4-metric-cond-op,.s4-op,.ta-event-op")?.value || "eq";
    return op === "in" || op === "not_in" || input.dataset.semanticMulti === "1";
  }

  function ensureSemanticShell(input) {
    let shell = input.closest(".xp-semantic-shell");
    if (shell) return shell;
    shell = document.createElement("span");
    shell.className = "xp-semantic-shell";
    input.parentNode.insertBefore(shell, input);
    shell.append(input);
    return shell;
  }

  function refreshSemantic(input) {
    if (!input) return;
    const shell = ensureSemanticShell(input);
    const field = input.dataset.semanticField || valueField(input);
    const options = domain(field);
    shell.classList.toggle("xp-has-domain", options.length > 0 && !input.disabled);
    if (!options.length || input.disabled) shell.querySelector(":scope > .xp-field-popup")?.remove();
  }

  function decorateSemanticValue(input, forcedField = "", forcedMulti = false) {
    if (!input) return;
    if (forcedField) input.dataset.semanticField = forcedField;
    if (forcedMulti) input.dataset.semanticMulti = "1";
    if (input.dataset.unifiedSemantic === "1") {
      refreshSemantic(input);
      return;
    }
    input.dataset.unifiedSemantic = "1";
    const shell = ensureSemanticShell(input);
    const options = () => domain(forcedField || valueField(input));
    const multi = () => forcedMulti || isMultiValue(input);

    const open = query => {
      const opts = options();
      if (!opts.length || input.disabled) return null;
      const selected = (multi() ? parseMulti(input.value) : [input.value]).filter(Boolean);
      return renderPopup(shell, opts, query, selected, option => {
        if (!multi()) input.value = option.value;
        else {
          const values = parseMulti(input.value);
          const legal = new Set(opts.map(item => item.value));
          const tail = values.at(-1) || "";
          if (tail && !legal.has(tail)) values.pop();
          if (!values.includes(option.value)) values.push(option.value);
          input.value = values.join(",");
        }
        input.dispatchEvent(new Event("input", { bubbles:true }));
        input.dispatchEvent(new Event("change", { bubbles:true }));
        if (!multi()) shell.querySelector(":scope > .xp-field-popup")?.remove();
      });
    };

    input.addEventListener("focus", () => {
      const opts = options();
      open(queryForOpen(input, multi(), opts));
    });
    input.addEventListener("click", () => {
      const opts = options();
      open(queryForOpen(input, multi(), opts));
    });
    input.addEventListener("input", () => {
      const opts = options();
      if (!opts.length) return;
      const query = tailQuery(input, multi());
      const match = exactOption(opts, query);
      if (match && !multi()) {
        input.value = match.value;
        shell.querySelector(":scope > .xp-field-popup")?.remove();
        return;
      }
      open(query);
    });
    input.addEventListener("keydown", event => {
      if (event.key === "Escape") shell.querySelector(":scope > .xp-field-popup")?.remove();
      if (event.key === "ArrowDown") {
        event.preventDefault();
        open(tailQuery(input, multi()))?.querySelector(".xp-field-option")?.focus();
      }
    });
    input.addEventListener("blur", () => setTimeout(() => {
      if (!shell.contains(document.activeElement)) shell.querySelector(":scope > .xp-field-popup")?.remove();
    }, 0));
    refreshSemantic(input);
  }

  function semanticControls(root = document) {
    root.querySelectorAll?.(".condition-value,.s4-filter-value,.cc-filter-value,.s4-metric-cond-value,.s4-value,.ta-event-value,#s4-boot-a,#s4-boot-b,#s4-boot-success")
      .forEach(input => decorateSemanticValue(input));
    root.querySelectorAll?.("#role-exclude,.ta-role-exclude").forEach(input => decorateSemanticValue(input, "pitch_type", true));
    root.querySelectorAll?.("#cc-reference").forEach(input => decorateSemanticValue(input, "pitch_type", false));
    root.querySelectorAll?.(".ta-event-arrangements").forEach(input => decorateSemanticValue(input, "__arrangement", true));
  }

  function scan(root = document) {
    root.querySelectorAll?.(NATIVE_SINGLE_FIELD_SELECTORS).forEach(decorateSingleSelect);
    FIELD_INPUT_RULES.forEach(([selector, multi]) => root.querySelectorAll?.(selector).forEach(input => decorateFieldInput(input, multi)));
    semanticControls(root);
    root.querySelectorAll?.('input[list^="ta-fields-"],input[list^="ta-pipeline-"]').forEach(removeNativeDatalist);
    root.querySelectorAll?.('datalist[id^="ta-fields-"],datalist[id^="ta-pipeline-"]').forEach(node => node.remove());
  }

  function syncAll() {
    document.querySelectorAll(NATIVE_SINGLE_FIELD_SELECTORS).forEach(select => {
      const shell = select.closest(".xp-field-shell");
      const input = shell?.querySelector(":scope > .xp-field-entry");
      if (!input) return;
      const options = legalFieldOptions(select);
      const selected = options.find(option => option.value === select.value);
      if (select.value && !selected) {
        select.value = "";
        select.dispatchEvent(new Event("change", { bubbles:true }));
      }
      if (document.activeElement !== input) {
        input.value = selected?.label || "";
        input.classList.remove("ta-invalid");
      }
    });
    FIELD_INPUT_RULES.forEach(([selector, multi]) => document.querySelectorAll(selector).forEach(input => {
      if (input.dataset.unifiedFieldInput === "1") validateDirectInput(input, multi);
    }));
    semanticControls(document);
  }

  function refreshRelated(event) {
    const target = event.target;
    const row = target?.closest?.(".condition-row,.s4-filter-row,.cc-filter-row,.s4-metric-row,.s4-stage");
    row?.querySelectorAll?.(".condition-value,.s4-filter-value,.cc-filter-value,.s4-metric-cond-value,.s4-value,.ta-event-value")
      .forEach(refreshSemantic);
    if (target?.matches?.("#s4-boot-group,#s4-boot-value")) {
      ["#s4-boot-a", "#s4-boot-b", "#s4-boot-success"].forEach(selector => refreshSemantic(document.querySelector(selector)));
    }
  }

  function init() {
    injectStyles();
    scan(document);
    document.addEventListener("click", event => {
      if (!event.target.closest?.(".xp-field-shell,.xp-field-input-shell,.xp-semantic-shell")) closePopups();
    });
    document.addEventListener("change", event => {
      refreshRelated(event);
      setTimeout(syncAll, 0);
    });
    document.addEventListener("input", event => {
      refreshRelated(event);
      if (event.target.matches?.(".s4-stage-kind,.s4-metric-fn,.s4-function,.ta-role-kind,.ta-role-fn,#s4-boot-stat")) setTimeout(syncAll, 0);
    });
    document.addEventListener("treepolo:fields-updated", () => setTimeout(() => { scan(document); syncAll(); }, 0));
    document.addEventListener("treepolo:analysis-options-changed", () => setTimeout(() => { scan(document); syncAll(); }, 0));

    let queued = false;
    new MutationObserver(mutations => {
      if (queued || !mutations.some(mutation => mutation.addedNodes.length || mutation.removedNodes.length)) return;
      queued = true;
      setTimeout(() => {
        queued = false;
        scan(document);
        syncAll();
      }, 0);
    }).observe(document.body, { childList:true, subtree:true });
  }

  window.treepoloUnifiedFieldControlsApi = { scan, sync:syncAll, legalFieldOptions };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init, { once:true });
  else init();
})();