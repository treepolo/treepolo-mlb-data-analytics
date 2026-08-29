(() => {
  "use strict";

  if (window.treepoloPlayerNames) return;

  const PEOPLE_API = "https://statsapi.mlb.com/api/v1/people";
  const BATCH_SIZE = 100;
  const PLAYER_FIELDS = {
    pitcher: { key:"pitcher_name", label:"投手姓名 Pitcher Name", existing:["pitcher_name","player_name"] },
    batter: { key:"batter_name", label:"打者姓名 Batter Name", existing:["batter_name"] },
    on_1b: { key:"on_1b_name", label:"一壘跑者姓名 Runner on 1B Name", existing:["on_1b_name"] },
    on_2b: { key:"on_2b_name", label:"二壘跑者姓名 Runner on 2B Name", existing:["on_2b_name"] },
    on_3b: { key:"on_3b_name", label:"三壘跑者姓名 Runner on 3B Name", existing:["on_3b_name"] },
    fielder_2: { key:"fielder_2_name", label:"捕手姓名 Catcher Name", existing:["fielder_2_name"] },
    fielder_3: { key:"fielder_3_name", label:"一壘手姓名 First Baseman Name", existing:["fielder_3_name"] },
    fielder_4: { key:"fielder_4_name", label:"二壘手姓名 Second Baseman Name", existing:["fielder_4_name"] },
    fielder_5: { key:"fielder_5_name", label:"三壘手姓名 Third Baseman Name", existing:["fielder_5_name"] },
    fielder_6: { key:"fielder_6_name", label:"游擊手姓名 Shortstop Name", existing:["fielder_6_name"] },
    fielder_7: { key:"fielder_7_name", label:"左外野手姓名 Left Fielder Name", existing:["fielder_7_name"] },
    fielder_8: { key:"fielder_8_name", label:"中外野手姓名 Center Fielder Name", existing:["fielder_8_name"] },
    fielder_9: { key:"fielder_9_name", label:"右外野手姓名 Right Fielder Name", existing:["fielder_9_name"] },
  };

  const cache = new Map();
  const inFlight = new Map();

  function normalizeId(value) {
    const text = String(value ?? "").trim();
    if (!/^\d+$/.test(text)) return "";
    const number = Number(text);
    return Number.isSafeInteger(number) && number > 0 ? String(number) : "";
  }

  function chunks(values, size = BATCH_SIZE) {
    const out = [];
    for (let index = 0; index < values.length; index += size) out.push(values.slice(index, index + size));
    return out;
  }

  async function fetchNames(ids) {
    const query = new URLSearchParams({ personIds: ids.join(",") });
    const response = await fetch(`${PEOPLE_API}?${query}`, { cache:"force-cache" });
    if (!response.ok) throw new Error(`MLB player lookup failed: ${response.status}`);
    const body = await response.json();
    const found = new Set();
    (body.people || []).forEach(person => {
      const id = normalizeId(person?.id);
      const name = String(person?.fullName || "").trim();
      if (!id || !name) return;
      cache.set(id, name);
      found.add(id);
    });
    ids.forEach(id => { if (!found.has(id) && !cache.has(id)) cache.set(id, null); });
  }

  async function resolve(ids) {
    const wanted = Array.from(new Set(ids.map(normalizeId).filter(Boolean)));
    const waits = [];
    const fresh = [];
    wanted.forEach(id => {
      if (cache.has(id)) return;
      const pending = inFlight.get(id);
      if (pending) waits.push(pending);
      else fresh.push(id);
    });

    chunks(fresh).forEach(batch => {
      const request = fetchNames(batch)
        .catch(() => {})
        .finally(() => batch.forEach(id => inFlight.delete(id)));
      batch.forEach(id => inFlight.set(id, request));
      waits.push(request);
    });
    if (waits.length) await Promise.all(waits);
  }

  function rawHeaders(table) {
    return Array.from(table?.querySelectorAll("thead tr:first-child > th") || [])
      .filter(header => !header.dataset.playerNameFor);
  }

  function existingNameHeader(table, field) {
    return Array.from(table?.querySelectorAll("thead tr:first-child > th") || [])
      .find(header => header.dataset.playerNameFor === field) || null;
  }

  function existingNameCell(row, field) {
    return Array.from(row?.children || []).find(cell => cell.dataset.playerNameFor === field) || null;
  }

  function prepareTable(table) {
    if (!table) return [];
    const headers = rawHeaders(table);
    const rawKeys = new Set(headers.map(header => header.textContent.trim()));
    const matches = [];
    headers.forEach((header, rawIndex) => {
      const field = header.textContent.trim();
      const config = PLAYER_FIELDS[field];
      if (!config || config.existing.some(key => rawKeys.has(key))) return;
      matches.push({ field, config, header, rawIndex });
    });
    if (!matches.length) return [];

    const rows = Array.from(table.querySelectorAll("tbody > tr"));
    const bindings = [];

    matches.sort((a, b) => b.rawIndex - a.rawIndex).forEach(match => {
      if (!existingNameHeader(table, match.field)) {
        const th = document.createElement("th");
        th.dataset.playerNameFor = match.field;
        th.dataset.resultKey = match.config.key;
        th.textContent = match.config.label;
        match.header.insertAdjacentElement("beforebegin", th);
      }

      rows.forEach(row => {
        const rawCells = Array.from(row.children).filter(cell => !cell.dataset.playerNameFor);
        const idCell = rawCells[match.rawIndex];
        if (!idCell) return;
        const id = normalizeId(idCell.textContent);
        let nameCell = existingNameCell(row, match.field);
        if (!nameCell) {
          nameCell = document.createElement("td");
          nameCell.dataset.playerNameFor = match.field;
          nameCell.dataset.resultKey = match.config.key;
          idCell.insertAdjacentElement("beforebegin", nameCell);
        }
        nameCell.dataset.playerId = id;
        nameCell.textContent = id && cache.has(id) ? (cache.get(id) || "—") : (id ? "…" : "—");
        if (id) bindings.push({ cell:nameCell, id });
      });
    });

    return bindings;
  }

  async function enhanceTable(table) {
    const bindings = prepareTable(table);
    if (!bindings.length) return;
    await resolve(bindings.map(binding => binding.id));
    bindings.forEach(binding => {
      if (!binding.cell.isConnected || binding.cell.dataset.playerId !== binding.id) return;
      binding.cell.textContent = cache.get(binding.id) || "—";
    });
  }

  function enhanceRoot(root = document) {
    Array.from(root?.querySelectorAll?.("table.result-table") || []).forEach(table => {
      enhanceTable(table).catch(() => {});
    });
  }

  window.treepoloPlayerNames = {
    PLAYER_FIELDS,
    normalizeId,
    resolve,
    enhanceTable,
    enhanceRoot,
  };
})();
